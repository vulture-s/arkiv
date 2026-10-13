#!/usr/bin/env python3
"""Ask whether THIS machine can actually read and decode THIS library's media.

    python scripts/probe_media.py                     # the configured library
    python scripts/probe_media.py --max-files 24      # look harder
    python scripts/probe_media.py --json

Exits 1 when a whole codec came back unusable, so it can gate a batch. See
`mediaprobe.py` for why presence checks miss this class entirely.

**Run it where the work will run.** Probing from another machine, or with another
binary, answers a different question — the nineteen clips that prompted this were
checked three ways and gave three different answers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import db  # noqa: E402
import mediaprobe  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-bucket", type=int, default=mediaprobe.DEFAULT_PER_BUCKET,
                    help="files sampled per (extension, folder) — folders are cameras")
    ap.add_argument("--max-files", type=int, default=mediaprobe.DEFAULT_MAX_FILES)
    ap.add_argument("--seconds", type=int, default=mediaprobe.DEFAULT_SECONDS,
                    help="how much audio to decode per file; proving a decoder "
                         "works does not need the whole clip")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    # The database is the first gate, and it is not a formality: pointed at a
    # library on an SMB mount from a macOS process without Full Disk Access, the
    # open fails exactly like the media reads do. The first version of this script
    # let that surface as a `sqlite3.OperationalError` traceback — a probe whose
    # whole purpose is to explain an environment, failing to explain the very
    # environment it was written for.
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT path FROM media WHERE has_audio = 1 AND path IS NOT NULL "
                "AND TRIM(path) <> ''").fetchall()
    except Exception as exc:
        print("BLOCKING — cannot open this library's database:")
        print("  {0}".format(db.get_db_path()))
        print("  {0}: {1}".format(type(exc).__name__, exc))
        print("  Nothing else can be checked until this reads. On macOS over SMB "
              "this is usually Full Disk Access for whatever is running arkiv.")
        return 1
    paths = [r["path"] for r in rows]
    if not paths:
        print("no media with audio in {0} — nothing to probe".format(db.get_db_path()))
        return 0

    result = mediaprobe.probe(
        paths, resolve=db.resolve_path, per_bucket=args.per_bucket,
        max_files=args.max_files, seconds=args.seconds)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("library : {0}".format(config.PROJECT_ROOT))
        print("ffmpeg  : {0}".format(config.FFMPEG_PATH))
        print(mediaprobe.format_report(result))
    return 1 if mediaprobe.blocking(result) else 0


if __name__ == "__main__":
    raise SystemExit(main())
