#!/usr/bin/env python3
"""
Local Media Asset Manager — Phase 1 Ingest CLI
Usage:
    python ingest.py --dir /path/to/media [--limit N] [--skip-vision] [--db /path/to/media.db]
"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
import db
import frames as frm
import transcribe as tr
import vision as vis

SUPPORTED = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mts"}


def probe(path: str) -> dict | None:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format", path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
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

    # Handle rotation metadata — swap width/height for 90/270 degree rotation
    w = video_stream.get("width") if video_stream else None
    h = video_stream.get("height") if video_stream else None
    if video_stream and w and h:
        rot = 0
        # Check tags.rotate
        rot_str = (video_stream.get("tags") or {}).get("rotate", "")
        if rot_str:
            try: rot = int(rot_str)
            except ValueError: pass
        # Check side_data_list rotation
        if not rot:
            for sd in (video_stream.get("side_data_list") or []):
                if "rotation" in sd:
                    try: rot = abs(int(sd["rotation"]))
                    except (ValueError, TypeError): pass
        if rot in (90, 270):
            w, h = h, w

    return {
        "duration_s": round(duration, 2),
        "size_mb": round(size_mb, 2),
        "width": w,
        "height": h,
        "fps": round(fps, 2) if fps else None,
        "has_audio": 1 if audio_stream else 0,
    }


def exiftool_extract(path: str) -> dict:
    """Extract EXIF metadata via exiftool -json. Returns dict of 12 fields."""
    cmd = [
        config.EXIFTOOL_PATH, "-json",
        "-Make", "-Model", "-LensModel",
        "-GPSLatitude", "-GPSLongitude",
        "-ColorSpace",
        "-ISO",
        "-ShutterSpeed", "-ExposureTime",
        "-FNumber", "-ApertureValue",
        "-FocalLength",
        "-CreateDate", "-DateTimeOriginal",
        "-n",  # numeric output for GPS
        path,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        if r.returncode != 0:
            return {}
        data = json.loads(r.stdout)
        if not data:
            return {}
        d = data[0]
    except Exception:
        return {}

    # Parse focal length (may be "50 mm" or numeric)
    fl_raw = d.get("FocalLength")
    fl = None
    if fl_raw is not None:
        try:
            fl = float(str(fl_raw).replace("mm", "").strip())
        except (ValueError, TypeError):
            pass

    # Parse shutter speed — prefer ExposureTime as string
    ss = d.get("ShutterSpeed") or d.get("ExposureTime")
    ss_str = str(ss) if ss else None

    # Parse aperture — prefer FNumber
    ap_raw = d.get("FNumber") or d.get("ApertureValue")
    ap = None
    if ap_raw is not None:
        try:
            ap = float(ap_raw)
        except (ValueError, TypeError):
            pass

    # Creation date — prefer CreateDate, fallback DateTimeOriginal
    cdate = d.get("CreateDate") or d.get("DateTimeOriginal")
    cdate_str = str(cdate) if cdate else None

    return {
        "camera_make": d.get("Make"),
        "camera_model": d.get("Model"),
        "lens_model": d.get("LensModel"),
        "gps_lat": d.get("GPSLatitude"),
        "gps_lon": d.get("GPSLongitude"),
        "color_space": str(d.get("ColorSpace")) if d.get("ColorSpace") else None,
        "iso": d.get("ISO"),
        "shutter_speed": ss_str,
        "aperture": ap,
        "focal_length": fl,
        "creation_date": cdate_str,
    }


def process_file(path: Path, skip_vision: bool, existing: dict | None = None) -> dict:
    """
    Process one media file.
    If `existing` is provided (refresh mode), skip transcription and reuse existing
    transcript/lang — only re-run thumbnail + vision.
    """
    print(" >probe", end="", flush=True)
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
        print(" >whisper", end="", flush=True)
        text, lang = tr.transcribe(str(path))
        record["transcript"] = text or None
        record["lang"] = lang or None

    # Thumbnail (video only, always extracted)
    is_video = path.suffix.lower() in VIDEO_EXT
    if is_video and meta["duration_s"] > 0:
        print(" >thumb", end="", flush=True)
        record["thumbnail_path"] = frm.extract_thumbnail(str(path), meta["duration_s"])

    # Frame extraction (video only) — persistent thumbnails + DB records
    if is_video and meta["duration_s"] > 0:
        print(" >frames", end="", flush=True)
        frame_data = frm.extract_frames(str(path), meta["duration_s"], meta["fps"] or 30)
        record["_frames"] = frame_data  # pass to caller for DB insert

        # Vision description (optional)
        if not skip_vision and frame_data:
            print(" >llava", end="", flush=True)
            frame_paths_for_vision = [f["thumbnail_path"] for f in frame_data]
            frame_results = vis.describe_frames(frame_paths_for_vision)
            for fd, vr in zip(frame_data, frame_results):
                fd["description"] = vr.get("description", "")
                fd["tags"] = ",".join(vr.get("tags", []))
            # Also store legacy frame_tags for backwards compat
            record["frame_tags"] = vis.frames_to_json(frame_results)

    print(" [OK]")
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
                frames = record.pop("_frames", [])
                db.upsert(record)
                # Store frame analysis data
                if frames:
                    # Get media_id for frame storage
                    with db.get_conn() as conn:
                        row = conn.execute("SELECT id FROM media WHERE path=?", (str(f),)).fetchone()
                        if row:
                            mid = row[0]
                            db.delete_frames(mid)
                            for fd in frames:
                                db.upsert_frame(
                                    media_id=mid,
                                    frame_index=fd["index"],
                                    timestamp_s=fd["timestamp_s"],
                                    thumbnail_path=fd.get("thumbnail_path"),
                                    description=fd.get("description", ""),
                                    tags=fd.get("tags", ""),
                                )
                ok += 1
            else:
                failed += 1
        except Exception as e:
            print(f" [ERROR: {e}]")
            failed += 1

    print(f"\nDone. OK={ok}  skip={skipped}  fail={failed}")
    print(f"DB: {db.DB_PATH}")


if __name__ == "__main__":
    main()
