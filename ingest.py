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
from typing import Optional, Tuple
from typing import Dict, List

import codec
import config
import db
import frames as frm
import transcribe as tr
import vision as vis

SUPPORTED = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mts"}
# Codecs needing browser-playable proxy — single source of truth in codec.py.
PROXY_CODECS = codec.PROXY_CODECS


def _warm_up_vision_model():
    """Send a dummy request to ensure qwen3-vl:8b is loaded in VRAM."""
    import urllib.request
    model = config.VISION_MODEL
    print(f"  Warming up vision model ({model})...", end="", flush=True)
    try:
        payload = json.dumps({
            "model": model,
            "prompt": "hi",
            "stream": False,
            "options": {"num_predict": 1},
        }).encode()
        req = urllib.request.Request(
            f"{config.OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=180)
        print(" ready")
    except Exception as e:
        print(f" warning: {e}")


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


def probe(path: str) -> Optional[Dict]:
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


def needs_proxy(path: str) -> bool:
    """Backward-compatible bool shim. Returns True only when codec.needs_proxy
    確定要 proxy（codec.NEEDED）；UNKNOWN/NOT_NEEDED 都當不需要，與舊 except→False
    行為一致。新 code 直接呼叫 codec.needs_proxy() 拿 tri-state 比較精準。"""
    return codec.needs_proxy(path) == codec.NEEDED


def generate_proxy(media_id: int, path: str, force: bool = False) -> Optional[str]:
    """Generate a 720p H.264 proxy for browser playback. Returns proxy path or None."""
    proxy_dir = config.PROXIES_DIR
    proxy_dir.mkdir(parents=True, exist_ok=True)
    proxy_path = config.proxy_path_for(media_id, path)
    if proxy_path.exists() and not force:
        return str(proxy_path)
    if force:
        proxy_path.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "28",
        "-profile:v", "high", "-level:v", "4.0",
        "-pix_fmt", "yuv420p",
        "-g", "30",
        "-vf", "scale=-2:720",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(proxy_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode == 0 and proxy_path.exists():
            return str(proxy_path)
        proxy_path.unlink(missing_ok=True)
        return None
    except Exception:
        proxy_path.unlink(missing_ok=True)
        return None


def _db_path_params(path: Path) -> Tuple[str, str]:
    abs_path = str(path)
    rel_path = db.to_relative(abs_path)
    return abs_path, rel_path


def _get_media_row_for_path(path: Path) -> Optional[Dict]:
    abs_path, rel_path = _db_path_params(path)
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM media WHERE path=? OR path=?",
            (abs_path, rel_path),
        ).fetchone()
        return dict(row) if row else None


def _get_media_id_for_path(path: Path) -> Optional[int]:
    abs_path, rel_path = _db_path_params(path)
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM media WHERE path=? OR path=?",
            (abs_path, rel_path),
        ).fetchone()
        return row[0] if row else None


def _apply_vision_to_frame_data(frame_data: List[Dict], frame_results: List[Dict]) -> List[float]:
    scores = []
    for fd, vr in zip(frame_data, frame_results):
        fd["description"] = vr.get("description", "")
        fd["tags"] = ",".join(vr.get("tags", []))
        fd["content_type"] = vr.get("content_type")
        fd["focus_score"] = vr.get("focus_score")
        fd["exposure"] = vr.get("exposure")
        fd["stability"] = vr.get("stability")
        fd["audio_quality"] = vr.get("audio_quality")
        fd["atmosphere"] = vr.get("atmosphere")
        fd["energy"] = vr.get("energy")
        fd["edit_position"] = vr.get("edit_position")
        fd["edit_reason"] = vr.get("edit_reason")
        if fd.get("focus_score") is not None:
            scores.append(db.compute_editability(fd))
    return scores


def process_file(path: Path, skip_vision: bool, existing: Optional[Dict] = None) -> Dict:
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

    exif = exiftool_extract(str(path))

    record = {
        "path": db.to_relative(str(path)),
        "filename": path.name,
        "ext": path.suffix.lower(),
        **meta,
        **exif,
        "transcript": existing.get("transcript") if existing else None,
        "lang": existing.get("lang") if existing else None,
        "frame_tags": None,
        "thumbnail_path": None,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Audio transcription (skip on refresh — reuse existing)
    if meta["has_audio"] and not existing:
        print(" >whisper", end="", flush=True)
        text, lang, segments, words = tr.transcribe(str(path))
        record["transcript"] = text if text is not None else ""
        record["lang"] = lang or None
        if segments:
            import json
            record["segments_json"] = json.dumps(segments, ensure_ascii=False)
        else:
            record["segments_json"] = None
        if words:
            record["words_json"] = json.dumps(words, ensure_ascii=False)
        else:
            record["words_json"] = None

    # Thumbnail (video only, always extracted)
    is_video = path.suffix.lower() in VIDEO_EXT
    if is_video and meta["duration_s"] > 0:
        print(" >thumb", end="", flush=True)
        thumb_path = frm.extract_thumbnail(str(path), meta["duration_s"])
        record["thumbnail_path"] = db.to_relative(thumb_path) if thumb_path else None

    # Frame extraction (video only) — persistent thumbnails + DB records
    if is_video and meta["duration_s"] > 0:
        print(" >frames", end="", flush=True)
        frame_data = frm.extract_frames(str(path), meta["duration_s"], meta["fps"] or 30)
        for frame in frame_data:
            if frame.get("thumbnail_path"):
                frame["thumbnail_path"] = db.to_relative(frame["thumbnail_path"])
        record["_frames"] = frame_data  # pass to caller for DB insert

        # Vision description (optional)
        if not skip_vision and frame_data:
            print(" >llava", end="", flush=True)
            frame_paths_for_vision = [
                db.resolve_path(f["thumbnail_path"]) if f.get("thumbnail_path") else ""
                for f in frame_data
            ]
            frame_results = vis.describe_frames(frame_paths_for_vision)
            scores = _apply_vision_to_frame_data(frame_data, frame_results)
            if scores:
                record["editability_score"] = max(scores)
            # Also store legacy frame_tags for backwards compat
            record["frame_tags"] = vis.frames_to_json(frame_results)

    print(" [OK]")
    return record


def _run_vision_only(args):
    """Resume vision: only process frames with empty descriptions."""
    import time as _time
    print(f"\n{'═'*60}")
    print("Vision-Only Mode: patching frames with empty descriptions")
    print(f"{'═'*60}\n")

    # Find all frames missing vision
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT m.id, m.path, m.filename, f.frame_index, f.thumbnail_path
            FROM media m
            JOIN frames f ON f.media_id = m.id
            WHERE (f.description IS NULL OR f.description = '')
              AND f.thumbnail_path IS NOT NULL
            ORDER BY m.id, f.frame_index
        """).fetchall()

    if not rows:
        print("All frames already have vision descriptions. Nothing to do.")
        return

    # Group by media
    from collections import defaultdict
    media_frames = defaultdict(list)
    for r in rows:
        media_frames[r["id"]].append(r)

    print(f"Found {len(rows)} frames across {len(media_frames)} files\n")

    _unload_ollama_model("qwen2.5:14b")
    _warm_up_vision_model()

    ok, halted = 0, False
    for vi, (mid, frames_list) in enumerate(media_frames.items(), 1):
        fname = frames_list[0]["filename"]
        frame_paths = [db.resolve_path(f["thumbnail_path"]) for f in frames_list]

        print(f"[{vi}/{len(media_frames)}] {fname} ({len(frame_paths)} frames) >vision", end="", flush=True)
        v_start = _time.time()

        # Phase 1: primary vision model
        frame_results = vis.describe_frames(frame_paths)
        failed_indices = [i for i, vr in enumerate(frame_results) if vr.get("error") or not vr.get("description")]

        # Phase 2: fallback model for failed frames
        if failed_indices:
            print(f" [Phase 1: {len(failed_indices)} failed, trying fallback]", end="", flush=True)
            fallback_model = "minicpm-v:latest"
            original_model = vis.VISION_MODEL
            try:
                vis.VISION_MODEL = fallback_model
                retry_paths = [frame_paths[i] for i in failed_indices]
                retry_results = vis.describe_frames(retry_paths)
                for idx, retry_r in zip(failed_indices, retry_results):
                    if retry_r.get("description") and not retry_r.get("error"):
                        frame_results[idx] = retry_r
            finally:
                vis.VISION_MODEL = original_model

        # Check if still failing after both phases
        still_failed = sum(1 for vr in frame_results if vr.get("error") or not vr.get("description"))
        if still_failed:
            remaining = len(media_frames) - vi
            print(f"\n\n{'!'*60}")
            print(f"VISION HALTED: {still_failed}/{len(frame_paths)} frames failed on {fname} (both models)")
            print(f"  Completed: {ok}  |  Remaining: {remaining}")
            print(f"  Fix Ollama, then re-run: py -3.12 ingest.py --dir {args.dir} --vision-only")
            print(f"{'!'*60}\n")
            halted = True
            break

        # Write to DB
        with db.get_conn() as conn:
            for f_info, vr in zip(frames_list, frame_results):
                desc = vr.get("description", "")
                tags = ",".join(vr.get("tags", []))
                conn.execute(
                    """
                    UPDATE frames
                    SET description=?, tags=?, content_type=?, focus_score=?, exposure=?,
                        stability=?, audio_quality=?, atmosphere=?, energy=?,
                        edit_position=?, edit_reason=?
                    WHERE media_id=? AND frame_index=?
                    """,
                    (
                        desc,
                        tags,
                        vr.get("content_type"),
                        vr.get("focus_score"),
                        vr.get("exposure"),
                        vr.get("stability"),
                        vr.get("audio_quality"),
                        vr.get("atmosphere"),
                        vr.get("energy"),
                        vr.get("edit_position"),
                        vr.get("edit_reason"),
                        f_info["id"],
                        f_info["frame_index"],
                    )
                )
                # Auto tags (pass conn to avoid self-deadlock)
                for tag_name in vr.get("tags", []):
                    tag_name = tag_name.strip()
                    if tag_name and tag_name != "```":
                        db.add_tag(mid, tag_name, source="auto", _conn=conn)
            # Update legacy frame_tags
            frame_tags_json = vis.frames_to_json(frame_results)
            conn.execute("UPDATE media SET frame_tags=? WHERE id=?", (frame_tags_json, mid))
            scores = _apply_vision_to_frame_data([dict(f) for f in frames_list], frame_results)
            if scores:
                conn.execute(
                    "UPDATE media SET editability_score=? WHERE id=?",
                    (max(scores), mid),
                )

        v_elapsed = _time.time() - v_start
        print(f" [{v_elapsed:.1f}s] [OK]")
        ok += 1

    if not halted:
        print(f"\nVision-only done. {ok} files patched.")


def _regenerate_proxies():
    """Rebuild all existing proxies with latest encoding settings."""
    proxy_dir = config.PROXIES_DIR
    existing = sorted(proxy_dir.glob("*.mp4")) if proxy_dir.exists() else []
    if not existing:
        print("No existing proxies to regenerate.")
        return

    print(f"Regenerating {len(existing)} proxies...")
    ok, failed = 0, 0
    for idx, proxy_path in enumerate(existing, 1):
        # Proxy filename is "{media_id}_{hash}.mp4" since the path-hash fix;
        # legacy files named just "{media_id}.mp4" are pre-fix orphans and
        # should be deleted (they may be cross-contaminated from another
        # install).
        stem_head = proxy_path.stem.split("_", 1)[0]
        try:
            mid = int(stem_head)
        except ValueError:
            print(f"  [SKIP] {proxy_path.name} (non-numeric stem)")
            continue
        rec = db.get_record_by_id(mid)
        if not rec:
            print(f"  [{idx}/{len(existing)}] id={mid}: record missing, deleting orphan proxy")
            proxy_path.unlink(missing_ok=True)
            continue
        src = db.resolve_path(rec["path"])
        expected_name = config.proxy_path_for(mid, src).name
        if proxy_path.name != expected_name:
            print(f"  [{idx}/{len(existing)}] {proxy_path.name}: stale naming, deleting")
            proxy_path.unlink(missing_ok=True)
        if not Path(src).exists():
            print(f"  [{idx}/{len(existing)}] id={mid}: source missing ({src}), skipping")
            failed += 1
            continue
        print(f"  [{idx}/{len(existing)}] id={mid} {rec['filename']}", end="", flush=True)
        result = generate_proxy(mid, src, force=True)
        if result:
            print(" [OK]")
            ok += 1
        else:
            print(" [FAIL]")
            failed += 1
    print(f"\nRegenerated: {ok}  Failed: {failed}")


def main():
    parser = argparse.ArgumentParser(description="Ingest media files into SQLite DB")
    parser.add_argument("--dir", required=True, help="Media directory to scan")
    parser.add_argument("--limit", type=int, default=0, help="Max files to process (0=all)")
    parser.add_argument("--skip-vision", action="store_true", help="Skip llava frame description")
    parser.add_argument("--refresh", action="store_true", help="Re-process already-indexed files (thumbnail + vision)")
    parser.add_argument("--vision-only", action="store_true", help="Only run vision on frames with empty descriptions (resume after halt)")
    parser.add_argument(
        "--migrate-relative",
        action="store_true",
        help="將 DB 中所有絕對路徑轉為相對路徑（對 ARKIV_PROJECT_ROOT）",
    )
    parser.add_argument(
        "--regenerate-proxies",
        action="store_true",
        help="刪除並重建所有 HEVC/ProRes proxy（套用最新編碼設定）",
    )
    parser.add_argument("--recursive", "-r", action="store_true", help="Recursively scan subdirectories")
    parser.add_argument("--db", default="", help="Path to SQLite DB (default: media.db next to ingest.py)")
    args = parser.parse_args()

    if args.db:
        db.DB_PATH = Path(args.db)

    db.init_db()

    if args.migrate_relative:
        db.migrate_to_relative()
        return

    if args.regenerate_proxies:
        _regenerate_proxies()
        return

    # ── Vision-only mode: patch missing vision descriptions ──────────────
    if args.vision_only:
        _run_vision_only(args)
        return

    # Warm up models before batch processing
    print("Warming up models...", flush=True)
    tr.warm_up()
    tr.warm_up_ollama()
    print("")

    media_dir = Path(args.dir)
    if not media_dir.exists():
        print(f"Error: {media_dir} does not exist")
        sys.exit(1)

    if media_dir.is_file():
        # Single-file mode (used by watch.py for new-arrival ingest)
        files = [media_dir] if media_dir.suffix.lower() in SUPPORTED else []
    elif args.recursive:
        files = sorted(
            f for f in media_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in SUPPORTED
        )
    else:
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
            existing = _get_media_row_for_path(f)

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
                        mid = _get_media_id_for_path(f)
                        if mid:
                            db.delete_frames(mid, _conn=conn)
                            for fd in frames:
                                db.upsert_frame(
                                    media_id=mid,
                                    frame_index=fd["index"],
                                    timestamp_s=fd["timestamp_s"],
                                    thumbnail_path=fd.get("thumbnail_path"),
                                    description=fd.get("description", ""),
                                    tags=fd.get("tags", ""),
                                    content_type=fd.get("content_type"),
                                    focus_score=fd.get("focus_score"),
                                    exposure=fd.get("exposure"),
                                    stability=fd.get("stability"),
                                    audio_quality=fd.get("audio_quality"),
                                    atmosphere=fd.get("atmosphere"),
                                    energy=fd.get("energy"),
                                    edit_position=fd.get("edit_position"),
                                    edit_reason=fd.get("edit_reason"),
                                    _conn=conn,
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
        _warm_up_vision_model()

        vision_ok, vision_fail = 0, 0
        for vi, (fpath, (record, frames)) in enumerate(phase1_results.items(), 1):
            fname = Path(fpath).name
            video_frames = [fd for fd in frames if fd.get("thumbnail_path")]
            if not video_frames:
                continue

            print(f"[{vi}/{len(phase1_results)}] {fname} >vision", end="", flush=True)
            v_start = _time.time()
            try:
                frame_paths = [db.resolve_path(fd["thumbnail_path"]) for fd in video_frames]
                # Phase 2a: primary vision model
                frame_results = vis.describe_frames(frame_paths)
                failed_indices = [i for i, vr in enumerate(frame_results) if vr.get("error") or not vr.get("description")]

                # Phase 2b: fallback model for failed frames
                if failed_indices:
                    print(f" [Phase 2a: {len(failed_indices)} failed, trying fallback]", end="", flush=True)
                    fallback_model = "minicpm-v:latest"
                    original_model = vis.VISION_MODEL
                    try:
                        vis.VISION_MODEL = fallback_model
                        retry_paths = [frame_paths[i] for i in failed_indices]
                        retry_results = vis.describe_frames(retry_paths)
                        for idx, retry_r in zip(failed_indices, retry_results):
                            if retry_r.get("description") and not retry_r.get("error"):
                                frame_results[idx] = retry_r
                    finally:
                        vis.VISION_MODEL = original_model

                scores = _apply_vision_to_frame_data(video_frames, frame_results)

                # Vision fail after both phases → halt
                still_failed = sum(1 for vr in frame_results if vr.get("error") or not vr.get("description"))
                if still_failed:
                    remaining = len(phase1_results) - vi
                    print(f"\n\n{'!'*60}")
                    print(f"VISION HALTED: {still_failed}/{len(video_frames)} frames failed on {fname} (both models)")
                    print(f"  Completed: {vision_ok}  |  Remaining: {remaining}")
                    print(f"  Fix Ollama, then resume with:")
                    print(f"    py -3.12 ingest.py --dir <path> --vision-only")
                    print(f"{'!'*60}\n")
                    break

                # Update DB: frames + media.frame_tags
                with db.get_conn() as conn:
                    mid = _get_media_id_for_path(Path(fpath))
                    if mid:
                        for fd in video_frames:
                            conn.execute(
                                """
                                UPDATE frames
                                SET description=?, tags=?, content_type=?, focus_score=?, exposure=?,
                                    stability=?, audio_quality=?, atmosphere=?, energy=?,
                                    edit_position=?, edit_reason=?
                                WHERE media_id=? AND frame_index=?
                                """,
                                (
                                    fd.get("description", ""),
                                    fd.get("tags", ""),
                                    fd.get("content_type"),
                                    fd.get("focus_score"),
                                    fd.get("exposure"),
                                    fd.get("stability"),
                                    fd.get("audio_quality"),
                                    fd.get("atmosphere"),
                                    fd.get("energy"),
                                    fd.get("edit_position"),
                                    fd.get("edit_reason"),
                                    mid,
                                    fd["index"],
                                )
                            )
                        frame_tags_json = vis.frames_to_json(frame_results)
                        conn.execute(
                            "UPDATE media SET frame_tags=?, editability_score=? WHERE id=?",
                            (frame_tags_json, max(scores) if scores else None, mid),
                        )

                v_elapsed = _time.time() - v_start
                print(f" [{v_elapsed:.1f}s] [OK]")
                vision_ok += 1
                # Write auto tags from vision
                with db.get_conn() as conn:
                    mid = _get_media_id_for_path(Path(fpath))
                    if mid:
                        for fd in video_frames:
                            for tag_name in fd.get("tags", "").split(","):
                                tag_name = tag_name.strip()
                                if tag_name and tag_name != "```":
                                    db.add_tag(mid, tag_name, source="auto", _conn=conn)
            except Exception as e:
                print(f" [ERROR: {e}]")
                vision_fail += 1

        print(f"Vision done. OK={vision_ok}  fail={vision_fail}")
    elif need_vision:
        print("\nNo new files to run vision on.")

    # ── Phase 3: Proxy generation (browser-incompatible codecs) ────────────
    print(f"\n{'─'*60}")
    print("Phase 3: Proxy generation for browser-incompatible codecs...")
    proxy_ok, proxy_skip = 0, 0
    with db.get_conn() as conn:
        all_media = conn.execute("SELECT id, path FROM media").fetchall()
    for mid, mpath in all_media:
        resolved_path = db.resolve_path(mpath)
        proxy_path = config.proxy_path_for(mid, resolved_path)
        if proxy_path.exists():
            proxy_skip += 1
            continue
        if not Path(resolved_path).suffix.lower() in VIDEO_EXT:
            continue
        if needs_proxy(resolved_path):
            print(f"  [{mid}] {Path(resolved_path).name} >proxy", end="", flush=True)
            result = generate_proxy(mid, resolved_path)
            if result:
                sz = Path(result).stat().st_size / (1024 * 1024)
                print(f" [OK {sz:.0f}MB]")
                proxy_ok += 1
            else:
                print(" [FAIL]")
    if proxy_ok or proxy_skip:
        print(f"Proxies: {proxy_ok} generated, {proxy_skip} already exist")
    else:
        print("No files need proxy (all browser-compatible)")

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
