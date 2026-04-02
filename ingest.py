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


def _unload_ollama_model(model: str):
    """Ask Ollama to unload a model from VRAM, freeing memory for the next phase."""
    import urllib.request
    try:
        payload = json.dumps({"model": model, "keep_alive": 0}).encode()
        req = urllib.request.Request(
            f"{config.OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=30)
        print(f"  Unloaded {model} from VRAM")
    except Exception as e:
        print(f"  Warning: could not unload {model}: {e}")


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

    # Extract start timecode (camera body TC, may not start at 00:00:00:00)
    start_tc = None
    # Check video stream tags first (most cameras embed here)
    if video_stream:
        start_tc = (video_stream.get("tags") or {}).get("timecode")
    # Fallback: format-level tags
    if not start_tc:
        start_tc = (fmt.get("tags") or {}).get("timecode")
    # Fallback: check for timecode stream
    if not start_tc:
        tc_stream = next((s for s in streams if s.get("codec_tag_string") == "tmcd"), None)
        if tc_stream:
            start_tc = (tc_stream.get("tags") or {}).get("timecode")

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
        "start_tc": start_tc,
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
    if args.limit and not args.refresh:
        # Filter out already-processed files before applying limit
        new_files = [f for f in files if not db.is_processed(str(f))]
        skipped_count = total - len(new_files)
        files = new_files[:args.limit]
        print(f"Found {total} media files ({skipped_count} already indexed). Processing {len(files)}...\n")
    elif args.limit:
        files = files[:args.limit]
        print(f"Found {total} media files. Processing {len(files)}...\n")
    else:
        print(f"Found {total} media files. Processing {len(files)}...\n")

    import time as _time

    ok, skipped, failed = 0, 0, 0
    bench_log = []  # per-file benchmark records
    batch_start = _time.time()

    # ── Phase 1: Probe + Whisper + LLM polish (skip vision) ────────────────
    # On VRAM-limited GPUs (e.g. RTX 4070 12GB), qwen2.5:14b (LLM polish)
    # and qwen3-vl:8b (vision) cannot coexist. Run transcription first,
    # then unload LLM and run vision in a separate pass.
    need_vision = not args.skip_vision
    phase1_results = {}  # path -> (record, frames)

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
        file_start = _time.time()
        try:
            # Phase 1: always skip vision — will run in Phase 2
            record = process_file(f, skip_vision=True, existing=existing)
            file_elapsed = _time.time() - file_start
            if record:
                frames = record.pop("_frames", [])
                db.upsert(record)
                # Store frame records (without vision descriptions yet)
                if frames:
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
                    # Queue for Phase 2 vision
                    if need_vision:
                        phase1_results[str(f)] = (record, frames)

                dur = record.get("duration_s", 0)
                bench_log.append({
                    "file": f.name,
                    "duration_s": dur,
                    "process_s": round(file_elapsed, 1),
                    "rtf": round(file_elapsed / max(dur, 1), 3),
                    "speed_x": round(dur / max(file_elapsed, 1), 1),
                })
                print(f"  [{file_elapsed:.1f}s | {dur/max(file_elapsed,1):.1f}x RT]")
                ok += 1
            else:
                failed += 1
        except Exception as e:
            print(f" [ERROR: {e}]")
            failed += 1

    # ── Phase 2: Vision (unload LLM first to free VRAM) ───────────────────
    if phase1_results:
        print(f"\n{'─'*60}")
        print(f"Phase 2: Vision — {len(phase1_results)} files, unloading LLM to free VRAM...")
        _unload_ollama_model("qwen2.5:14b")

        vision_ok, vision_fail = 0, 0
        for vi, (fpath, (record, frames)) in enumerate(phase1_results.items(), 1):
            fname = Path(fpath).name
            video_frames = [fd for fd in frames if fd.get("thumbnail_path")]
            if not video_frames:
                continue

            print(f"[{vi}/{len(phase1_results)}] {fname} >vision", end="", flush=True)
            v_start = _time.time()
            try:
                frame_paths = [fd["thumbnail_path"] for fd in video_frames]
                frame_results = vis.describe_frames(frame_paths)
                for fd, vr in zip(video_frames, frame_results):
                    fd["description"] = vr.get("description", "")
                    fd["tags"] = ",".join(vr.get("tags", []))

                # Update DB: frames + media.frame_tags
                with db.get_conn() as conn:
                    row = conn.execute("SELECT id FROM media WHERE path=?", (fpath,)).fetchone()
                    if row:
                        mid = row[0]
                        for fd in video_frames:
                            conn.execute(
                                "UPDATE frames SET description=?, tags=? WHERE media_id=? AND frame_index=?",
                                (fd.get("description", ""), fd.get("tags", ""), mid, fd["index"])
                            )
                        frame_tags_json = vis.frames_to_json(frame_results)
                        conn.execute("UPDATE media SET frame_tags=? WHERE id=?", (frame_tags_json, mid))

                v_elapsed = _time.time() - v_start
                print(f" [{v_elapsed:.1f}s] [OK]")
                vision_ok += 1
                # Write auto tags from vision
                with db.get_conn() as conn:
                    row = conn.execute("SELECT id FROM media WHERE path=?", (fpath,)).fetchone()
                    if row:
                        mid = row[0]
                        for fd in video_frames:
                            for tag_name in fd.get("tags", "").split(","):
                                tag_name = tag_name.strip()
                                if tag_name and tag_name != "```":
                                    db.add_tag(mid, tag_name, source="auto")
            except Exception as e:
                print(f" [ERROR: {e}]")
                vision_fail += 1

        print(f"Vision done. OK={vision_ok}  fail={vision_fail}")
    elif need_vision:
        print("\nNo new files to run vision on.")

    batch_elapsed = _time.time() - batch_start
    total_dur = sum(b["duration_s"] for b in bench_log)

    print(f"\nDone. OK={ok}  skip={skipped}  fail={failed}")
    print(f"DB: {db.DB_PATH}")

    if bench_log:
        print(f"\n{'='*60}")
        print(f"BENCHMARK SUMMARY — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'='*60}")
        print(f"Pipeline: faster-whisper {config.WHISPER_MODEL} + Silero VAD + {config.VISION_MODEL}")
        print(f"{'─'*60}")
        print(f"{'File':<20} {'Duration':>8} {'Process':>8} {'Speed':>8}")
        print(f"{'─'*60}")
        for b in bench_log:
            m, s = divmod(int(b["duration_s"]), 60)
            print(f"{b['file']:<20} {m:02d}:{s:02d}    {b['process_s']:>6.1f}s  {b['speed_x']:>5.1f}x")
        print(f"{'─'*60}")
        m, s = divmod(int(total_dur), 60)
        print(f"{'TOTAL':<20} {m:02d}:{s:02d}    {batch_elapsed:>6.1f}s  {total_dur/max(batch_elapsed,1):>5.1f}x")
        print(f"{'='*60}")

        # Save bench log to JSON
        bench_path = config.BASE_DIR / "bench_ingest.json"
        bench_data = {
            "timestamp": datetime.now().isoformat(),
            "pipeline": f"faster-whisper {config.WHISPER_MODEL} + Silero VAD + {config.VISION_MODEL}",
            "gpu": "NVIDIA GeForce RTX 4070",
            "total_duration_s": round(total_dur, 1),
            "total_process_s": round(batch_elapsed, 1),
            "overall_speed_x": round(total_dur / max(batch_elapsed, 1), 1),
            "files": bench_log,
        }
        bench_path.write_text(json.dumps(bench_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Bench log saved: {bench_path}")


if __name__ == "__main__":
    main()
