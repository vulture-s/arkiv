"""Guards for `scripts/grok-consult.sh` — the read-only Grok consultation wrapper.

Everything here runs without Grok credentials and without a network call: the real
`grok` binary is replaced by a stub on PATH, so each classification branch is
exercised deterministically. The live round-trip (a real authenticated turn) is
deliberately not covered — it needs `grok login` and costs money per call; it was
verified by hand against grok 0.2.101.

The branch that matters most is `test_answer_mentioning_authentication_is_not_...`:
the wrapper cannot use the exit code to detect a login failure (an unauthenticated
`grok` prints its banner to stdout and still exits 0), so an earlier version grepped
the whole output for auth strings — which also scanned Grok's *answer*, and threw
away a successful reply whenever the consultation was itself about authentication.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "grok-consult.sh"

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="bash wrapper; POSIX shells only"
)


def _run(tmp_path, args, stub_body=None, stdin=None):
    """Run the wrapper with a fake `grok` on PATH and a scrubbed HOME.

    HOME is redirected at a tmp dir so the wrapper's `$HOME/.grok/bin/grok`
    fallback can't reach a real installation on the developer's machine.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    if stub_body is not None:
        stub = bindir / "grok"
        stub.write_text(stub_body, encoding="utf-8")
        stub.chmod(0o755)

    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir(exist_ok=True)
    # Keep coreutils available, but put the stub first and drop any real grok.
    env["PATH"] = "{0}:/usr/bin:/bin:/usr/sbin:/sbin".format(bindir)

    return subprocess.run(
        ["bash", str(SCRIPT)] + args,
        capture_output=True, text=True, env=env,
        cwd=str(tmp_path), input=stdin, timeout=60,
    )


def _stub(stdout="", stderr="", code=0):
    return (
        "#!/usr/bin/env bash\n"
        + ("printf '%s' {0}\n".format(json.dumps(stdout)) if stdout else "")
        + ("printf '%s' {0} >&2\n".format(json.dumps(stderr)) if stderr else "")
        + "exit {0}\n".format(code)
    )


def test_script_is_executable_and_syntactically_valid():
    assert SCRIPT.exists(), "wrapper missing"
    assert os.access(SCRIPT, os.X_OK), "wrapper must stay chmod +x"
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_no_prompt_exits_2(tmp_path):
    r = _run(tmp_path, [], stub_body=_stub(stdout='{"text":"hi"}'), stdin="")
    assert r.returncode == 2
    assert "no prompt" in r.stderr.lower()


def test_unknown_mode_exits_2(tmp_path):
    r = _run(tmp_path, ["--mode", "sabotage", "hello"],
             stub_body=_stub(stdout='{"text":"hi"}'))
    assert r.returncode == 2
    assert "unknown mode" in r.stderr.lower()


def test_missing_grok_binary_exits_3(tmp_path):
    r = _run(tmp_path, ["hello"], stub_body=None)  # nothing named grok on PATH
    assert r.returncode == 3
    assert "not found" in r.stderr.lower()


def test_unauthenticated_exits_4_even_though_grok_exits_zero(tmp_path):
    """grok 0.2.101 prints the banner to STDOUT and exits 0 — the wrapper must
    still map it to 4 rather than pass the banner off as an answer."""
    r = _run(tmp_path, ["hello"],
             stub_body=_stub(stdout="You are not authenticated.\n", code=0))
    assert r.returncode == 4
    assert "not authenticated" in r.stderr.lower()


def test_answer_mentioning_authentication_is_not_mistaken_for_a_login_failure(tmp_path):
    """The regression this file exists for: a consultation *about* auth used to be
    classified as an auth failure and discarded, because the auth grep ran over
    Grok's own answer. A JSON envelope means a real reply — no auth check applies."""
    answer = "Your handler for 'not authenticated' should not call grok login here."
    r = _run(tmp_path, ["review my auth code"],
             stub_body=_stub(stdout=json.dumps({"text": answer}), code=0))
    assert r.returncode == 0, r.stderr
    assert answer in r.stdout


def test_json_envelope_text_is_unwrapped(tmp_path):
    r = _run(tmp_path, ["hello"],
             stub_body=_stub(stdout=json.dumps({"text": "42", "usage": {"x": 1}})))
    assert r.returncode == 0
    assert r.stdout.strip() == "42"
    assert "usage" not in r.stdout


def test_nonzero_exit_is_propagated_with_stderr(tmp_path):
    r = _run(tmp_path, ["hello"], stub_body=_stub(stderr="upstream exploded\n", code=7))
    assert r.returncode == 7
    assert "upstream exploded" in r.stderr


def test_stderr_scratch_file_is_not_a_fixed_shared_path():
    """A fixed /tmp name collides between concurrent runs and is a symlink target."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "/tmp/grok-consult.err" not in body
    assert "mktemp" in body


def test_wrapper_never_grants_write_permission():
    """The whole read-only guarantee is one flag deep — pin it."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "--permission-mode plan" in body
    assert "--allow-writes" not in body
    assert "--permission-mode auto" not in body


def test_stub_helper_leaves_no_temp_file_behind(tmp_path):
    """The mktemp scratch file is trapped on EXIT, not leaked per consultation."""
    tmpdir = tmp_path / "scratch"
    tmpdir.mkdir()
    env_before = set(tmpdir.iterdir())
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "grok"
    stub.write_text(_stub(stdout='{"text":"ok"}'), encoding="utf-8")
    stub.chmod(0o755)
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home2")
    (tmp_path / "home2").mkdir(exist_ok=True)
    env["PATH"] = "{0}:/usr/bin:/bin".format(bindir)
    env["TMPDIR"] = str(tmpdir)
    r = subprocess.run(["bash", str(SCRIPT), "hello"], capture_output=True,
                       text=True, env=env, cwd=str(tmp_path), timeout=60)
    assert r.returncode == 0, r.stderr
    assert set(tmpdir.iterdir()) == env_before, "scratch file leaked"


def test_bash_is_available_for_the_wrapper():
    assert shutil.which("bash"), "these tests assume bash on PATH"
