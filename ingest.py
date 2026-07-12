#!/usr/bin/env python3
"""
Local Media Asset Manager — Phase 1 Ingest CLI
Usage:
    python ingest.py --dir /path/to/media [--limit N] [--skip-vision] [--db /path/to/media.db]
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from typing import Dict, List

import codec
import config
import db
import frames as frm
import tag_quality
import transcribe as tr
import vision as vis

SUPPORTED = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts",
             ".insv", ".360",  # 360 rigs (Insta360 / GoPro Max) — see VIDEO_EXT note
             ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}
# B3: .mkv/.avi/.webm are in SUPPORTED (so they ingest into the DB) but were
# absent here, so is_video was False for them → they silently skipped
# thumbnail/frames/vision. ffmpeg/ffprobe handle these containers, so treat
# them as video too. Keep VIDEO_EXT ⊆ SUPPORTED.
# 360 formats: .insv (Insta360) / .360 (GoPro Max) are HEVC-in-MOV/MP4 — ffmpeg
# probes + extracts frames fine (.insv verified 2026-06-12: dual 2880×2880 HEVC
# fisheye + AAC, thumbnail decodes). Frames land as raw fisheye, which is exactly
# what we want indexed (VLM still describes them). Competitor StoryCube shows the
# same files as UNKNOWN — see case-study storycube-asus-gopro-20260612.
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mts", ".mkv", ".avi", ".webm", ".insv", ".360"}
# Codecs needing browser-playable proxy — single source of truth in codec.py.
PROXY_CODECS = codec.PROXY_CODECS
logger = logging.getLogger(__name__)

# ── WS per-stage progress (S1a brick 3) ──────────────────────────────────────
# When driven by the WebSocket ingest (server.py sets ARKIV_STAGE_EVENTS=1) the
# pipeline emits machine-readable JSON events, each on its own flushed line behind
# a sentinel, so the line-buffered WS reader sees every stage in real time. Direct
# CLI runs leave the flag unset and keep the compact inline `>probe` markers — the
# existing human output (and its parsers) is unchanged.
_STAGE_EVENTS = os.environ.get("ARKIV_STAGE_EVENTS") == "1"

# brick 4: per-run whisper language override (None = auto-detect / guard-layer
# hint, the default). Set from --language in main(), read at the transcribe call.
_LANGUAGE_OVERRIDE = None


def _emit_progress(obj: Dict) -> None:
    """One JSON progress event on its own flushed line (sentinel-prefixed). No-op
    unless ARKIV_STAGE_EVENTS=1."""
    if _STAGE_EVENTS:
        print(f"__ARKIV__ {json.dumps(obj, ensure_ascii=False)}", flush=True)


def _stage(marker: str, stage: str) -> None:
    """Mark one pipeline stage: a structured event under the WS, or the compact
    inline `>marker` for direct CLI use."""
    if _STAGE_EVENTS:
        _emit_progress({"t": "stage", "stage": stage})
    else:
        print(f" >{marker}", end="", flush=True)


def _bench_pipeline_desc():
    """Human-readable pipeline string for the benchmark summary. Reports the
    *effective* vision model (settings.vision_model(), = config.VISION_MODEL
    when unset) so a `vision.model` override isn't misreported — warmup and the
    real vision calls both consume settings.vision_model()."""
    import settings as _settings
    return "faster-whisper {0} + Silero VAD + {1}".format(
        config.WHISPER_MODEL, _settings.vision_model())


def _warm_up_vision_model():
    """Send a dummy request to ensure the vision model is loaded in VRAM."""
    import urllib.request
    import settings as _settings
    # Warm up the SAME model + num_ctx the real calls will use (settings library
    # default, falling back to config) — otherwise warm-up and run diverge.
    model = _settings.vision_model()
    num_ctx = _settings.vision_num_ctx()
    print(f"  Warming up vision model ({model})...", end="", flush=True)
    try:
        payload = json.dumps({
            "model": model,
            "prompt": "hi",
            "stream": False,
            # Load with the same capped context the real vision calls use
            # (settings vision.num_ctx). Warming up at the model's default
            # context first balloons VRAM and leaves the model CPU-offloaded
            # even after the real calls reload it — keep them consistent so the
            # model lands 100% on GPU from the first frame.
            "options": {"num_predict": 1, "num_ctx": num_ctx},
        }).encode()
        req = urllib.request.Request(
            f"{config.OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        # audit L3: close the response socket instead of leaking the fd to GC
        with urllib.request.urlopen(req, timeout=180):
            pass
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
        # audit L3: close the response socket instead of leaking the fd to GC
        with urllib.request.urlopen(req, timeout=30):
            pass
        print(f"  Unloaded {model} from VRAM")
    except Exception as e:
        print(f"  Warning: could not unload {model}: {e}")


def _ensure_vision_ready(max_wait_s=None, _probe=None, _sleep=None):
    """Phase 11.5b: probe-driven gate before a vision batch.

    Systematizes the previously-ad-hoc warm-up: (1) bounded exponential
    backoff while memory pressure exceeds ARKIV_GPU_MEM_THRESHOLD, then
    (2) warm the vision model up if the probe says it isn't resident.

    SENSOR, NOT GATE: a degraded probe (no Ollama / no psutil) yields a
    PROCEED decision, so this never blocks ingest on a missing signal; and
    after max_wait it proceeds anyway with a warning. _probe/_sleep are
    injectable for tests. Returns the final probe dict.
    """
    import resource_probe as rp
    import time as _t

    raw_probe = _probe or rp.probe
    sleep = _sleep or _t.sleep
    try:
        import jobs
        active = jobs.active_count()
    except Exception:
        active = None
    if max_wait_s is None:
        try:
            max_wait_s = float(os.getenv("ARKIV_BACKPRESSURE_MAX_WAIT", "120"))
        except ValueError:
            max_wait_s = 120.0

    # Belt-and-suspenders for the red line: even though rp.probe never raises,
    # an injected probe (or a future change) might — a probe failure must never
    # block the vision phase, so treat it as a degraded (PROCEED) reading.
    def probe(active_jobs=None):
        try:
            return raw_probe(active_jobs=active_jobs)
        except Exception as e:  # noqa: BLE001
            r = rp._degraded_result("probe raised: {0}".format(e))
            r["active_jobs"] = active_jobs
            return r

    result = probe(active_jobs=active)
    print(f"  [probe] {rp.summary_line(result)}")
    decision, reason = rp.decide(result)
    waited, delay = 0.0, 2.0
    while decision == "WAIT" and waited < max_wait_s:
        this_sleep = min(delay, max_wait_s - waited)  # strictly bounded by max_wait
        print(f"  [backpressure] {reason} (waited {waited:.0f}s/{max_wait_s:.0f}s)")
        sleep(this_sleep)
        waited += this_sleep
        delay = min(delay * 2, 30.0)
        result = probe(active_jobs=active)
        decision, reason = rp.decide(result)
    if decision == "WAIT":
        print(f"  [backpressure] still busy after {max_wait_s:.0f}s — proceeding anyway (sensor, not gate)")
    import settings as _settings
    if not rp.is_model_loaded(result, _settings.vision_model()):
        _warm_up_vision_model()
    return result


def _normalize_bmd_tag(value):
    if value is None:
        return None
    tag = str(value).strip().lower()
    return tag or None


def _shutter_angle_to_speed(angle, fps, path=None):
    try:
        angle_value = float(angle)
        fps_value = float(fps)
        if angle_value <= 0 or fps_value <= 0:
            raise ValueError
    except (TypeError, ValueError):
        logger.warning(
            "Blackmagic-designCameraShutterAngle parse failed for %s: angle=%r fps=%r",
            path or "media",
            angle,
            fps,
        )
        return None

    denominator = round((360.0 * fps_value) / angle_value)
    if denominator <= 0:
        logger.warning(
            "Blackmagic-designCameraShutterAngle parse failed for %s: angle=%r fps=%r",
            path or "media",
            angle,
            fps,
        )
        return None
    return "1/{0}".format(denominator)


def _white_balance_string(kelvin, tint, path=None):
    try:
        kelvin_value = float(kelvin)
        tint_value = float(tint)
    except (TypeError, ValueError):
        logger.warning(
            "Blackmagic-designCameraWhiteBalance parse failed for %s: kelvin=%r tint=%r",
            path or "media",
            kelvin,
            tint,
        )
        return None
    return "{0}K T{1}".format(int(round(kelvin_value)), int(round(tint_value)))


def probe(path: str) -> Optional[Dict]:
    # `-v error` (not `quiet`) so real ffprobe errors surface on stderr — a
    # silent `-v quiet` failure mid-ingest used to print only "[ffprobe failed]"
    # with no cause. Bounded timeout + one retry recovers from transient
    # subprocess-spawn / resource pressure (e.g. handle exhaustion under load)
    # that otherwise poisons every subsequent clip in a long batch.
    cmd = [
        config.FFPROBE_PATH, "-v", "error",
        "-print_format", "json",
        "-show_streams", "-show_format", path
    ]
    r = None
    last_err = ""
    for attempt in range(2):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
            break
        except (OSError, subprocess.TimeoutExpired) as e:
            last_err = "{0}: {1}".format(type(e).__name__, e)
            if attempt == 0:
                time.sleep(2)  # let transient resource pressure clear, then retry
    if r is None:
        print("\n    [ffprobe spawn failed: {0}]".format(last_err))
        return None
    if r.returncode != 0:
        print("\n    [ffprobe rc={0}] {1}".format(r.returncode, (r.stderr or "").strip()[:300]))
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        print("\n    [ffprobe bad json: {0}]".format(e))
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
        # Persisted so Phase 3 can decide proxy need from the DB instead of
        # re-running ffprobe over the entire library every ingest (H1: the
        # mid-run ffprobe storm / ingest53 root cause).
        "codec": (video_stream.get("codec_name") or "").lower() or None if video_stream else None,
    }


# audit H11: once-only banner flag — a missing exiftool binary used to be
# swallowed per-file by a bare except, leaving the whole library's camera
# metadata silently NULL with zero signal.
_exiftool_missing_warned = False


def exiftool_extract(path: str, fps: Optional[float] = None) -> dict:
    """Extract EXIF metadata via exiftool -json. Returns dict of 12 fields."""
    global _exiftool_missing_warned
    cmd = [
        config.EXIFTOOL_PATH, "-json",
        "-Make", "-Model", "-LensModel",
        # issue #115: Sony XAVC (A7 V / FX30) leaves the standard Make/Model
        # blank and puts device identity in the embedded XML (NRT) block as
        # DeviceManufacturer / DeviceModelName. Read them in the SAME exiftool
        # call so sidecar-less clips still populate camera_make/model. Non-Sony
        # files simply don't have these tags → d.get() returns None → no effect.
        "-DeviceManufacturer", "-DeviceModelName", "-LensZoomModelName",
        "-GPSLatitude", "-GPSLongitude",
        "-ColorSpace",
        "-ISO",
        "-ReelName", "-CameraReelName", "-Reel#", "-ReelNumber",
        "-ShutterSpeed", "-ExposureTime",
        "-FNumber", "-ApertureValue",
        "-FocalLength",
        "-CreateDate", "-DateTimeOriginal",
        "-Keys:CreationDate",
        # Blackmagic Cam app (iOS) writes per-vendor schema in [Keys] group.
        # Lens info goes to "Blackmagic-design Camera Lens Type" (short form
        # collapses spaces). Other BMD fields (ISO/Aperture/ShutterAngle/WB)
        # not consumed yet — see todo B10b3.
        "-Blackmagic-designCameraLensType",
        "-Blackmagic-designCameraIso",
        "-Blackmagic-designCameraAperture",
        "-Blackmagic-designCameraShutterAngle",
        "-Blackmagic-designCameraWhiteBalanceKelvin",
        "-Blackmagic-designCameraWhiteBalanceTint",
        "-Blackmagic-designCameraEnvironment",
        "-Blackmagic-designCameraDayNight",
        "-n",  # numeric output for GPS
        path,
    ]
    # audit H11: split the old bare `except Exception: return {}` so failures
    # are loud — metadata still degrades gracefully to {}, but with a cause.
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        if r.returncode != 0:
            print("\n    [exiftool rc={0}] {1}".format(
                r.returncode, (r.stderr or "").strip()[:200]))
            return {}
        data = json.loads(r.stdout)
        if not data:
            return {}
        d = data[0]
    except FileNotFoundError:
        if not _exiftool_missing_warned:
            _exiftool_missing_warned = True
            print("\n    [exiftool not found: {0} — camera metadata will be NULL"
                  " for this run]".format(config.EXIFTOOL_PATH))
        return {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print("\n    [exiftool failed: {0}: {1}]".format(type(e).__name__, e))
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
    if not ss_str and d.get("Blackmagic-designCameraShutterAngle") is not None:
        ss_str = _shutter_angle_to_speed(d.get("Blackmagic-designCameraShutterAngle"), fps, path=path)

    # Parse aperture — prefer FNumber
    ap_raw = d.get("FNumber") or d.get("ApertureValue")
    ap = None
    if ap_raw is not None:
        try:
            ap = float(ap_raw)
        except (ValueError, TypeError):
            logger.warning("Aperture parse failed for %s: value=%r", path, ap_raw)
    elif d.get("Blackmagic-designCameraAperture") is not None:
        ap = d.get("Blackmagic-designCameraAperture")

    # Creation date — prefer CreateDate, fallback DateTimeOriginal
    white_balance = None
    if d.get("Blackmagic-designCameraWhiteBalanceKelvin") is not None or d.get("Blackmagic-designCameraWhiteBalanceTint") is not None:
        white_balance = _white_balance_string(
            d.get("Blackmagic-designCameraWhiteBalanceKelvin"),
            d.get("Blackmagic-designCameraWhiteBalanceTint"),
            path=path,
        )

    auto_tags = []
    for tag_value in (
        d.get("Blackmagic-designCameraEnvironment"),
        d.get("Blackmagic-designCameraDayNight"),
    ):
        tag_name = _normalize_bmd_tag(tag_value)
        if tag_name and tag_name not in auto_tags:
            auto_tags.append(tag_name)

    cdate = d.get("CreateDate") or d.get("DateTimeOriginal") or d.get("CreationDate")
    cdate_str = str(cdate) if cdate else None

    return {
        # issue #115: fall back to embedded-XML device identity when the
        # standard EXIF Make/Model are blank (Sony XAVC without an M01.XML
        # sidecar). Standard tags still win when present.
        "camera_make": d.get("Make") or d.get("DeviceManufacturer"),
        "camera_model": d.get("Model") or d.get("DeviceModelName"),
        "lens_model": d.get("LensModel") or d.get("Blackmagic-designCameraLensType") or d.get("LensZoomModelName"),
        "gps_lat": d.get("GPSLatitude"),
        "gps_lon": d.get("GPSLongitude"),
        "color_space": str(d.get("ColorSpace")) if d.get("ColorSpace") else None,
        "iso": d.get("ISO") if d.get("ISO") is not None else d.get("Blackmagic-designCameraIso"),
        "shutter_speed": ss_str,
        "aperture": ap,
        "focal_length": fl,
        "creation_date": cdate_str,
        "reel_name": d.get("ReelName") or d.get("CameraReelName") or d.get("Reel#") or d.get("ReelNumber"),
        "white_balance": white_balance,
        "_auto_tags": auto_tags,
    }


def parse_xavc_sidecar(mp4_path: str) -> dict:
    from pathlib import Path
    import xml.etree.ElementTree as ET

    p = Path(mp4_path)
    sidecar = p.with_name(p.stem + "M01.XML")
    if not sidecar.exists():
        return {}
    try:
        tree = ET.parse(sidecar)
        root = tree.getroot()
        ns = {"x": root.tag.split("}")[0].lstrip("{")} if "}" in root.tag else {}
    except (ET.ParseError, OSError):
        return {}

    def find_attr(xpath_with_x, xpath_plain, attr):
        if ns:
            el = root.find(xpath_with_x, ns)
        else:
            el = root.find(xpath_plain)
        return el.get(attr) if el is not None else None

    return {
        "camera_make": find_attr(".//x:Device", ".//Device", "manufacturer"),
        "camera_model": find_attr(".//x:Device", ".//Device", "modelName"),
        "lens_model": find_attr(".//x:Lens", ".//Lens", "modelName"),
        "creation_date": find_attr(".//x:CreationDate", ".//CreationDate", "value"),
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
        # fable-audit round-5 #1: a truncated proxy from a prior kill/power-loss is
        # otherwise served forever. Treat an empty file as absent so it gets rebuilt.
        if proxy_path.stat().st_size > 0:
            return str(proxy_path)
        proxy_path.unlink(missing_ok=True)
    if force:
        proxy_path.unlink(missing_ok=True)
    # Encode to a same-dir tmp with the REAL .mp4 suffix, then os.replace onto the
    # final path only after a clean, non-empty encode (mirrors frames.py _extract_to).
    # A kill/power-loss mid-encode then leaves the tmp — never a half-written final
    # that every consumer would accept by bare existence (fable-audit round-5 #1).
    # Tmp keeps .mp4 (codex footgun: ffmpeg infers the muxer from the extension) and
    # sits beside the final so os.replace stays on one filesystem.
    tmp_path = proxy_path.with_name("{0}.tmp.{1}{2}".format(proxy_path.stem, os.getpid(), proxy_path.suffix))
    tmp_path.unlink(missing_ok=True)
    cmd = [
        config.FFMPEG_PATH, "-y", "-i", path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "28",
        "-profile:v", "high", "-level:v", "4.0",
        "-pix_fmt", "yuv420p",
        "-g", "30",
        "-vf", "scale=-2:720",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(tmp_path),
    ]
    try:
        # encoding pinned: Windows zh-TW default cp950 can't decode ffmpeg's
        # utf-8 stderr → UnicodeDecodeError crashes proxy gen on headless ingest.
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
        if r.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
            os.replace(str(tmp_path), str(proxy_path))
            return str(proxy_path)
        tmp_path.unlink(missing_ok=True)
        # fable-audit round-5 #60: say WHY (ffmpeg-missing vs disk-full vs bad codec)
        # instead of a silent None that reads as a mystery 409 downstream.
        tail = (r.stderr or "")[-300:]
        print("[proxy] ffmpeg rc={0} for {1}: {2}".format(r.returncode, path, tail))
        return None
    except FileNotFoundError as exc:  # ffmpeg binary itself missing — distinct failure
        tmp_path.unlink(missing_ok=True)
        print("[proxy] ffmpeg not found ({0}); check FFMPEG_PATH".format(exc))
        return None
    except subprocess.TimeoutExpired:
        tmp_path.unlink(missing_ok=True)
        print("[proxy] timeout after 600s: {0}".format(path))
        return None
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        print("[proxy] failed {0}: {1}: {2}".format(path, type(exc).__name__, exc))
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


def _get_media_id_for_path(path: Path, _conn=None) -> Optional[int]:
    abs_path, rel_path = _db_path_params(path)

    def _do(c):
        row = c.execute(
            "SELECT id FROM media WHERE path=? OR path=?",
            (abs_path, rel_path),
        ).fetchone()
        return row[0] if row else None

    if _conn is not None:
        return _do(_conn)
    with db.get_conn() as conn:
        return _do(conn)


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


def process_file(path: Path, skip_vision: bool, existing: Optional[Dict] = None, refresh: bool = False) -> Dict:
    """
    Process one media file.
    If `existing` is provided (refresh mode), skip transcription and reuse existing
    transcript/lang — only re-run thumbnail + vision.
    """
    _stage("probe", "probe")
    meta = probe(str(path))
    if meta is None:
        print(" [ffprobe failed]")
        return {}

    exif = exiftool_extract(str(path), fps=meta.get("fps") or (existing.get("fps") if existing else None))
    sidecar = parse_xavc_sidecar(str(path))
    for k, v in sidecar.items():
        if v and not exif.get(k):
            exif[k] = v

    record = {
        "path": db.to_relative(str(path)),
        "filename": path.name,
        "ext": path.suffix.lower(),
        **meta,
        **exif,
        "transcript": existing.get("transcript") if existing else None,
        "lang": existing.get("lang") if existing else None,
        # frame_tags / thumbnail_path are intentionally NOT seeded here. db.upsert
        # is column-subset, so omitting them means a refresh whose vision or
        # thumbnail step fails leaves the prior values intact instead of nulling
        # media.frame_tags (search text) / thumbnail_path (H6). They are added to
        # the record below / in Phase 2 only when a real value exists.
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Audio transcription (skip on refresh — reuse existing)
    if meta["has_audio"] and not existing:
        _stage("whisper", "transcribe")
        text, lang, segments, words = tr.transcribe(str(path), language=_LANGUAGE_OVERRIDE)
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
        _stage("thumb", "thumbnail")
        thumb_path = frm.extract_thumbnail(str(path), meta["duration_s"], force=refresh)
        # Only record a thumbnail when extraction succeeded — omitting the key on
        # failure preserves the prior value on refresh (H6) and leaves new rows
        # at the column default (NULL).
        if thumb_path:
            record["thumbnail_path"] = db.to_relative(thumb_path)

    # Frame extraction (video only) — persistent thumbnails + DB records
    if is_video and meta["duration_s"] > 0:
        _stage("frames", "frames")
        frame_data = frm.extract_frames(str(path), meta["duration_s"], meta["fps"] or 30, force=refresh)
        for frame in frame_data:
            if frame.get("thumbnail_path"):
                frame["thumbnail_path"] = db.to_relative(frame["thumbnail_path"])
        record["_frames"] = frame_data  # pass to caller for DB insert

        # Vision description (optional)
        if not skip_vision and frame_data:
            _stage("llava", "vision")
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

    if not _STAGE_EVENTS:
        print(" [OK]")
    return record


def _run_queue_cmd(args):
    """Phase 11.5c: `--queue status|cancel|retry`."""
    import jobs

    if args.queue == "status":
        c = jobs.counts()
        print("Job queue: " + "  ".join("{0}={1}".format(k, v) for k, v in c.items()))
        active = jobs.list_jobs()
        if not active:
            print("  (no jobs)")
            return
        print("  {0:>5}  {1:<10} {2:<10} {3:<9} {4}".format("id", "type", "status", "priority", "target"))
        for j in active:
            print("  {0:>5}  {1:<10} {2:<10} {3:<9} {4}".format(
                j["id"], j["type"], j["status"], j["priority"], j.get("target") or ""
            ))
        return

    # cancel / retry need a job id
    if not args.job_id:
        print("--queue {0} requires --job-id N".format(args.queue))
        sys.exit(2)
    fn = jobs.cancel if args.queue == "cancel" else jobs.retry
    ok = fn(args.job_id)
    if ok:
        print("Job {0} {1}{2}.".format(args.job_id, args.queue, "ed" if args.queue == "cancel" else "→pending"))
    else:
        print("Job {0}: cannot {1} (absent or wrong state).".format(args.job_id, args.queue))
        sys.exit(1)


def _run_status_cmd(args):
    """Phase 11.5e: `arkiv status` — resource probe + queue depth + ETA hint."""
    import resource_probe as rp
    import jobs

    active = jobs.active_count()
    result = rp.probe(active_jobs=active)
    qc = jobs.counts()

    if getattr(args, "json", False):
        print(json.dumps({"resource": result, "queue": qc}, ensure_ascii=False))
        return

    print(rp.summary_line(result))
    print("queue: " + "  ".join("{0}={1}".format(k, v) for k, v in qc.items()))
    decision, reason = rp.decide(result)
    print("next vision phase would: {0} ({1})".format(decision, reason))


# issue #48: vision failure tolerance. A frame is "failed" when both the primary
# and fallback model leave its description empty. Historically a single failed
# frame halted the whole run (zero-tolerance) — fine interactively, fragile for a
# 481-frame overnight run where one transient Ollama hiccup kills the night.
#   --max-failures N : tolerate N cumulative failed frames, halt once exceeded
#                      (N=0, the default, preserves the historical behaviour).
#   --skip-failed    : never halt on frame failures; leave them empty (so a later
#                      --vision-only re-picks them) and report the list at the end.
# A consecutive-failure guard fires regardless of the above: this many frames
# failing in a row (whole files producing nothing) means Ollama is down, so we
# halt fast instead of spinning all night writing the same error.
_VISION_CONSECUTIVE_HALT = 20


def _describe_frames_with_fallback(frame_paths):
    """Primary vision model, then minicpm-v fallback for any failed frame.

    Returns (frame_results, still_failed_indices). Shared by --vision-only and the
    main Phase-2 loop so both get identical describe → fallback → tolerance logic.
    """
    frame_results = vis.describe_frames(frame_paths)
    failed_indices = [i for i, vr in enumerate(frame_results) if vr.get("error") or not vr.get("description")]
    if failed_indices:
        fallback_model = config.VISION_FALLBACK_MODEL
        if not fallback_model or not vis.model_available(fallback_model):
            # Graceful: don't hammer Ollama with a 404 per failed frame for a
            # fallback model that isn't installed — leave them empty for a later
            # --vision-only retry (issue #48 tolerance still applies).
            print(f" [Phase 1: {len(failed_indices)} failed, fallback '{fallback_model or 'none'}' 未安裝 → 跳過]", end="", flush=True)
        else:
            print(f" [Phase 1: {len(failed_indices)} failed, trying fallback {fallback_model}]", end="", flush=True)
            original_model = vis.VISION_MODEL
            try:
                vis.VISION_MODEL = fallback_model
                retry_results = vis.describe_frames([frame_paths[i] for i in failed_indices])
                for idx, retry_r in zip(failed_indices, retry_results):
                    if retry_r.get("description") and not retry_r.get("error"):
                        frame_results[idx] = retry_r
            finally:
                vis.VISION_MODEL = original_model
    still_failed = [i for i, vr in enumerate(frame_results) if vr.get("error") or not vr.get("description")]
    return frame_results, still_failed


def _vision_halt_decision(file_failed, file_total, total_failed, consecutive_failed,
                          max_failures, skip_failed):
    """Decide whether to halt after a file with `file_failed`/`file_total` still-failing
    frames. Returns (should_halt, reason); reason is "" when continuing.

    Order matters: the consecutive-failure guard is checked first and ignores the
    tolerance flags — an Ollama outage must stop an unattended run even under
    --skip-failed.
    """
    if consecutive_failed >= _VISION_CONSECUTIVE_HALT:
        return True, "{0} consecutive frame failures — Ollama likely down / model crash".format(consecutive_failed)
    if skip_failed:
        return False, ""
    if total_failed > max_failures:
        return True, "{0} failed frame(s) exceeded --max-failures={1}".format(total_failed, max_failures)
    return False, ""


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
    _ensure_vision_ready()

    max_failures = getattr(args, "max_failures", 0) or 0
    skip_failed = getattr(args, "skip_failed", False)
    ok, halted = 0, False
    total_failed, consecutive_failed = 0, 0
    failed_files = []  # (fname, failed_count, frame_count) for the end-of-run report
    for vi, (mid, frames_list) in enumerate(media_frames.items(), 1):
        fname = frames_list[0]["filename"]
        frame_paths = [db.resolve_path(f["thumbnail_path"]) for f in frames_list]

        print(f"[{vi}/{len(media_frames)}] {fname} ({len(frame_paths)} frames) >vision", end="", flush=True)
        v_start = _time.time()

        frame_results, still_failed_idx = _describe_frames_with_fallback(frame_paths)

        # Commit now — successful frames get descriptions; failed frames keep their
        # empty description (so a later --vision-only re-picks exactly them). Work is
        # never lost, even on a file we may halt after.
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
        n_failed = len(still_failed_idx)
        if n_failed:
            total_failed += n_failed
            # Only a *whole* file producing nothing counts toward the Ollama-down
            # streak; a partial failure (some frames succeeded) resets it.
            consecutive_failed = consecutive_failed + n_failed if n_failed == len(frame_paths) else 0
            failed_files.append((fname, n_failed, len(frame_paths)))
            print(f" [{v_elapsed:.1f}s] [{n_failed}/{len(frame_paths)} FAILED]")
            should_halt, reason = _vision_halt_decision(
                n_failed, len(frame_paths), total_failed, consecutive_failed, max_failures, skip_failed)
            if should_halt:
                remaining = len(media_frames) - vi
                print(f"\n\n{'!'*60}")
                print(f"VISION HALTED: {reason}")
                print(f"  Completed: {ok}  |  Remaining: {remaining}  |  Failed frames: {total_failed}")
                print(f"  Fix Ollama, then resume: py -3.12 ingest.py --vision-only")
                print(f"  (skip persistent failures: add --skip-failed)")
                print(f"{'!'*60}\n")
                halted = True
                break
        else:
            consecutive_failed = 0
            print(f" [{v_elapsed:.1f}s] [OK]")
            ok += 1

    if failed_files:
        total = sum(n for _, n, _ in failed_files)
        print(f"\n⚠ {total} frame(s) across {len(failed_files)} file(s) left empty (re-run --vision-only to retry just those):")
        for fn, n, tot in failed_files[:20]:
            print(f"    {fn}: {n}/{tot}")
        if len(failed_files) > 20:
            print(f"    … and {len(failed_files) - 20} more file(s)")
    if not halted:
        suffix = f", {len(failed_files)} with skipped frames." if failed_files else "."
        print(f"\nVision-only done. {ok} file(s) fully patched{suffix}")
    return halted


def _regenerate_proxies():
    """Rebuild all existing proxies with latest encoding settings."""
    proxy_dir = config.PROXIES_DIR
    existing = sorted(proxy_dir.glob("*.mp4")) if proxy_dir.exists() else []
    if not existing:
        print("No existing proxies to regenerate.")
        return

    print(f"Regenerating {len(existing)} proxies...")
    size_before = _dir_size_bytes(proxy_dir)  # B6: report net size change
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
    delta = _dir_size_bytes(proxy_dir) - size_before
    print(f"\nRegenerated: {ok}  Failed: {failed}  (size delta: {_fmt_size_delta(delta)})")


def _dir_size_bytes(path) -> int:
    """Total size of files directly in `path` (non-recursive is fine — proxy/
    thumbnail dirs are flat). Missing dir → 0."""
    p = Path(path)
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.glob("*") if f.is_file())


def _fmt_size_delta(delta_bytes: int) -> str:
    sign = "+" if delta_bytes >= 0 else "-"
    return f"{sign}{abs(delta_bytes) / 1_000_000:.1f} MB"


def _regenerate_thumbnails():
    """B4: rebuild the poster thumbnail for every video record (mirror of
    --regenerate-proxies). Re-runs extract_thumbnail and updates
    media.thumbnail_path. Frame thumbnails (which carry vision descriptions) are
    intentionally left untouched — use --vision-only / --refresh for those."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path, filename FROM media").fetchall()
        records = [dict(r) for r in rows]
    videos = [r for r in records if Path(r["path"]).suffix.lower() in VIDEO_EXT]
    if not videos:
        print("No video records to regenerate thumbnails for.")
        return

    print(f"Regenerating thumbnails for {len(videos)} video records...")
    size_before = _dir_size_bytes(config.THUMBNAILS_DIR)
    ok, failed = 0, 0
    for idx, rec in enumerate(videos, 1):
        try:
            src = db.resolve_path(rec["path"])
        except ValueError:
            # Poisoned/cross-OS row whose path escapes PROJECT_ROOT — treat as a
            # missing source and skip, never crash the whole regen pass.
            print(f"  [{idx}/{len(videos)}] id={rec['id']}: unresolvable path, skipping")
            failed += 1
            continue
        if not Path(src).exists():
            print(f"  [{idx}/{len(videos)}] id={rec['id']}: source missing, skipping")
            failed += 1
            continue
        meta = probe(src)
        dur = meta.get("duration_s", 0) if meta else 0
        print(f"  [{idx}/{len(videos)}] id={rec['id']} {rec['filename']}", end="", flush=True)
        thumb = frm.extract_thumbnail(src, dur, force=True) if dur > 0 else None
        if thumb:
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE media SET thumbnail_path=? WHERE id=?",
                    (db.to_relative(thumb), rec["id"]),
                )
            print(" [OK]")
            ok += 1
        else:
            print(" [FAIL]")
            failed += 1
    delta = _dir_size_bytes(config.THUMBNAILS_DIR) - size_before
    print(f"\nRegenerated: {ok}  Failed: {failed}  (size delta: {_fmt_size_delta(delta)})")


def _migrate_storage():
    """Phase 8.0c migration: BASE_DIR/{media.db, thumbnails/, chroma_db/, proxies/}
    → BASE_DIR/.arkiv/{project.db, thumbnails/, chroma_db/, proxies/}.

    Idempotent: refuses if new layout already exists.
    Backup-first: writes BASE_DIR/.legacy-backup-{ts}.tar.gz before any move.
    Verify-after: sqlite COUNT + thumbnails file count cross-check.
    """
    import tarfile
    import sqlite3

    base = config.BASE_DIR
    arkiv_dir = base / ".arkiv"

    pairs = [
        (base / "media.db", arkiv_dir / "project.db", "DB"),
        (base / "thumbnails", arkiv_dir / "thumbnails", "thumbnails"),
        (base / "chroma_db", arkiv_dir / "chroma_db", "chroma_db"),
        (base / "proxies", arkiv_dir / "proxies", "proxies"),
    ]

    # Idempotency check
    new_db = arkiv_dir / "project.db"
    if new_db.exists():
        print(f"[SKIP] {new_db} 已存在 — migration 已跑過。")
        print(f"       如要重跑：先 rm -rf {arkiv_dir} 再執行。")
        return

    # What's actually movable? (skip symlinks — they're workarounds, leave them)
    to_move = [
        (src, dst, name) for src, dst, name in pairs
        if src.exists() and not src.is_symlink()
    ]

    if not to_move:
        print(f"[INFO] BASE_DIR ({base}) 沒有 legacy storage 要搬。")
        print(f"       建立空 {arkiv_dir}/ 供新 ingest 使用。")
        arkiv_dir.mkdir(parents=True, exist_ok=True)
        # Also clean dangling symlinks (e.g. 5/15 thumbnails workaround)
        for src, _, name in pairs:
            if src.is_symlink():
                target = src.resolve(strict=False)
                if not target.exists():
                    print(f"       拆 dangling symlink: {src} -> {target}")
                    src.unlink()
        return

    # Backup
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = base / f".legacy-backup-{ts}.tar.gz"
    print(f"[1/3] Backup → {backup_path}")
    try:
        with tarfile.open(backup_path, "w:gz") as tar:
            for src, _, name in to_move:
                tar.add(src, arcname=src.name)
                print(f"        + {name}: {src.name}")
    except OSError as e:
        print(f"[FATAL] backup failed: {e}")
        sys.exit(5)

    # Pre-move counts for verification
    pre_db_count = 0
    pre_thumb_count = 0
    legacy_db = base / "media.db"
    legacy_thumbs = base / "thumbnails"
    if legacy_db.exists():
        try:
            conn = sqlite3.connect(str(legacy_db))
            pre_db_count = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
            conn.close()
        except sqlite3.Error:
            pass
    if legacy_thumbs.exists() and legacy_thumbs.is_dir():
        pre_thumb_count = sum(1 for _ in legacy_thumbs.iterdir())

    # Move
    print(f"[2/3] Move → {arkiv_dir}/")
    arkiv_dir.mkdir(parents=True, exist_ok=True)
    for src, dst, name in to_move:
        print(f"        {src.name} → {dst.relative_to(base)}")
        shutil.move(str(src), str(dst))

    # Also clean any dangling symlinks left behind
    for src, _, name in pairs:
        if src.exists() and src.is_symlink():
            target = src.resolve(strict=False)
            if not target.exists():
                print(f"        拆 dangling symlink: {src.name} -> {target}")
                src.unlink()

    # Verify
    print(f"[3/3] Verify")
    if new_db.exists():
        try:
            conn = sqlite3.connect(str(new_db))
            post_db_count = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
            conn.close()
            ok_mark = "[OK]" if post_db_count == pre_db_count else f"[FAIL] (pre={pre_db_count} post={post_db_count})"
            print(f"        media rows: {post_db_count} {ok_mark}")
        except sqlite3.Error as e:
            print(f"        [WARN] sqlite check failed: {e}")
    new_thumbs = arkiv_dir / "thumbnails"
    if new_thumbs.exists():
        post_thumb_count = sum(1 for _ in new_thumbs.iterdir())
        ok_mark = "[OK]" if post_thumb_count == pre_thumb_count else f"[FAIL] (pre={pre_thumb_count} post={post_thumb_count})"
        print(f"        thumbnails: {post_thumb_count} {ok_mark}")

    print(f"\n[DONE] Storage migrated → {arkiv_dir}")
    print(f"       Backup: {backup_path}")
    print(f"       Rollback (if needed):")
    print(f"         rm -rf {arkiv_dir} && tar xzf {backup_path} -C {base}")


_CANON_PROMPT = (
    "以下是同一支影片的標籤，可能有同義詞或同概念不同詞（例：生肉/生魚/肉類、夜間/夜景）。"
    "請把同義或同概念的合併成一個，**只能從原標籤清單裡選一個最通用的詞代表，"
    "絕對不可創造清單以外的新詞**；不同概念一律保留。回傳 {\"tags\":[...]}，只輸出 JSON。\n原標籤："
)


def _run_canonicalize_tags(args):
    """Populate media.canonical_tags via one LLM semantic-merge per media. Reads
    the raw vision tags (rank_media_tags over frame_tags), asks the chat model to
    merge synonyms picking only existing words, guards the result (no invention /
    no over-merge → falls back to raw), and stores it SEPARATELY from the raw
    tags. Skips media already canonicalized. No re-vision, no footage needed."""
    import json as _json
    import llm
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, frame_tags FROM media WHERE frame_tags IS NOT NULL AND canonical_tags IS NULL"
        ).fetchall()
    print("Canonicalize tags: {0} media pending".format(len(rows)))
    processed = changed = failed = 0
    for r in rows:
        try:
            frames = _json.loads(r["frame_tags"]) or []
        except Exception:
            continue
        raw = tag_quality.rank_media_tags(frames)
        if not raw:
            db.set_canonical_tags(r["id"], [])
            continue
        try:
            resp = llm.chat(_CANON_PROMPT + _json.dumps(raw, ensure_ascii=False), json_mode=True)
            proposed = _json.loads(resp["text"]).get("tags", [])
        except Exception as e:
            failed += 1
            print("  media {0}: LLM 失敗 ({1}) → 跳過".format(r["id"], e))
            continue
        clean = tag_quality.guard_canonical(raw, proposed)
        db.set_canonical_tags(r["id"], clean)
        processed += 1
        if clean != raw:
            changed += 1
            print("  media {0}: {1} → {2}".format(r["id"], raw, clean))
    print("Done. {0} processed, {1} changed, {2} failed.".format(processed, changed, failed))


# ── Library-level alias-map proposal (embed → cluster → LLM judge → review) ──
_ALIAS_JUDGE_PROMPT = (
    "以下是一組語意相近的標籤候選。請判斷哪些是「同義或同概念」該合併、哪些其實是不同概念要分開。"
    "每個要合併的概念，從候選清單裡選一個最通用的詞當代表(pref)，其餘同義詞放 alts。"
    "不同概念各自獨立；若整組其實都不同概念就回空陣列。"
    "**只能用清單裡出現的詞，絕對不可創造新詞，也不要把不同距離/不同事物硬合(例：路跑≠馬拉松)**。"
    "回傳 {\"groups\":[{\"pref\":\"...\",\"alts\":[\"...\"]}]}，只輸出 JSON。\n候選標籤："
)


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _cluster_by_cosine(names, vectors, threshold):
    """Union-find: tags within `threshold` cosine of each other land in one group.
    Returns candidate groups of size >= 2 (loose on purpose — the LLM is the real
    gate that splits/rejects; clustering only needs high recall to not miss pairs)."""
    n = len(names)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _cosine(vectors[i], vectors[j]) >= threshold:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(names[i])
    return [g for g in groups.values() if len(g) >= 2]


def _run_propose_aliases(args):
    """Propose a library-level tag alias map. Pulls the global tag cloud (already
    variant-merged + noise-filtered), embeds every tag with bge-m3, clusters by
    cosine to PROPOSE near-synonym groups, then asks the local chat LLM to judge
    each cluster (pick a preferred term, confirm true synonyms, split distinct
    concepts). Guards every group against invention. Writes a reviewable proposal
    to .arkiv/tag_aliases.proposed.json — NOT applied until you review +
    --apply-aliases. Non-destructive: raw tags are never touched."""
    import json as _json
    import llm
    import vectordb
    threshold = getattr(args, "alias_threshold", 0.80) or 0.80
    records = tag_quality.merge_tag_records(db.get_all_tag_names())
    names = [r["name"] for r in records]
    print("Propose aliases: {0} distinct tags, cosine threshold {1}".format(len(names), threshold))
    if len(names) < 2:
        print("Too few tags — nothing to propose.")
        return
    try:
        vectors = vectordb.embed_batch(names)
    except Exception as e:
        print("  batch embed failed ({0}) → per-tag embed".format(e))
        vectors = [llm.embed(t) for t in names]
    clusters = _cluster_by_cosine(names, vectors, threshold)
    print("  {0} candidate clusters (size>=2) → LLM judging".format(len(clusters)))
    out_groups = []
    for cl in clusters:
        cl_set = set(cl)
        try:
            resp = llm.chat(_ALIAS_JUDGE_PROMPT + _json.dumps(cl, ensure_ascii=False), json_mode=True)
            proposed = _json.loads(resp["text"]).get("groups", [])
        except Exception as e:
            print("  cluster {0}: LLM 失敗 ({1}) → 跳過".format(cl, e))
            continue
        for g in proposed:
            pref = tag_quality.canonicalize(g.get("pref") or "")
            alts = [tag_quality.canonicalize(a) for a in (g.get("alts") or [])]
            # guard: pref + every alt must be a real tag from THIS cluster (no
            # invention, no cross-cluster bleed); need >=1 alt or there's no merge.
            alts = [a for a in alts if a and a in cl_set and a != pref]
            if pref in cl_set and alts:
                out_groups.append({"pref": pref, "alts": alts})
                # A group folding many alts is the over-merge risk — flag it so
                # review scrutinizes (馬拉松≠路跑 class errors). Human gate decides.
                warn = " ⚠ 多，請複查" if len(alts) >= 5 else ""
                print("    {0}  ←  {1}{2}".format(pref, "/".join(alts), warn))
    payload = {"version": 1, "groups": out_groups}
    config.TAG_ALIASES_PROPOSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.TAG_ALIASES_PROPOSED_PATH.write_text(
        _json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    merged = sum(len(g["alts"]) for g in out_groups)
    print("\nProposed {0} merge groups (folds {1} tags) → {2}".format(
        len(out_groups), merged, config.TAG_ALIASES_PROPOSED_PATH))
    print("Review/edit that file, then: python ingest.py --apply-aliases")


def _run_apply_aliases(args):
    """Activate the reviewed proposal: validate shape, then copy
    tag_aliases.proposed.json → tag_aliases.json (the live map /api/tags reads).
    Reversible — delete tag_aliases.json to restore the unfolded cloud."""
    import json as _json
    src = config.TAG_ALIASES_PROPOSED_PATH
    if not src.exists():
        print("No proposal at {0}. Run --propose-aliases first.".format(src))
        return
    try:
        data = _json.loads(src.read_text(encoding="utf-8"))
        groups = data.get("groups", [])
        clean = []
        for g in groups:
            pref = (g.get("pref") or "").strip()
            alts = [a.strip() for a in (g.get("alts") or []) if a and a.strip() and a.strip() != pref]
            if pref and alts:
                clean.append({"pref": pref, "alts": alts})
    except (ValueError, OSError) as e:
        print("Proposal malformed ({0}) — not applying.".format(e))
        return
    config.TAG_ALIASES_PATH.write_text(
        _json.dumps({"version": 1, "groups": clean}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Applied {0} alias groups → {1}".format(len(clean), config.TAG_ALIASES_PATH))
    print("Tag cloud will fold on next /api/tags (map auto-reloads on file change).")


def main():
    # Windows: the console codepage (cp950 on zh-TW) can't encode chars the CLI
    # prints (⚠, →, emoji) → UnicodeEncodeError crashes mid-run. Force UTF-8 on
    # stdout/stderr so progress output is robust on every platform.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Ingest media files into SQLite DB")
    parser.add_argument("--dir", help="Media directory to scan (required unless --migrate-* / --regenerate-proxies / --vision-only / --files)")
    parser.add_argument("--files", nargs="+", metavar="F", help="Ingest an explicit list of files instead of scanning a --dir. Used by the cross-library 精選集 'copy into project' flow to index clips gathered from several source libraries in one run (one model warmup). Files outside PROJECT_ROOT store an absolute media.path (still resolvable).")
    parser.add_argument("--limit", type=int, default=0, help="Max files to process (0=all)")
    parser.add_argument("--skip-vision", action="store_true", help="Skip llava frame description")
    parser.add_argument("--refresh", action="store_true", help="Re-process already-indexed files — re-extracts thumbnail + frames (not reusing cached ones, so changed extraction logic like 360 reproject re-applies) + re-runs vision (issue #53)")
    parser.add_argument("--vision-only", action="store_true", help="Only run vision on frames with empty descriptions (resume after halt)")
    parser.add_argument("--canonicalize-tags", action="store_true", help="Populate media.canonical_tags via one LLM semantic-merge per media (生肉/生魚/肉類→肉類). Non-destructive (raw tags untouched, stored separately for the UI toggle). Skips media already canonicalized. No --dir / no re-vision needed.")
    parser.add_argument("--propose-aliases", action="store_true", help="Library-level tag dedup: embed the global tag cloud (bge-m3) → cluster near-synonyms → LLM judges each group (运动会/比赛→赛事) → writes a REVIEWABLE proposal to .arkiv/tag_aliases.proposed.json. Not applied until --apply-aliases. Non-destructive.")
    parser.add_argument("--apply-aliases", action="store_true", help="Activate the reviewed proposal: copy tag_aliases.proposed.json → tag_aliases.json (the live map /api/tags folds the cloud by). Reversible — delete tag_aliases.json to restore.")
    parser.add_argument("--alias-threshold", type=float, default=0.80, metavar="F", help="With --propose-aliases: cosine threshold for clustering candidate synonyms (default 0.80 — bge-m3 is anisotropic so related terms all score high; below ~0.75 every tag collapses into one blob. Raise toward 0.85 for tighter groups).")
    parser.add_argument("--max-failures", type=int, default=0, metavar="N", help="issue #48: tolerate N cumulative failed frames before halting vision (0=halt on first, the default). Failed frames are left empty for a later --vision-only retry.")
    parser.add_argument("--skip-failed", action="store_true", help="issue #48: never halt on individual frame vision failures — skip them (left empty for retry), report at end. Recommended for large unattended/overnight runs. A whole-Ollama outage still halts fast.")
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
    parser.add_argument(
        "--regenerate-thumbnails",
        action="store_true",
        help="B4: 重建所有影片記錄的封面縮圖（不動 frame vision）",
    )
    parser.add_argument(
        "--migrate-storage",
        action="store_true",
        help="Phase 8.0c: 搬 legacy BASE_DIR/{media.db, thumbnails/, chroma_db/, proxies/} → BASE_DIR/.arkiv/",
    )
    parser.add_argument("--whisper-guard", type=int, default=None, metavar="MODE", help="brick 4: transcription quality preset 0-4 (0 baseline … 4 +LLM polish, the default). Omit = config default / ARKIV_WHISPER_GUARD_LAYERS env.")
    parser.add_argument("--language", default=None, metavar="LANG", help="brick 4: force whisper language code (zh/en/ja/ko); omit = auto-detect / preset hint.")
    parser.add_argument("--recursive", "-r", action="store_true", help="Recursively scan subdirectories")
    parser.add_argument("--db", default="", help="Path to SQLite DB (default: media.db next to ingest.py)")
    parser.add_argument("--no-embed", action="store_true", help="Skip building the vector index after ingest (default: auto-embed so search/chat work immediately)")
    parser.add_argument("--queue", choices=["status", "cancel", "retry"], help="Phase 11.5c job queue: status | cancel --job-id N | retry --job-id N")
    parser.add_argument("--job-id", type=int, default=0, help="Job id for --queue cancel/retry")
    parser.add_argument("--status", action="store_true", help="Phase 11.5e: print resource + queue status (--json for machine-readable)")
    parser.add_argument("--json", action="store_true", help="With --status: emit JSON instead of human-readable")
    args = parser.parse_args()

    # brick 4: apply the per-run whisper preset + language override before any
    # transcription. Guard-mode resolution keeps its env-wins precedence (an
    # ARKIV_WHISPER_GUARD_LAYERS env still overrides the flag, as in the
    # transcribe CLI); language is a direct override read at the transcribe call.
    if args.whisper_guard is not None:
        tr._apply_whisper_guard_mode(tr._resolve_whisper_guard_mode(args.whisper_guard))
    global _LANGUAGE_OVERRIDE
    _LANGUAGE_OVERRIDE = args.language or None

    # --dir validation: required only when actually ingesting
    maintenance_mode = (
        args.migrate_storage or args.migrate_relative
        or args.regenerate_proxies or args.regenerate_thumbnails or args.vision_only
        or args.canonicalize_tags
        or args.propose_aliases or args.apply_aliases
        or bool(args.queue) or args.status
    )
    if not maintenance_mode and not args.dir and not args.files:
        parser.error("--dir (or --files) is required unless using --migrate-storage / --migrate-relative / --regenerate-proxies / --vision-only")

    if args.db:
        db.DB_PATH = Path(args.db)

    # --migrate-storage runs BEFORE db.init_db() because it creates the
    # storage layout (BASE_DIR/.arkiv/) that init_db needs. Other maintenance
    # modes operate on an already-initialized DB.
    if args.migrate_storage:
        _migrate_storage()
        return

    # Phase 8.0e: pre-flight storage check before any pipeline work.
    # Skip for maintenance modes (they're the tools that fix broken state).
    if not (args.migrate_relative or args.regenerate_proxies or args.regenerate_thumbnails or args.queue or args.status or args.canonicalize_tags or args.propose_aliases or args.apply_aliases):
        import health
        ok_pf, errors_pf = health.preflight_paths()
        if not ok_pf:
            print("\n[FATAL] Storage preflight 失敗：")
            for e in errors_pf:
                print(f"  - {e}")
            print("\n        修法後重跑；或先跑 --migrate-storage 落 Phase 8.0c layout。")
            sys.exit(4)

    db.init_db()

    if args.queue:
        _run_queue_cmd(args)
        return

    if args.status:
        _run_status_cmd(args)
        return

    if args.migrate_relative:
        db.migrate_to_relative()
        return

    if args.regenerate_proxies:
        _regenerate_proxies()
        return

    if args.regenerate_thumbnails:
        _regenerate_thumbnails()
        return

    # ── Vision-only mode: patch missing vision descriptions ──────────────
    if args.vision_only:
        halted = _run_vision_only(args)
        if halted:
            sys.exit(1)  # halt mid-patch must not report success to cron/watch
        return

    # ── Canonicalize tags: LLM semantic-merge into media.canonical_tags ──
    if args.canonicalize_tags:
        _run_canonicalize_tags(args)
        return

    # ── Library-level alias map: propose / apply (global tag-cloud dedup) ──
    if args.propose_aliases:
        _run_propose_aliases(args)
        return
    if args.apply_aliases:
        _run_apply_aliases(args)
        return

    # Warm up models before batch processing
    print("Warming up models...", flush=True)
    tr.warm_up()
    tr.warm_up_ollama()
    print("")

    # audit M7: a relative --dir (e.g. `--dir clips`) used to flow cwd-relative
    # paths into the DB — a third path form besides abs/rel-to-PROJECT_ROOT,
    # breaking dedupe and resolve_path. Canonicalize before any DB interaction.
    if args.files:
        # Explicit file list (精選集 copy-into-project) — resolve + filter to
        # SUPPORTED, dedup preserving order, then fall through to the same batch
        # pipeline as --dir. Missing files are dropped with a note (the copy
        # orchestrator already gated reachability, but be defensive).
        files = []
        seen = set()
        for raw in args.files:
            fp = Path(raw).expanduser().resolve()
            key = str(fp)
            if key in seen:
                continue
            seen.add(key)
            if fp.suffix.lower() not in SUPPORTED:
                print(f"Skip (unsupported): {fp}")
                continue
            if not fp.exists():
                print(f"Skip (missing): {fp}")
                continue
            files.append(fp)
    else:
        media_dir = Path(args.dir).expanduser().resolve()
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

    # audit M26: load the known-path set ONCE for the whole batch —
    # db.is_processed() opens a fresh connection per file, so a 5000-file dir
    # cost 5000+ connections just to build a skip set. Same abs-OR-rel
    # semantics as is_processed.
    with db.get_conn() as conn:
        _known_paths = {r[0] for r in conn.execute("SELECT path FROM media").fetchall()}

    def _is_known(p) -> bool:
        return str(p) in _known_paths or db.to_relative(str(p)) in _known_paths

    if args.limit and not args.refresh:
        # Filter out already-processed files before applying limit
        new_files = [f for f in files if not _is_known(f)]
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
    refreshed_ids = set()  # already-indexed ids re-processed this run → force re-embed
    bench_log = []  # per-file benchmark records
    batch_start = _time.time()

    # ── Phase 1: Probe + Whisper + LLM polish (skip vision) ────────────────
    # On VRAM-limited GPUs (e.g. RTX 4070 12GB), qwen2.5:14b (LLM polish)
    # and qwen3-vl:8b (vision) cannot coexist. Run transcription first,
    # then unload LLM and run vision in a separate pass.
    need_vision = not args.skip_vision
    phase1_results = {}  # path -> (record, frames)

    for i, f in enumerate(files, 1):
        already = _is_known(f)  # audit M26: set lookup, not a per-file connection
        if already and not args.refresh:
            print(f"[{i}/{len(files)}] SKIP {f.name}")
            skipped += 1
            continue

        existing = None
        if already and args.refresh:
            existing = _get_media_row_for_path(f)

        if _STAGE_EVENTS:
            _emit_progress({"t": "file", "index": i, "total": len(files), "file": f.name, "status": "start"})
        else:
            print(f"[{i}/{len(files)}] {f.name}", end="", flush=True)
        file_start = _time.time()
        try:
            # Phase 1: always skip vision — will run in Phase 2
            record = process_file(f, skip_vision=True, existing=existing, refresh=args.refresh)
            file_elapsed = _time.time() - file_start
            if record:
                frames = record.pop("_frames", [])
                # Write media row + auto tags + frame rows in ONE transaction
                # (H7): a crash between the media upsert and the frame inserts used
                # to leave a frame-less row that is_processed then skipped forever,
                # only recoverable via a full --refresh.
                # NB: a refresh's stale-auto-tag clear lives in Phase 2, inside
                # the same transaction as the fresh vision-tag write — so a clip
                # whose vision FAILS keeps its prior tags rather than being left
                # bald (Codex review P2). The BMD tags added here are re-applied
                # there too. For video clips this Phase-1 add is redundant with
                # Phase 2, but it's the ONLY tag write for audio/no-vision clips.
                auto_tags = record.get("_auto_tags") or []
                with db.get_conn() as conn:
                    # audit H5: resolve the existing row id FIRST (the lookup
                    # matches abs OR rel form) and UPDATE in place. upsert's
                    # ON CONFLICT(path) never fires across abs↔rel forms, so a
                    # refresh of a legacy absolute-path row used to INSERT a
                    # duplicate row that frames/tags then attached to
                    # arbitrarily (observed in prod). Updating by id also
                    # normalizes the stored path to the relative form.
                    mid = _get_media_id_for_path(f, _conn=conn)
                    if mid:
                        db.update_media_by_id(mid, record, _conn=conn)
                    else:
                        db.upsert(record, _conn=conn)
                        mid = _get_media_id_for_path(f, _conn=conn)
                    if mid:
                        if existing is not None:
                            refreshed_ids.add(mid)
                        for tag_name in auto_tags:
                            db.add_tag(mid, tag_name, source="auto", _conn=conn)
                        # Store frame records (without vision descriptions yet)
                        if frames:
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
                if frames and need_vision:
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
                _emit_progress({"t": "file", "index": i, "total": len(files), "file": f.name, "status": "phase1_done"})
            else:
                failed += 1
        except Exception as e:
            print(f" [ERROR: {e}]")
            failed += 1

    # ── Phase 2: Vision (unload LLM first to free VRAM) ───────────────────
    # Initialized unconditionally so the exit-code logic can read them even when
    # Phase 2 doesn't run (was a fragile locals().get("vision_fail", 0) hack).
    vision_ok = vision_fail = 0
    consecutive_vision_fail = 0  # halt-on-N-consecutive whole-file EXCEPTIONS (Phase 6 / R6)
    vision_halted = False
    # issue #48: frame-failure tolerance (see _vision_halt_decision). Default 0 /
    # False = historical zero-tolerance halt.
    max_failures = getattr(args, "max_failures", 0) or 0
    skip_failed = getattr(args, "skip_failed", False)
    total_failed, consecutive_empty_frames = 0, 0
    vision_failed_files = []  # (fname, failed_count, frame_count)
    if phase1_results:
        print(f"\n{'─'*60}")
        print(f"Phase 2: Vision — {len(phase1_results)} files, unloading LLM to free VRAM...")
        _unload_ollama_model("qwen2.5:14b")
        _ensure_vision_ready()
        for vi, (fpath, (record, frames)) in enumerate(phase1_results.items(), 1):
            fname = Path(fpath).name
            video_frames = [fd for fd in frames if fd.get("thumbnail_path")]
            if not video_frames:
                continue

            if _STAGE_EVENTS:
                _emit_progress({"t": "stage", "stage": "vision", "file": fname})
            else:
                print(f"[{vi}/{len(phase1_results)}] {fname} >vision", end="", flush=True)
            v_start = _time.time()
            try:
                frame_paths = [db.resolve_path(fd["thumbnail_path"]) for fd in video_frames]
                frame_results, still_failed_idx = _describe_frames_with_fallback(frame_paths)
                scores = _apply_vision_to_frame_data(video_frames, frame_results)

                # Update DB: frames + media.frame_tags + auto tags.
                # audit L2: the frame/media UPDATEs and the tag clear+rewrite
                # used to live in TWO separate transactions — a crash between
                # them left frame_tags and the tags table permanently
                # inconsistent with no resume path. Everything for one clip now
                # commits atomically in a single transaction.
                with db.get_conn() as conn:
                    mid = _get_media_id_for_path(Path(fpath), _conn=conn)
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
                        # audit M2: COALESCE so a free-text vision fallback
                        # (all frames score=None → scores empty) preserves the
                        # prior editability_score instead of nulling it on
                        # refresh — same semantics as _run_vision_only.
                        conn.execute(
                            "UPDATE media SET frame_tags=?, editability_score=COALESCE(?, editability_score) WHERE id=?",
                            (frame_tags_json, max(scores) if scores else None, mid),
                        )
                        # Write auto tags from vision. Clear + rewrite happen in
                        # ONE transaction reached only on vision success: a
                        # refresh that fails vision never clears, so old
                        # searchable tags survive (Codex review P2). BMD tags
                        # (added in Phase 1) are cleared by this too, so
                        # re-apply them here.
                        db.delete_auto_tags(mid, _conn=conn)
                        for tag_name in (record.get("_auto_tags") or []):
                            db.add_tag(mid, tag_name, source="auto", _conn=conn)
                        for fd in video_frames:
                            for tag_name in fd.get("tags", "").split(","):
                                tag_name = tag_name.strip()
                                if tag_name and tag_name != "```":
                                    db.add_tag(mid, tag_name, source="auto", _conn=conn)

                v_elapsed = _time.time() - v_start
                consecutive_vision_fail = 0  # reset EXCEPTION streak: file processed without raising
                n_failed = len(still_failed_idx)
                if n_failed:
                    # issue #48: some frames still empty after both models. The
                    # successful frames were already committed above; the failed
                    # ones keep empty descriptions so a later --vision-only retries
                    # exactly them. Tolerate / halt per the policy.
                    # NOT counted in vision_fail: that drives sys.exit(1) (line ~1600),
                    # and a tolerated/skipped frame must leave the exit code 0 when the
                    # run completes — matching --vision-only (where only a halt exits 1).
                    # vision_fail stays reserved for whole-file EXCEPTIONS below.
                    total_failed += n_failed
                    consecutive_empty_frames = consecutive_empty_frames + n_failed if n_failed == len(video_frames) else 0
                    vision_failed_files.append((fname, n_failed, len(video_frames)))
                    print(f" [{v_elapsed:.1f}s] [{n_failed}/{len(video_frames)} FAILED]")
                    should_halt, reason = _vision_halt_decision(
                        n_failed, len(video_frames), total_failed, consecutive_empty_frames, max_failures, skip_failed)
                    if should_halt:
                        vision_halted = True
                        remaining = len(phase1_results) - vi
                        print(f"\n\n{'!'*60}")
                        print(f"VISION HALTED: {reason}")
                        print(f"  Completed: {vision_ok}  |  Remaining: {remaining}  |  Failed frames: {total_failed}")
                        print(f"  Fix Ollama, then resume: py -3.12 ingest.py --vision-only")
                        print(f"  (skip persistent failures: add --skip-failed)")
                        print(f"{'!'*60}\n")
                        break
                else:
                    consecutive_empty_frames = 0
                    print(f" [{v_elapsed:.1f}s] [OK]")
                    vision_ok += 1
            except Exception as e:
                print(f" [ERROR: {e}]")
                vision_fail += 1
                consecutive_vision_fail += 1
                # Halt on consecutive whole-file EXCEPTIONS (likely Ollama disconnect
                # / model crash). Distinct from the empty-description streak above —
                # this is describe_frames itself raising. Don't burn through every
                # file writing the same error 200 times.
                if consecutive_vision_fail >= 3:
                    vision_halted = True
                    remaining = len(phase1_results) - vi
                    print(f"\n{'!'*60}")
                    print(f"VISION HALTED: {consecutive_vision_fail} consecutive failures (last: {fname})")
                    print(f"  Completed: {vision_ok}  |  Remaining: {remaining}")
                    print(f"  Likely Ollama disconnect / model crash. Resume with:")
                    print(f"    py -3.12 ingest.py --dir <path> --vision-only")
                    print(f"{'!'*60}\n")
                    break

        if vision_failed_files and not vision_halted:
            total = sum(n for _, n, _ in vision_failed_files)
            print(f"\n⚠ {total} frame(s) across {len(vision_failed_files)} file(s) left empty (re-run --vision-only to retry):")
            for fn, n, tot in vision_failed_files[:20]:
                print(f"    {fn}: {n}/{tot}")
            if len(vision_failed_files) > 20:
                print(f"    … and {len(vision_failed_files) - 20} more file(s)")
        skipped_frames = sum(n for _, n, _ in vision_failed_files)
        print(f"Vision done. OK={vision_ok}  exceptions={vision_fail}  skipped_frames={skipped_frames}")
    elif need_vision and ok == 0 and failed > 0:
        # Phase 1 全 fail → Phase 2 沒檔可跑（不是「沒新檔」是「上游全炸」）
        print(f"\nPhase 2 skipped: phase 1 had {failed}/{len(files)} failures, no frames to process.")
    elif need_vision and skipped > 0 and ok == 0:
        # 全部 already-processed + 沒開 --refresh
        print(f"\nPhase 2 skipped: all {skipped} files already indexed (use --refresh to re-vision).")
    elif need_vision:
        print("\nNo new files to run vision on.")

    # ── Phase 3: Proxy generation (browser-incompatible codecs) ────────────
    print(f"\n{'─'*60}")
    print("Phase 3: Proxy generation for browser-incompatible codecs...")
    proxy_ok, proxy_skip = 0, 0
    with db.get_conn() as conn:
        all_media = conn.execute("SELECT id, path, codec FROM media").fetchall()
    for mid, mpath, stored_codec in all_media:
        resolved_path = db.resolve_path(mpath)
        proxy_path = config.proxy_path_for(mid, resolved_path)
        if proxy_path.exists():
            proxy_skip += 1
            continue
        if not Path(resolved_path).suffix.lower() in VIDEO_EXT:
            continue
        # Decide proxy need from the stored codec (set at ingest time) instead of
        # re-running ffprobe on every browser-compatible file each invocation
        # (H1). Only legacy rows with NULL codec fall back to a one-time probe,
        # whose result is then persisted so the next run skips it too.
        if stored_codec:
            want_proxy = stored_codec.lower() in codec.PROXY_CODECS
        else:
            verdict = codec.needs_proxy(resolved_path)
            want_proxy = verdict == codec.NEEDED
            if verdict != codec.UNKNOWN:
                probed = codec.probe_codec(resolved_path)
                if probed:
                    with db.get_conn() as conn:
                        conn.execute(
                            "UPDATE media SET codec=? WHERE id=?", (probed.lower(), mid)
                        )
        if want_proxy:
            print(f"  [{mid}] {Path(resolved_path).name} >proxy", end="", flush=True)
            result = generate_proxy(mid, resolved_path)
            if result:
                # B6: show original→proxy size delta so the compression win is
                # visible per-clip (not just the proxy's absolute size).
                mib = 1024 * 1024
                proxy_sz = Path(result).stat().st_size
                orig_sz = Path(resolved_path).stat().st_size
                pct = (proxy_sz - orig_sz) / orig_sz * 100 if orig_sz else 0
                print(f" [OK {proxy_sz / mib:.0f}MB ← {orig_sz / mib:.0f}MB, {pct:+.0f}%]")
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

    # Auto-build the vector index so semantic search + chat work immediately.
    # ingest writes SQLite records; embeddings live in ChromaDB and were a
    # separate `embed.py` step — easy to forget, leaving search/chat returning
    # nothing. Default ON; --no-embed to skip (e.g. batch-ingest then embed once).
    #
    # Run even when ok == 0 (was gated on ok>0): a prior run that died mid-batch
    # left SQLite rows the next run sees as "processed" (ok==0) and never embedded
    # → invisible to search forever. run_embed reconciles — it re-embeds anything
    # not yet indexed, force re-embeds refreshed rows, and prunes deleted ones (H5).
    if not getattr(args, "no_embed", False):
        try:
            import embed
            print(f"\n{'─'*60}")
            embed.run_embed(force_ids=refreshed_ids)
        except Exception as exc:
            print(f"\n[embed] ⚠ vector index build failed: {exc}")
            print("[embed] run `python embed.py` manually; ingest itself succeeded.")

    if bench_log:
        # Report the *effective* vision model, not config.VISION_MODEL — a
        # `vision.model` override would otherwise misreport the model actually
        # used for this run (see _bench_pipeline_desc).
        _pipeline_desc = _bench_pipeline_desc()
        print(f"\n{'='*60}")
        print(f"BENCHMARK SUMMARY — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'='*60}")
        print(f"Pipeline: {_pipeline_desc}")
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
            "pipeline": _pipeline_desc,
            "gpu": detect_gpu(),
            "total_duration_s": round(total_dur, 1),
            "total_process_s": round(batch_elapsed, 1),
            "overall_speed_x": round(total_dur / max(batch_elapsed, 1), 1),
            "files": bench_log,
        }
        bench_path.write_text(json.dumps(bench_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Bench log saved: {bench_path}")

    # Phase 8.0e (R1): exit code reflects actual ingest outcome.
    # Before this, main() always fell through to exit 0 — 222/222 fail
    # on 2026-05-25 still returned 0, hiding the regression from runner.
    if failed and not ok:
        sys.exit(2)  # all phase 1 failed
    if failed or vision_fail or vision_halted:
        sys.exit(1)  # partial fail (phase 1) or vision failure/halt (phase 2)
    # else: implicit exit 0


def detect_gpu() -> str:
    """Return short GPU description for bench log (cross-platform)."""
    import subprocess
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=5,
            )
            chipset = metal = None
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("Chipset Model:"):
                    chipset = line.split(":", 1)[1].strip()
                elif line.startswith("Metal Support:"):
                    metal = line.split(":", 1)[1].strip()
            if chipset:
                return f"{chipset} ({metal})" if metal else chipset
        except Exception:
            pass
        return "Apple Silicon GPU"
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            lines = r.stdout.strip().splitlines()
            if lines:
                return lines[0]
    except Exception:
        pass
    return "Unknown GPU"


if __name__ == "__main__":
    main()
