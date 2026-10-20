"""Repair pass for audio rows that inherited cover-art video fields (#521).

The real schema (db.init_db via `tmp_db`) and real rows via db.upsert, so the
script is exercised against what a library actually holds — including the
neighbours it must leave alone: real video, a video carrying a cover, a JPEG
still whose `mjpeg` codec is correct.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

import fix_audio_cover_metadata as fix


@pytest.fixture
def lib(tmp_db, sample_record):
    db = importlib.import_module("db")
    rows = {
        # the bug: audio rows carrying the cover's "video" fields
        "song.mp3": dict(ext=".mp3", codec="mjpeg", fps=90000.0, width=300, height=300),
        "song.m4a": dict(ext=".m4a", codec="mjpeg", fps=90000.0, width=600, height=600),
        "SONG.FLAC": dict(ext=".FLAC", codec="mjpeg", fps=90000.0, width=500, height=500),
        # already correct audio: nothing to do
        "clean.wav": dict(ext=".wav", codec=None, fps=None, width=None, height=None),
        # neighbours that must not be touched
        "clip.mp4": dict(ext=".mp4", codec="h264", fps=25.0, width=1920, height=1080),
        "covered.mp4": dict(ext=".mp4", codec="mjpeg", fps=90000.0, width=300, height=300),
        "still.jpg": dict(ext=".jpg", codec="mjpeg", fps=None, width=4000, height=3000),
    }
    for name, f in rows.items():
        db.upsert(sample_record(path="/lib/" + name, filename=name, **f))
    return tmp_db


def _row(path, name):
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    try:
        r = c.execute("SELECT codec, fps, width, height FROM media WHERE filename=?",
                      (name,)).fetchone()
        return tuple(r)
    finally:
        c.close()


def test_dry_run_reports_and_writes_nothing(lib, capsys):
    assert fix.main(["--db", str(lib)]) == 0
    out = capsys.readouterr().out
    assert "audio rows carrying video fields: 3" in out
    assert "song.mp3" in out and "codec=mjpeg" in out and "fps=90000.0" in out
    assert "dry run — nothing written" in out
    assert _row(lib, "song.mp3") == ("mjpeg", 90000.0, 300, 300)
    assert not list(lib.parent.glob("*.pre-521-*.bak")), "a dry run must not even back up"


def test_apply_clears_audio_rows_only_and_backs_up_first(lib, capsys):
    assert fix.main(["--db", str(lib), "--apply"]) == 0
    out = capsys.readouterr().out

    for name in ("song.mp3", "song.m4a", "SONG.FLAC"):
        assert _row(lib, name) == (None, None, None, None), name
    assert _row(lib, "clip.mp4") == ("h264", 25.0, 1920, 1080)
    assert _row(lib, "covered.mp4") == ("mjpeg", 90000.0, 300, 300), \
        "a video row needs a re-probe, not a guess"
    assert _row(lib, "still.jpg") == ("mjpeg", None, 4000, 3000)
    assert "video rows that look like cover art" in out and "covered.mp4" in out

    baks = list(lib.parent.glob("test.db.pre-521-*.bak"))
    assert len(baks) == 1
    assert _row(baks[0], "song.mp3") == ("mjpeg", 90000.0, 300, 300), \
        "the backup must hold the values from before the write"
    assert "cleared 3 row(s); audio rows still carrying video fields: 0" in out


def test_apply_twice_is_a_no_op(lib, capsys):
    fix.main(["--db", str(lib), "--apply"])
    capsys.readouterr()
    assert fix.main(["--db", str(lib), "--apply"]) == 0
    assert "nothing to fix." in capsys.readouterr().out
    assert len(list(lib.parent.glob("*.pre-521-*.bak"))) == 1, "no backup when nothing is written"


def test_failed_backup_writes_nothing(lib, monkeypatch, capsys):
    def broken(path):
        raise OSError("disk full")
    monkeypatch.setattr(fix, "backup", broken)

    assert fix.main(["--db", str(lib), "--apply"]) == 1
    assert "backup FAILED, nothing written" in capsys.readouterr().out
    assert _row(lib, "song.mp3") == ("mjpeg", 90000.0, 300, 300)


def test_backup_verifies_row_count(lib, monkeypatch):
    """If the copy does not hold the same rows, `backup()` itself must refuse —
    and main() then writes nothing."""
    seen = iter([7, 6])  # source, then the (short) copy
    monkeypatch.setattr(fix, "_media_count", lambda conn: next(seen))
    with pytest.raises(RuntimeError, match="backup has 6 media rows, DB has 7"):
        fix.backup(lib)


def test_missing_db_is_an_error(tmp_path, capsys):
    assert fix.main(["--db", str(tmp_path / "nope.db")]) == 1
    assert "DB not found" in capsys.readouterr().out
