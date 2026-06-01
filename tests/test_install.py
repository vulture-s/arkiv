"""Guards for the 'good install' contract — that a fresh install ships every
module server.py needs. A hand-maintained copy list in install.sh once went
stale and dropped auth/chat/admin/… → the install crashed on first run with
ModuleNotFoundError. These tests fail loudly if that can recur."""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_install_copies_python_modules_via_glob_not_a_stale_list():
    install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    # the local-copy path must glob *.py so it can't drift behind new modules
    assert 'cp "$SRC"/*.py' in install, "install.sh must copy all *.py via glob"


def test_server_imports_cleanly_from_repo_root(tmp_path):
    """The DEFINITIVE 'good install' check: actually import server in a fresh
    subprocess from the repo root. A renamed/deleted/uncopied first-party module
    surfaces here as a real ModuleNotFoundError — unlike a static AST scan, which
    can only see names that already resolve to a file."""
    env = dict(os.environ)
    env["ARKIV_DB_PATH"] = str(tmp_path / "import-probe.db")
    result = subprocess.run(
        [sys.executable, "-c", "import server"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, (
        "importing server failed (a fresh install would crash):\n" + result.stderr
    )
