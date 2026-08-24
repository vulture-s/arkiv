#!/usr/bin/env python3
"""Re-read EXIF for rows whose camera metadata was never captured.

`exiftool_extract` returns `{}` when the binary is missing — a whole ingest run
then writes `creation_date`, `camera_make`, `camera_model`, `lens_model` and the
rest as NULL, with one banner line as the only signal. Installing exiftool
afterwards fixes nothing: `db._backfill_shot_date` derives `shot_date` FROM
`creation_date`, so a NULL stays NULL, and nothing anywhere goes back to the file
to read the EXIF a second time.

Visible cost: those clips sit in the `unknown` bucket of the shoot-date facet
forever, and the camera columns the DIT views depend on are empty.

This is the pass that goes back. It reads the file again, writes only fields that
are currently NULL, and never overwrites something already there — a hand-edited
value must survive a repair run.

    python backfill_creation_date.py --dry-run     # what would change
    python backfill_creation_date.py               # do it
    python backfill_creation_date.py --limit 20    # a taste first

Exit status is 0 even when nothing needed doing; a repair that finds nothing to
repair is a success, not an error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import db

# Only these are backfilled. Everything here comes from the file itself and is
# therefore re-derivable; anything a human can edit (tags, ratings, in/out
# points, camera_id) is deliberately absent.
_EXIF_FIELDS = (
    "creation_date",
    "camera_make",
    "camera_model",
    "lens_model",
    "gps_lat",
    "gps_lon",
    "color_space",
    "iso",
    "shutter_speed",
    "aperture",
    "focal_length",
    "reel_name",
    "white_balance",
)


def rows_needing_backfill(conn, limit=None):
    """Rows with no `creation_date`. That single column is the marker: it is the
    one field essentially every camera writes, so its absence means the read
    failed rather than the camera being quiet."""
    sql = ("SELECT id, path, filename FROM media "
           "WHERE creation_date IS NULL OR creation_date = '' ORDER BY id")
    if limit:
        sql += " LIMIT {0}".format(int(limit))
    return conn.execute(sql).fetchall()


def backfill_row(conn, row, dry_run=False):
    """Returns (status, fields_written). status ∈ missing / no_exif / ok."""
    import ingest

    resolved = db.resolve_path(row["path"])
    if not Path(resolved).exists():
        # The NAS is unplugged, or the file moved. Not an error worth failing on:
        # run it again when the volume is back.
        return "missing", {}

    meta = ingest.exiftool_extract(resolved)
    if not meta:
        return "no_exif", {}

    current = conn.execute(
        "SELECT {0} FROM media WHERE id = ?".format(", ".join(_EXIF_FIELDS)),
        (row["id"],),
    ).fetchone()

    writes = {}
    for field in _EXIF_FIELDS:
        value = meta.get(field)
        if value in (None, ""):
            continue
        if current[field] not in (None, ""):
            continue  # never overwrite what is already there
        writes[field] = value
    if not writes:
        return "no_exif", {}

    if not dry_run:
        # shot_date is DERIVED, so it has to be recomputed here — writing
        # creation_date alone would leave the facet still showing `unknown`,
        # which is the symptom this script exists to clear.
        if "creation_date" in writes:
            writes["shot_date"] = db.normalise_shot_date(writes["creation_date"])
        assignments = ", ".join("{0} = ?".format(k) for k in writes)
        conn.execute(
            "UPDATE media SET {0} WHERE id = ?".format(assignments),
            list(writes.values()) + [row["id"]],
        )
    return "ok", writes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written, change nothing")
    ap.add_argument("--limit", type=int, default=None,
                    help="only look at the first N candidate rows")
    args = ap.parse_args(argv)

    db.init_db()
    counts = {"ok": 0, "missing": 0, "no_exif": 0}
    with db.get_conn() as conn:
        rows = rows_needing_backfill(conn, args.limit)
        print("{0} row(s) with no creation_date".format(len(rows)))
        for row in rows:
            status, writes = backfill_row(conn, row, dry_run=args.dry_run)
            counts[status] += 1
            if status == "ok":
                print("  [{0}] {1}: {2}".format(
                    row["id"], row["filename"],
                    ", ".join("{0}={1}".format(k, v) for k, v in writes.items())))
            elif status == "missing":
                print("  [{0}] {1}: file not reachable — skipped".format(
                    row["id"], row["filename"]))
    verb = "would write" if args.dry_run else "wrote"
    print("{0} {1}; {2} without EXIF; {3} unreachable".format(
        verb, counts["ok"], counts["no_exif"], counts["missing"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
