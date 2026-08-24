#!/usr/bin/env python3
"""Re-transcribe a library whose timecodes were written in gapless-speech time.

Every clip transcribed before PR #350 carries timestamps relative to the
VAD-trimmed audio, not the media. The error is the total silence removed before
that line, so it grows along the clip — a caption can be seconds early by the end.

**There is no offset-only backfill.** `_vad_filter` consumed its `stamps` and
returned a path; the trimmed wav was unlinked moments later. The mapping was never
stored anywhere, so the only repair is to transcribe the clip again.

This calls the SAME worker the API uses rather than reimplementing it — that
worker already carries the H1 guard (never blank a good transcript on a failed
decode), the outgoing-language archive, and the backup snapshot. A repair script
with its own copy of that logic is a repair script that drifts from the thing it
is repairing.

    ARKIV_PROJECT_ROOT=<library> python scripts/repair_timecodes.py --dry-run
    ARKIV_PROJECT_ROOT=<library> python scripts/repair_timecodes.py

`backup=True` is not optional and is not exposed as a flag: the write overwrites
the archived `transcripts` row for that language, and `corrections._write_backup`
is the only rollback path.

Expect polish, not whisper, to be the wall clock. Whisper decodes several times
faster than realtime; LLM polish runs at 3-4 characters/second. A few hours of
audio is an overnight job.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report the work and stop")
    ap.add_argument("--limit", type=int, default=None,
                    help="only the first N clips (a taste before the overnight run)")
    args = ap.parse_args(argv)

    if not os.getenv("ARKIV_PROJECT_ROOT"):
        print("set ARKIV_PROJECT_ROOT to the library you mean to repair", file=sys.stderr)
        return 2

    import config
    import db

    print("project root:", config.PROJECT_ROOT)
    print("db:", config.DB_PATH)
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, path, duration_s, "
            "       CASE WHEN transcript IS NOT NULL AND transcript != '' "
            "            THEN 1 ELSE 0 END AS has_text "
            "FROM media WHERE has_audio=1 ORDER BY id"
        ).fetchall()
    # Every audio clip is a target, not only the ones with text: a clip that came
    # back empty may have been a failed decode rather than a silent clip, and the
    # H1 guard means a good transcript can never be replaced by an empty one.
    targets = [(r["id"], r["path"]) for r in rows]
    if args.limit:
        targets = targets[: args.limit]
    print("audio clips: {0} (with transcript: {1})  audio: {2:.2f} h".format(
        len(rows), sum(r["has_text"] for r in rows),
        sum((r["duration_s"] or 0) for r in rows) / 3600))
    print("targets this run:", len(targets))
    if args.dry_run:
        return 0

    import routers.retranscribe as rt
    start = time.time()
    rt._run_retranscribe_all(targets, None, True)
    progress = dict(rt._retranscribe_guard.progress)
    print("done={done} failed={failed} backup={backup}".format(**progress))
    print("elapsed: {0:.1f} min".format((time.time() - start) / 60))
    # A failure count above zero is worth acting on: it means a clip was skipped
    # (file unreachable, decode error), not that its transcript was replaced badly.
    return 0


if __name__ == "__main__":
    sys.exit(main())
