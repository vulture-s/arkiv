"""Codex Round-2 audit J4: ARKIV_API SSRF guard in arkiv_resolve.py.

Plugin runs inside the operator's Resolve, but env override is the natural
attack vector — a poisoned ARKIV_API like http://169.254.169.254/ would have
the plugin hit cloud metadata on every search/import. Validator hard-fails
non-http(s) and link-local hosts at module load.

Subprocess to keep arkiv_resolve.py out of pytest's sys.modules cache."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "resolve_plugin"


def _import_plugin(env_overrides):
    env = os.environ.copy()
    env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", "import arkiv_resolve; print(arkiv_resolve.ARKIV_API)"],
        cwd=str(PLUGIN_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode, result.stdout, result.stderr


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.170.2/v2/credentials/",
        "http://0.0.0.0:8501",
        "file:///etc/passwd",
        "ftp://localhost/",
    ],
)
def test_resolve_plugin_rejects_bad_arkiv_api(bad_url):
    code, _, stderr = _import_plugin({"ARKIV_API": bad_url})
    assert code != 0, f"expected failure with {bad_url}"
    assert "ARKIV_API" in stderr


def test_resolve_plugin_accepts_localhost_and_tailscale():
    code, stdout, stderr = _import_plugin({"ARKIV_API": "http://localhost:8501"})
    assert code == 0, f"unexpected failure: {stderr}"
    assert "http://localhost:8501" in stdout

    code, stdout, stderr = _import_plugin({"ARKIV_API": "http://100.64.154.6:8501"})
    assert code == 0, f"unexpected failure: {stderr}"
    assert "100.64.154.6" in stdout
