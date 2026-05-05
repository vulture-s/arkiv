"""Codex Round-2 audit J3 + J5: env-driven path / URL hardening.

J3 hard-fails when ARKIV_*_DIR / ARKIV_*_PATH points at system roots — a
misconfigured operator (or compromised env) used to be able to clobber /etc.
J5 hard-fails when ARKIV_OLLAMA_URL is non-http(s) or points at link-local /
cloud-metadata IPs — closes the SSRF gadget where arkiv would proxy 169.254.x
queries on every embed call.

Tests run config import in a subprocess so a failed reload doesn't leave
sys.modules in a state that breaks later tests (importing modules hold a stale
reference to config when sys.modules is popped mid-suite)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ARKIV_ROOT = Path(__file__).resolve().parent.parent


def _check_config_loads(env_overrides):
    """Spawn a subprocess that imports config with given env. Returns (code, stderr)."""
    env = os.environ.copy()
    env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", "import config; print(config.PROXIES_DIR, config.OLLAMA_URL)"],
        cwd=str(ARKIV_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode, result.stderr


@pytest.mark.parametrize(
    "env_var,bad_path",
    [
        ("ARKIV_PROXIES_DIR", "/etc/arkiv-proxies"),
        ("ARKIV_THUMBNAILS_DIR", "/usr/local/share/arkiv"),
        ("ARKIV_DB_PATH", "/var/log/arkiv.db"),
        ("ARKIV_CHROMA_PATH", "/Library/arkiv-chroma"),
        ("ARKIV_PROJECT_ROOT", "/System/arkiv"),
    ],
)
def test_config_rejects_writable_path_under_system_root(tmp_path, env_var, bad_path):
    code, stderr = _check_config_loads({env_var: bad_path})
    assert code != 0, f"expected failure but config loaded with {env_var}={bad_path}"
    assert "ValueError" in stderr


def test_config_accepts_writable_path_under_user_dirs(tmp_path):
    code, stderr = _check_config_loads(
        {
            "ARKIV_PROXIES_DIR": str(tmp_path / "proxies"),
            "ARKIV_THUMBNAILS_DIR": str(tmp_path / "thumbs"),
            "ARKIV_DB_PATH": str(tmp_path / "media.db"),
            "ARKIV_CHROMA_PATH": str(tmp_path / "chroma"),
            "ARKIV_PROJECT_ROOT": str(tmp_path),
        }
    )
    assert code == 0, f"unexpected failure: {stderr}"


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://169.254.169.254/latest/meta-data/",   # AWS metadata
        "http://169.254.170.2/v2/credentials/",       # ECS metadata
        "http://0.0.0.0:11434",                       # any-interface
        "file:///etc/passwd",                         # non-http scheme
        "ftp://localhost:21/",                        # non-http scheme
    ],
)
def test_config_rejects_ollama_url_link_local_or_bad_scheme(bad_url):
    code, stderr = _check_config_loads({"ARKIV_OLLAMA_URL": bad_url})
    assert code != 0, f"expected failure but config loaded with {bad_url}"
    assert "ARKIV_OLLAMA_URL" in stderr


def test_config_accepts_localhost_and_tailscale_ollama():
    # localhost default (no env override)
    code, stderr = _check_config_loads({})
    assert code == 0, f"baseline import broken: {stderr}"
    # Tailscale CGNAT (Ollama on NAS)
    code, stderr = _check_config_loads({"ARKIV_OLLAMA_URL": "http://100.64.154.6:11434"})
    assert code == 0, f"unexpected failure: {stderr}"
