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


# ── B10c: ExifTool auto-detect fallback chain ────────────────────────────────

def test_detect_exiftool_env_var_wins(monkeypatch):
    """ARKIV_EXIFTOOL_PATH env > everything else (trusts user override blindly)"""
    sys.path.insert(0, str(ARKIV_ROOT))
    import config
    monkeypatch.setenv("ARKIV_EXIFTOOL_PATH", "/custom/path/exiftool")
    assert config._detect_exiftool() == "/custom/path/exiftool"


def test_detect_exiftool_falls_back_to_shutil_which(monkeypatch):
    """No env → shutil.which lookup"""
    sys.path.insert(0, str(ARKIV_ROOT))
    import config
    monkeypatch.delenv("ARKIV_EXIFTOOL_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/exiftool" if name == "exiftool" else None)
    assert config._detect_exiftool() == "/usr/local/bin/exiftool"


def test_detect_exiftool_falls_back_to_common_paths(tmp_path, monkeypatch):
    """No env + which miss → scan common install paths (Windows winget LOCALAPPDATA pattern)"""
    sys.path.insert(0, str(ARKIV_ROOT))
    import config
    fake_exif = tmp_path / "Programs" / "ExifTool" / "exiftool.exe"
    fake_exif.parent.mkdir(parents=True)
    fake_exif.write_text("fake")
    monkeypatch.delenv("ARKIV_EXIFTOOL_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert config._detect_exiftool() == str(fake_exif)


def test_detect_exiftool_all_miss_returns_literal(monkeypatch, tmp_path):
    """All fallbacks miss → returns 'exiftool' literal (subprocess fails loud later)"""
    sys.path.insert(0, str(ARKIV_ROOT))
    import config
    monkeypatch.delenv("ARKIV_EXIFTOOL_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    # Point all env-var-based candidates at empty tmp_path so none exist
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty1"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty2"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty_home"))
    # Hard-coded /usr/local/bin/exiftool etc. may exist on Mac CI — skip if so
    if any(Path(p).exists() for p in ["/usr/local/bin/exiftool", "/opt/homebrew/bin/exiftool",
                                       "/usr/bin/exiftool", "C:/Program Files/exiftool/exiftool.exe",
                                       "C:/ProgramData/chocolatey/bin/exiftool.exe",
                                       "C:/Strawberry/perl/bin/exiftool.bat"]):
        pytest.skip("hardcoded common path exists on host — can't isolate")
    assert config._detect_exiftool() == "exiftool"


def test_detect_exiftool_empty_env_falls_through(monkeypatch):
    """ARKIV_EXIFTOOL_PATH='' (empty string, e.g. user explicitly cleared) →
    treated same as unset, falls through to shutil.which (not returned as empty)."""
    sys.path.insert(0, str(ARKIV_ROOT))
    import config
    monkeypatch.setenv("ARKIV_EXIFTOOL_PATH", "")
    monkeypatch.setattr("shutil.which", lambda name: "/from/which/exiftool" if name == "exiftool" else None)
    assert config._detect_exiftool() == "/from/which/exiftool"


def test_detect_exiftool_user_local_bin(tmp_path, monkeypatch):
    """Linux pipx / user install: ~/.local/bin/exiftool detected via Path.home() candidate."""
    sys.path.insert(0, str(ARKIV_ROOT))
    import config
    fake_home = tmp_path / "fakehome"
    fake_exif = fake_home / ".local" / "bin" / "exiftool"
    fake_exif.parent.mkdir(parents=True)
    fake_exif.write_text("fake")
    monkeypatch.delenv("ARKIV_EXIFTOOL_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    # Skip if hardcoded paths exist on host (Mac/Linux CI)
    if any(Path(p).exists() for p in ["/usr/local/bin/exiftool", "/opt/homebrew/bin/exiftool",
                                       "/usr/bin/exiftool"]):
        pytest.skip("hardcoded common path exists on host — can't isolate")
    assert config._detect_exiftool() == str(fake_exif)
