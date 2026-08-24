"""The repair script must not grow its own copy of the retranscribe logic.

The clips being repaired are someone's only copy of a hand-corrected transcript.
The API worker already knows not to blank a good transcript on a failed decode, to
archive the outgoing language, and to snapshot before writing. A repair script
that reimplements that is a script that drifts away from the thing it repairs —
and it drifts silently, because a repair run looks the same either way until you
need the rollback.
"""
from __future__ import annotations

from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "repair_timecodes.py"


def _src():
    return _SCRIPT.read_text(encoding="utf-8")


def test_it_calls_the_api_worker():
    assert "_run_retranscribe_all" in _src()


def test_it_does_not_write_transcripts_itself():
    """Any UPDATE of its own would be a second, unreviewed write path."""
    src = _src()
    for forbidden in ("UPDATE media", "upsert_transcript", "INSERT INTO transcripts"):
        assert forbidden not in src, "the script writes transcripts on its own"


def test_the_backup_is_not_optional():
    """`corrections._write_backup` is the only rollback. A `--no-backup` flag is a
    loaded gun pointed at a library that cannot be re-derived."""
    src = _src()
    assert "--no-backup" not in src
    assert "_run_retranscribe_all(targets, None, True)" in src


def test_it_refuses_to_run_without_being_told_which_library():
    """Defaulting to the ambient PROJECT_ROOT would let a mistyped command
    re-transcribe the wrong library."""
    import subprocess
    import sys

    env = {k: v for k, v in __import__("os").environ.items() if k != "ARKIV_PROJECT_ROOT"}
    r = subprocess.run([sys.executable, str(_SCRIPT), "--dry-run"],
                       capture_output=True, text=True, env=env, timeout=60)

    assert r.returncode == 2
    assert "ARKIV_PROJECT_ROOT" in r.stderr
