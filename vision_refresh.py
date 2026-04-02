#!/usr/bin/env python3
"""
Vision Refresh — only process frames with empty descriptions.
Avoids re-running vision on frames that already have descriptions.

Usage:
    python vision_refresh.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time

import config
import vision as vis


def main():
    parser = argparse.ArgumentParser(description="Refresh empty vision frame descriptions")
    parser.add_argument("--limit", type=int, help="Max frames to process")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without running vision")
    parser.add_argument("--db", default=str(config.DB_PATH), help="Database path")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # Find frames with empty descriptions (skip audio-only media)
    rows = conn.execute("""
        SELECT f.id, f.media_id, f.frame_index, f.thumbnail_path, m.filename
        FROM frames f
        JOIN media m ON f.media_id = m.id
        WHERE (f.description IS NULL OR f.description = '')
          AND f.thumbnail_path IS NOT NULL
        ORDER BY f.media_id, f.frame_index
    """).fetchall()

    total = len(rows)
    if args.limit:
        rows = rows[:args.limit]

    print(f"Found {total} frames with empty descriptions. Processing {len(rows)}...")
    if args.dry_run:
        for r in rows:
            print(f"  [{r['media_id']}] {r['filename']} frame {r['frame_index']}: {r['thumbnail_path']}")
        return

    ok, fail = 0, 0
    start = time.time()

    # Group by media_id for batch updates
    current_media_id = None
    media_frames = []

    for i, r in enumerate(rows, 1):
        thumb = r["thumbnail_path"]
        print(f"[{i}/{len(rows)}] {r['filename']} frame {r['frame_index']}", end="", flush=True)

        t0 = time.time()
        result = vis._describe_one(thumb)
        elapsed = time.time() - t0

        desc = result.get("description", "")
        tags = result.get("tags", [])

        if desc:
            conn.execute(
                "UPDATE frames SET description = ?, tags = ? WHERE id = ?",
                (desc, ",".join(tags) if tags else "", r["id"])
            )
            conn.commit()
            ok += 1
            print(f" [{elapsed:.1f}s] ✓ {desc[:60]}")
        else:
            fail += 1
            err = result.get("error", "empty response")
            print(f" [{elapsed:.1f}s] ✗ {err}")

        # Also update media.frame_tags JSON for this media
        if r["media_id"] != current_media_id:
            if current_media_id is not None:
                _update_media_frame_tags(conn, current_media_id)
            current_media_id = r["media_id"]

    # Final media update
    if current_media_id is not None:
        _update_media_frame_tags(conn, current_media_id)

    elapsed_total = time.time() - start
    print(f"\nDone. OK={ok}  fail={fail}  time={elapsed_total:.0f}s  avg={elapsed_total/max(ok+fail,1):.1f}s/frame")
    conn.close()


def _update_media_frame_tags(conn, media_id):
    """Rebuild media.frame_tags JSON from frames table."""
    frames = conn.execute(
        "SELECT description, tags FROM frames WHERE media_id = ? ORDER BY frame_index",
        (media_id,)
    ).fetchall()

    frame_tags = json.dumps([
        {
            "description": f["description"] or "",
            "tags": [t.strip() for t in (f["tags"] or "").split(",") if t.strip()]
        }
        for f in frames
    ], ensure_ascii=False)

    conn.execute("UPDATE media SET frame_tags = ? WHERE id = ?", (frame_tags, media_id))
    conn.commit()


if __name__ == "__main__":
    main()
