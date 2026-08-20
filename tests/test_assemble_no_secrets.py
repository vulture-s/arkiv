"""The assemble scripts must never copy a dotenv file into a shipped bundle.

Both `src-tauri/assemble-backend.sh` (macOS) and
`src-tauri/assemble-backend-win.ps1` (Windows) build the bundle by copying the
whole repo minus a *denylist* of heavy or irrelevant directories. The comment in
the shell script says the strategy out loud: "Copy generously (repo minus the
heavy/irrelevant dirs) so no runtime import is missed."

That trade is fine for source files and fatal for secrets. Until 2026-08-20
neither denylist mentioned `.env`, while `install.sh` does `cp .env.example .env`
and `.env.example` instructs the reader to fill in `ARKIV_TOKEN_HMAC_KEY`,
`ARKIV_ADMIN_BOOTSTRAP_TOKEN` and `ARKIV_PG_DSN` — the last of which carries a
database password. A release built on a real workstation (the only kind of
machine that has a filled-in `.env`) would therefore ship the developer's
credentials inside the `.dmg`/`.exe`, readable by anyone who downloads it. A CI
runner checks out clean and never has a `.env`, so CI could not surface this.

`.dockerignore` already excluded `.env`; the Tauri path had simply never had the
same rule applied. This pins both halves of the fix:

  1. the copy step excludes `.env*`, and
  2. a post-copy guard fails the build if one is present anyway.

(2) is not redundant with (1). Neither copy runs with `--delete`/`/MIR`, so a
`.env` deposited by an older build of these scripts still sits in
`src-tauri/backend/src/` and would ship regardless of the new exclude. A
denylist stops the patterns someone remembered; the guard stops the class.

If you add a new dotenv variant or rename these scripts, update the constants
below rather than deleting the test — the invariant is "no dotenv reaches a
bundle", not "these exact strings appear".
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SH = ROOT / "src-tauri" / "assemble-backend.sh"
PS1 = ROOT / "src-tauri" / "assemble-backend-win.ps1"

# The glob every layer agrees on. Deliberately covers `.env.example` too: nothing
# at runtime reads it (config._load_env only looks for `.env`), so shipping it
# would only widen the surface the guard has to police.
DOTENV_GLOB = ".env*"


@pytest.mark.parametrize("script", [SH, PS1], ids=["macos-sh", "windows-ps1"])
def test_assemble_script_exists(script: Path) -> None:
    assert script.is_file(), (
        f"{script.relative_to(ROOT)} is missing. If the assemble step was renamed, "
        "point this test at the new file rather than dropping the check."
    )


def test_macos_script_excludes_dotenv() -> None:
    body = SH.read_text(encoding="utf-8")
    assert f"--exclude '{DOTENV_GLOB}'" in body, (
        "assemble-backend.sh no longer excludes .env* from the source rsync. "
        "That copy is a denylist over the whole repo, so removing this line means "
        "a workstation build ships the developer's .env inside the .dmg."
    )


def test_windows_script_excludes_dotenv() -> None:
    body = PS1.read_text(encoding="utf-8")
    # robocopy /XF takes exact names or wildcards; `.env` alone would not cover
    # `.env.local`, so both forms have to be present.
    for pattern in ("'.env'", "'.env.*'"):
        assert pattern in body, (
            f"assemble-backend-win.ps1 no longer passes {pattern} to robocopy /XF. "
            "That copy is a denylist over the whole repo, so removing this means a "
            "workstation build ships the developer's .env inside the .exe/.msi."
        )


@pytest.mark.parametrize("script", [SH, PS1], ids=["macos-sh", "windows-ps1"])
def test_assemble_script_has_post_copy_guard(script: Path) -> None:
    """The exclude alone cannot clean up after an older build. The guard can."""
    body = script.read_text(encoding="utf-8")
    assert DOTENV_GLOB in body and "refusing to ship" in body, (
        f"{script.relative_to(ROOT)} lost its post-copy dotenv guard. The copy step "
        "runs without --delete/--mir, so a .env left behind by a previous build is "
        "still in src-tauri/backend/src/ and ships even when the exclude is present. "
        "The guard is what makes that a build failure instead of a silent leak."
    )


def test_guard_glob_actually_matches_a_planted_dotenv() -> None:
    """Assert the pattern works, not just that it is spelled somewhere.

    A guard whose glob quietly stops matching is worse than no guard: the build
    goes green and the leak returns. This plants the files the guard exists to
    catch and one it must ignore.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        (src / "routers").mkdir(parents=True)
        (src / "config.py").write_text("VERSION = '0'\n", encoding="utf-8")
        (src / "routers" / "admin.py").write_text("x = 1\n", encoding="utf-8")

        matched = sorted(p.name for p in src.rglob(DOTENV_GLOB) if p.is_file())
        assert matched == [], f"clean tree should not trip the guard, got {matched}"

        (src / ".env").write_text("ARKIV_TOKEN_HMAC_KEY=nope\n", encoding="utf-8")
        (src / ".env.local").write_text("ARKIV_PG_DSN=nope\n", encoding="utf-8")
        (src / ".env.example").write_text("# template\n", encoding="utf-8")

        matched = sorted(p.name for p in src.rglob(DOTENV_GLOB) if p.is_file())
        assert matched == [".env", ".env.example", ".env.local"], (
            f"guard glob {DOTENV_GLOB!r} missed a dotenv variant: {matched}"
        )


def _bash_available() -> bool:
    try:
        return subprocess.run(["bash", "-c", "true"], capture_output=True).returncode == 0
    except (OSError, FileNotFoundError):  # no bash on PATH at all
        return False


@pytest.mark.skipif(not _bash_available(), reason="bash unavailable")
def test_macos_script_is_valid_bash() -> None:
    """A guard that never runs because the script is unparseable protects nothing.

    Two Windows-specific details, both load-bearing:

    * The script is piped in on stdin rather than passed as a path argument. The
      only `bash` on PATH under Windows is Git Bash, whose MSYS layer rewrites
      Windows-style path arguments and turns `C:\\Users\\...` into an unopenable
      `C:Usersuser...` (exit 127, "No such file or directory").
    * It is piped as **bytes**, not text. `text=True` runs stdin through a
      TextIOWrapper, which on Windows translates every `\\n` into `\\r\\n`; bash
      then reports a bogus `syntax error: unexpected end of file` on a file that
      is perfectly valid. The bytes path reproduces the file exactly.
    """
    proc = subprocess.run(["bash", "-n"], input=SH.read_bytes(), capture_output=True)
    assert proc.returncode == 0, f"bash -n failed:\n{proc.stderr.decode(errors='replace')}"
