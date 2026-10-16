#!/usr/bin/env python3
"""Clear the video fields that audio rows inherited from their cover art (#521).

Before #521 was fixed, an MP3 / M4A / FLAC with embedded cover art was ingested
as if the cover were its picture track: ffprobe reports the artwork as a video
stream (`codec_name=mjpeg`, `r_frame_rate=90000/1`, the cover's size), and
ingest took "the first video stream". So the library holds audio rows like

    codec=mjpeg  fps=90000.0  width=300  height=300

Fixing ingest stops new rows getting these values; it does nothing for rows
already in the DB. This is the pass for those.

What it changes: for rows whose extension is an audio extension
(`mediatypes.AUDIO_EXT`), set `codec`, `fps`, `width`, `height` to NULL — the
values a correct ingest writes for audio. An audio file has no picture track,
so there is nothing to re-derive and no need to touch the file: the repair is
decided from the row alone, and it works with the media volume unplugged.

What it does NOT change: every other column; and video-extension rows. A video
container can carry cover art too, but there the right value is the real video
stream's, which only a re-probe can give. Those rows are COUNTED (codec=mjpeg
and fps≥1000, the cover-art signature) and listed so they can be re-ingested
with `ingest.py --refresh`, never guessed at.

    python fix_audio_cover_metadata.py                   # dry run (default)
    python fix_audio_cover_metadata.py --apply           # back up, then write
    python fix_audio_cover_metadata.py --db /x/media.db  # another library

`--apply` copies the DB first (SQLite online backup, safe with the server
running and with WAL) to `<db>.pre-521-<timestamp>.bak` next to it, checks the
copy has the same number of media rows, and refuses to write if that fails.

Exit status: 0 on success, including "nothing to fix"; 1 when the DB cannot be
opened or the backup fails (nothing is written in that case).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import mediatypes

FIELDS = ("codec", "fps", "width", "height")
SAMPLE = 10
# The cover-art signature on a video-extension row: an mjpeg "stream" at the
# 90000 tick rate. Real MJPEG footage runs at camera frame rates (≤ 240).
_COVER_FPS_FLOOR = 1000.0


def _ext_of(row) -> str:
    ext = (row["ext"] or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    return ext or Path(row["path"] or "").suffix.lower()


def find_audio_rows(conn):
    """Audio-extension rows with any of FIELDS set. Filtered in Python on the
    normalised extension (with `path` as the fallback), so a row stored with an
    upper-case or dot-less `ext` is not missed."""
    rows = conn.execute(
        "SELECT id, path, filename, ext, codec, fps, width, height FROM media "
        "WHERE codec IS NOT NULL OR fps IS NOT NULL "
        "   OR width IS NOT NULL OR height IS NOT NULL ORDER BY id").fetchall()
    return [r for r in rows if _ext_of(r) in mediatypes.AUDIO_EXT]


def find_video_rows_with_cover_signature(conn):
    rows = conn.execute(
        "SELECT id, path, filename, ext, codec, fps, width, height FROM media "
        "WHERE LOWER(codec) = 'mjpeg' AND fps >= ? ORDER BY id",
        (_COVER_FPS_FLOOR,)).fetchall()
    return [r for r in rows if _ext_of(r) in mediatypes.VIDEO_EXT]


def _media_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM media").fetchone()[0]


def backup(db_path: Path) -> Path:
    """Online backup next to the DB; verifies the copy before returning it."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = db_path.with_name("{0}.pre-521-{1}.bak".format(db_path.name, stamp))
    src = sqlite3.connect(str(db_path), timeout=30)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
            want, got = _media_count(src), _media_count(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if want != got:
        raise RuntimeError("backup has {0} media rows, DB has {1}".format(got, want))
    return dest


def _fmt(row) -> str:
    size = ("{0}x{1}".format(row["width"], row["height"])
            if row["width"] is not None or row["height"] is not None else "-")
    return "  [{0}] {1}  codec={2}  fps={3}  size={4}".format(
        row["id"], row["filename"] or Path(row["path"] or "").name,
        row["codec"], row["fps"], size)


def _default_db_path() -> Path:
    import db  # imported lazily: only to resolve the configured path
    return Path(db.get_db_path())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="back up the DB, then write (default: dry run, change nothing)")
    ap.add_argument("--db", default="",
                    help="SQLite DB to repair (default: the configured library DB)")
    args = ap.parse_args(argv)

    db_path = Path(args.db).expanduser() if args.db else _default_db_path()
    if not db_path.is_file():
        print("DB not found: {0}".format(db_path))
        return 1
    print("DB: {0}".format(db_path))

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        try:
            audio = find_audio_rows(conn)
            video = find_video_rows_with_cover_signature(conn)
        except sqlite3.Error as e:
            print("cannot read media table: {0}".format(e))
            return 1

        by_ext = {}
        for r in audio:
            by_ext[_ext_of(r)] = by_ext.get(_ext_of(r), 0) + 1
        print("audio rows carrying video fields: {0}{1}".format(
            len(audio),
            "  (" + ", ".join("{0}={1}".format(k, v) for k, v in sorted(by_ext.items())) + ")"
            if by_ext else ""))
        for r in audio[:SAMPLE]:
            print(_fmt(r))
        if len(audio) > SAMPLE:
            print("  … and {0} more".format(len(audio) - SAMPLE))

        if video:
            print("video rows that look like cover art (NOT modified — re-ingest "
                  "with `ingest.py --refresh` to re-probe): {0}".format(len(video)))
            for r in video[:SAMPLE]:
                print(_fmt(r))

        if not audio:
            print("nothing to fix.")
            return 0
        if not args.apply:
            print("dry run — nothing written. Re-run with --apply to clear "
                  "{0} on these {1} row(s).".format("/".join(FIELDS), len(audio)))
            return 0

        try:
            bak = backup(db_path)
        except (sqlite3.Error, OSError, RuntimeError) as e:
            print("backup FAILED, nothing written: {0}".format(e))
            return 1
        print("backup: {0}".format(bak))

        ids = [r["id"] for r in audio]
        with conn:  # one transaction: all rows or none
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                conn.execute(
                    "UPDATE media SET codec=NULL, fps=NULL, width=NULL, height=NULL "
                    "WHERE id IN ({0})".format(",".join("?" * len(chunk))), chunk)
        left = len(find_audio_rows(conn))
        print("cleared {0} row(s); audio rows still carrying video fields: {1}".format(
            len(ids), left))
        return 0 if left == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
