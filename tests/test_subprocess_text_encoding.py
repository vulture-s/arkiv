"""`subprocess(..., text=True)` must name its encoding, or a zh-TW box decodes cp950.

`text=True` without `encoding=` decodes the child's bytes with
`locale.getpreferredencoding()`. On a Traditional Chinese Windows that is
**cp950**, while everything this repo shells out to — git, ffmpeg, ffprobe,
Python — emits UTF-8. Measured on 2026-09-17:

    locale.getpreferredencoding()  = cp950
    git show HEAD:mhl.py, text=True  → stdout is None, returncode 0
    ...with encoding="utf-8"         → str, 26514 chars
    first non-ASCII byte, offset 8441 = b'\\xe2\\x80\\x94'  = an em dash

🔴 **The failure mode is the one this codebase keeps writing down.** The decode
error is raised inside subprocess's reader thread, surfaces as a warning nobody
reads, and `returncode` stays **0** — so `check=True` passes, `stdout` comes back
`None`, and the crash lands somewhere else entirely with no mention of encoding.
`routers/ingest.py` used to swallow it as "a failure can be transient (NAS
asleep)": ffprobe echoes the file path on stderr, the path has Chinese in it, and
the clip's duration silently became `None`.

And the character that breaks it is the em dash this repo puts in every commit
message and docstring — plus any CJK in a library or clip name, which is the
common case for this tool's users (`專案甲`, `素材庫`, `外景`).

`codec.py` already carried the fix and the reason ("Windows cp950 default can
choke on ffprobe output bytes (headless ingest crash), so decode explicitly") —
in one file, while twenty-five other call sites had the same bug. A fix that has
to be remembered at every new call site is not a fix, so this is a flat
prohibition: **no spawn anywhere in the tree may decode without naming its
encoding.** It landed as a ratchet with production at zero and fourteen test-side
callers still listed; that list is empty and gone, and the assertion below is the
whole tree.

⚠️ This checks the *call*, not the runtime. A caller that passes `encoding=` from
a variable holding "cp950" would satisfy it. That is not the mistake anyone makes
here — the mistake is omitting the argument — and a test that claims more than it
checks is worse than one that says what it is.
"""
from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "build", "dist", "__pycache__"}

_SPAWNERS = {"run", "Popen", "check_output", "check_call", "call"}


def _is_true(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _bare_calls(tree: ast.AST):
    """Yield line numbers of spawns that decode to str without naming an encoding."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "attr", None) or getattr(fn, "id", None)
        if name not in _SPAWNERS:
            continue
        # `subprocess.run(...)` or a bare `run(...)` from `from subprocess import run`.
        # Anything qualified by a different module is somebody else's `run`.
        owner = getattr(getattr(fn, "value", None), "id", None)
        if name in {"run", "Popen"} and owner not in {"subprocess", None}:
            continue
        kw = {k.arg: k for k in node.keywords if k.arg}
        decodes = _is_true(kw.get("text").value) if "text" in kw else False
        if not decodes and "universal_newlines" in kw:
            decodes = _is_true(kw["universal_newlines"].value)
        if decodes and "encoding" not in kw:
            yield node.lineno


def _scan() -> dict:
    found: dict = {}
    for path in sorted(_REPO.rglob("*.py")):
        if _SKIP_DIRS & set(path.relative_to(_REPO).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        lines = list(_bare_calls(tree))
        if lines:
            found[path.relative_to(_REPO).as_posix()] = lines
    return found


def test_the_scanner_finds_the_thing_it_is_looking_for():
    """A ratchet that silently matches nothing holds nothing.

    Without this, a scanner broken by (say) an ast change would report a clean
    tree forever, and the guard would read as passing while checking nothing —
    the failure shape this repo has been bitten by more than once.
    """
    tree = ast.parse(
        "import subprocess\n"
        "subprocess.run(['git'], text=True)\n"                        # bare
        "subprocess.run(['git'], text=True, encoding='utf-8')\n"      # named
        "subprocess.Popen(['git'], universal_newlines=True)\n"        # bare, old spelling
        "subprocess.run(['git'], capture_output=True)\n"              # bytes, fine
        "other.run(['git'], text=True)\n"                             # not subprocess
    )
    assert list(_bare_calls(tree)) == [2, 4]


def test_no_file_anywhere_decodes_with_the_platform_default():
    """Whole tree, not just production — the allowance list is empty now.

    It was `production only` for one commit, while fourteen test-side callers were
    still bare. Keeping that narrower assertion afterwards would leave a guard
    that reads as total and is not.
    """
    offenders = _scan()
    assert not offenders, (
        "text=True without encoding=: {0}".format(offenders)
    )


# ── naming the encoding is only half the contract ────────────────────────────
#
# A Python child writes its stdio in `locale.getpreferredencoding()` unless told
# otherwise — cp950 on this machine. So `encoding="utf-8"` on the PARENT alone
# turns a call that worked into one that does not, and it fails in the direction
# that hides: `returncode` is 0 and `stdout` is None.
#
# That is not hypothetical. Adding `encoding="utf-8"` to the test-side callers
# broke `test_config.py` and `test_resolve_plugin.py` exactly this way, and the
# full-suite count went 22 red to 28 before these two tests existed.

UTF8_CHILD = dict(os.environ, PYTHONIOENCODING="utf-8")
SAMPLE = "\u6058\u99a8 \u2014 \u660e\u71d2\u8089"  # library names + the em dash


def _spawn(code, env):
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
    )


def test_a_python_child_round_trips_non_ascii_when_both_ends_agree():
    """The convention this repo uses: child forced to utf-8, parent decodes utf-8."""
    r = _spawn("import sys; sys.stdout.write({0!r})".format(SAMPLE), UTF8_CHILD)
    assert r.returncode == 0, r.stderr
    assert r.stdout == SAMPLE, repr(r.stdout)


def test_a_mismatch_loses_the_output_without_failing():
    """The shape that makes this class invisible, pinned deterministically.

    The child is forced to cp1252 — a codec Python ships everywhere, so this does
    not depend on the machine's locale the way the real bug did. It can encode the
    em dash (0x97), and 0x97 on its own is not valid UTF-8, so the parent's decode
    cannot succeed.

    Python then fails two different ways by platform: Windows decodes in
    subprocess's reader thread, which dies and leaves `stdout` None with
    `returncode` 0; POSIX decodes in `communicate()` and raises. Either is
    accepted here — what is asserted is that **you never get the text back**, and
    on one of those paths nothing looks wrong at all.
    """
    mismatched = dict(os.environ, PYTHONIOENCODING="cp1252")
    try:
        r = _spawn("import sys; sys.stdout.write('\u2014')", mismatched)
    except UnicodeDecodeError:
        return  # POSIX path: loud, and that is fine
    assert r.stdout != "\u2014", (
        "cp1252 output decoded as utf-8 — the premise of this test no longer holds"
    )
    assert r.returncode == 0 and r.stdout is None, (
        "expected the silent shape (rc 0, stdout None), got rc={0} stdout={1!r}".format(
            r.returncode, r.stdout
        )
    )
