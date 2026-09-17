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
message and docstring — plus every library name on this user's disk (`恬馨`,
`明燒肉`, `寶礦力`).

`codec.py` already carried the fix and the reason ("Windows cp950 default can
choke on ffprobe output bytes (headless ingest crash), so decode explicitly") —
in one file, while twelve other call sites had the same bug. A fix that has to be
remembered at every new call site is not a fix, so this is a ratchet:

  * production is at zero and must stay there;
  * the remaining test-side callers are listed with their counts, and a count may
    only go DOWN. Adding one anywhere fails.

⚠️ This checks the *call*, not the runtime. A caller that passes `encoding=` from
a variable holding "cp950" would satisfy it. That is not the mistake anyone makes
here — the mistake is omitting the argument — and a test that claims more than it
checks is worse than one that says what it is.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "build", "dist", "__pycache__"}

# Callers still decoding with the platform default. Every one is test-side, and
# the list may only shrink — see the module docstring.
KNOWN_BARE = {
    "tests/test_auth.py": 1,
    "tests/test_chroma_staleness.py": 1,
    "tests/test_config.py": 1,
    "tests/test_grok_consult.py": 2,
    "tests/test_mcp_e2e.py": 3,
    "tests/test_mhl.py": 1,
    "tests/test_offload.py": 1,
    "tests/test_offload_mhl_phase.py": 1,
    "tests/test_repair_timecodes_script.py": 1,
    "tests/test_resolve_plugin.py": 1,
    "tests/test_still_raster_frames.py": 1,
}

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


def test_no_production_file_decodes_with_the_platform_default():
    """The half that reaches a user's library. Everything outside `tests/`."""
    offenders = {f: ls for f, ls in _scan().items() if not f.startswith("tests/")}
    assert not offenders, (
        "text=True without encoding= in production: {0}".format(offenders)
    )


def test_the_remaining_test_side_callers_only_ever_decrease():
    found = _scan()
    counts = {f: len(ls) for f, ls in found.items()}
    for path, allowed in KNOWN_BARE.items():
        assert counts.get(path, 0) <= allowed, (
            "{0} gained a bare text=True: {1} > {2} (lines {3})".format(
                path, counts.get(path, 0), allowed, found.get(path)
            )
        )
    new = sorted(set(counts) - set(KNOWN_BARE))
    assert not new, "new files decoding with the platform default: {0}".format(new)


@pytest.mark.parametrize("path", sorted(KNOWN_BARE))
def test_the_allowance_list_has_no_stale_entries(path):
    """A ratchet whose list outlives the problem stops being a ratchet.

    Once a file is fixed its entry must go, or the allowance quietly re-opens the
    door for the next bare call added to that same file.
    """
    counts = {f: len(ls) for f, ls in _scan().items()}
    assert counts.get(path, 0) == KNOWN_BARE[path], (
        "{0} now has {1} bare call(s), not {2} — update KNOWN_BARE".format(
            path, counts.get(path, 0), KNOWN_BARE[path]
        )
    )
