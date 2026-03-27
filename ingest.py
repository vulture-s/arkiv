#!/usr/bin/env python3
"""
Local Media Asset Manager — Phase 1 Ingest CLI
Usage:
    python ingest.py --dir /path/to/media [--limit N] [--skip-vision] [--db /path/to/media.db]
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import db
import frames as frm
import transcribe as tr
import vision as vis

SUPPORTED = {".mp4", ".mov", ".m4v", ".mts", ".wav", ".mp3", ".m4a", ".aac"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mts"}


def probe(path: str) -> dict | None:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format", path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None

    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(fmt.get("duration") or 0)
    size_mb = int(fmt.get("size") or 0) / 1024 / 1024

    fps = 0.0
    if video_stream:
        r_fps = video_stream.get("r_frame_rate", "0/1")
        try:
            num, den = r_fps.split("/")
            fps = float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            fps = 0.0

    return {
        "duration_s": round(duration, 2),
        "size_mb": round(size_mb, 2),
        "width": video_stream.get("width") if video_stream else None,
        "height": video_stream.get("height") if video_stream else None,
        "fps": round(fps, 2) if fps else None,
        "has_audio": 1 if audio_stream else 0,
    }


def process_file(path: Path, skip_vision: bool, existing: dict | None = None) -> dict:
    """
    Process one media file.
    If `existing` is provided (refresh mode), skip transcription and reuse existing
    transcript/lang — only re-run thumbnail + vision.
    """
    print(" → probe", end="", flush=True)
    meta = probe(str(path))
    if meta is None:
        print(" [ffprobe failed]")
        return {}

    record = {
        "path": str(path),
        "filename": path.name,
        "ext": path.suffix.lower(),
        **meta,
        "transcript": existing.get("transcript") if existing else None,
        "lang": existing.get("lang") if existing else None,
        "frame_tags": None,
        "thumbnail_path": None,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Audio transcription (skip on refresh — reuse existing)
    if meta["has_audio"] and not existing:
        print(" → whisper", end="", flush=True)
        text, lang = tr.transcribe(str(path))
        record["transcript"] = text or None
        record["lang"] = lang or None

    # Thumbnail (video only, always extracted)
    is_video = path.suffix.lower() in VIDEO_EXT
    if is_video and meta["duration_s"] > 0:
        print(" → thumb", end="", flush=True)
        record["thumbnail_path"] = frm.extract_thumbnail(str(path), meta["duration_s"])

    # Frame description (video only)
    if is_video and not skip_vision and meta["duration_s"] > 0:
        print(" → llava", end="", flush=True)
        frame_paths = frm.extract_frames(str(path), meta["duration_s"], meta["fps"] or 30)
        if frame_paths:
            frame_results = vis.describe_frames(frame_paths)
            record["frame_tags"] = vis.frames_to_json(frame_results)
            # Clean up temp frames
            for fp in frame_paths:
                Path(fp).unlink(missing_ok=True)
            shutil.rmtree(Path(frame_paths[0]).parent, ignore_errors=True)

    print(" ✓")
    return record


def main():
    parser = argparse.ArgumentParser(description="Ingest media files into SQLite DB")
    parser.add_argument("--dir", required=True, help="Media directory to scan")
    parser.add_argument("--limit", type=int, default=0, help="Max files to process (0=all)")
    parser.add_argument("--skip-vision", action="store_true", help="Skip llava frame description")
    parser.add_argument("--refresh", action="store_true", help="Re-process already-indexed files (thumbnail + vision)")
    parser.add_argument("--db", default="", help="Path to SQLite DB (default: media.db next to ingest.py)")
    args = parser.parse_args()

    if args.db:
        db.DB_PATH = Path(args.db)

    db.init_db()

    # Warm up models before batch processing
    print("Warming up models...", flush=True)
    tr.warm_up()
    tr.warm_up_ollama()
    print("")

    media_dir = Path(args.dir)
    if not media_dir.exists():
        print(f"Error: {media_dir} does not exist")
        sys.exit(1)

    files = sorted(
        f for f in media_dir.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED
    )

    total = len(files)
    if args.limit:
        files = files[:args.limit]

    print(f"Found {total} media files. Processing {len(files)}...\n")

    ok, skipped, failed = 0, 0, 0
    for i, f in enumerate(files, 1):
        already = db.is_processed(str(f))
        if already and not args.refresh:
            print(f"[{i}/{len(files)}] SKIP {f.name}")
            skipped += 1
            continue

        existing = None
        if already and args.refresh:
            with db.get_conn() as conn:
                row = conn.execute("SELECT * FROM media WHERE path=?", (str(f),)).fetchone()
                existing = dict(row) if row else None

        print(f"[{i}/{len(files)}] {f.name}", end="", flush=True)
        try:
            record = process_file(f, args.skip_vision, existing=existing)
            if record:
                db.upsert(record)
                ok += 1
            else:
                failed += 1
        except Exception as e:
            print(f" [ERROR: {e}]")
            failed += 1

    print(f"\nDone. ✓ {ok}  skip {skipped}  fail {failed}")
    print(f"DB: {db.DB_PATH}")


if __name__ == "__main__":
    main()
