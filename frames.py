from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import config

THUMBNAILS_DIR = config.THUMBNAILS_DIR


def extract_thumbnail(video_path: str, duration_s: float) -> str | None:
    """
    Extract one representative frame (50% position) and save permanently
    to thumbnails/{stem}.jpg. Returns saved path or None on failure.
    """
    THUMBNAILS_DIR.mkdir(exist_ok=True)
    stem = Path(video_path).stem
    out = THUMBNAILS_DIR / f"{stem}.jpg"
    if out.exists():
        return str(out)

    t = max(duration_s * 0.5, 1.0)
    cmd = [
        "ffmpeg", "-ss", str(t), "-i", video_path,
        "-vf", "scale=320:-1",
        "-frames:v", "1", str(out), "-y"
    ]
    subprocess.run(cmd, capture_output=True)
    return str(out) if out.exists() else None


def extract_frames(video_path: str, duration_s: float, fps: float) -> list[dict]:
    """
    Extract representative frames from a video and persist thumbnails.
    Returns list of {index, timestamp_s, thumbnail_path}.
    - Short clip (< 60s): 3 fixed frames at 25%, 50%, 75%
    - Long clip (>= 60s): ffmpeg scene detect threshold=0.3
    """
    THUMBNAILS_DIR.mkdir(exist_ok=True)
    stem = Path(video_path).stem

    if duration_s < 60:
        results = _extract_fixed_persistent(video_path, duration_s, fps, stem)
    else:
        results = _extract_scene_persistent(video_path, duration_s, stem)
        if not results:
            results = _extract_fixed_persistent(video_path, duration_s, fps, stem)

    return results


def _extract_fixed_persistent(video_path: str, duration_s: float, fps: float, stem: str) -> list[dict]:
    positions = [0.25, 0.5, 0.75]
    results = []
    for i, pct in enumerate(positions):
        t = duration_s * pct
        out = THUMBNAILS_DIR / f"{stem}_frame{i}.jpg"
        if not out.exists():
            cmd = [
                "ffmpeg", "-ss", str(t), "-i", video_path,
                "-vf", "scale=320:-1",
                "-frames:v", "1", str(out), "-y"
            ]
            subprocess.run(cmd, capture_output=True)
        if out.exists():
            results.append({
                "index": i,
                "timestamp_s": round(t, 2),
                "thumbnail_path": str(out),
            })
    return results


def _extract_scene_persistent(video_path: str, duration_s: float, stem: str) -> list[dict]:
    """Use scene detection, then persist top 3 frames."""
    tmp_dir = tempfile.mkdtemp(prefix="media_frames_")
    # First pass: detect scene timestamps
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", "select='gt(scene,0.3)',showinfo",
        "-vsync", "vfr", "-f", "null", "-"
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    # Parse timestamps from showinfo output
    timestamps = []
    for line in proc.stderr.splitlines():
        if "pts_time:" in line:
            try:
                pts = float(line.split("pts_time:")[1].split()[0])
                timestamps.append(pts)
            except (ValueError, IndexError):
                pass

    if not timestamps:
        return []

    # Pick up to 3 evenly spaced scene changes
    if len(timestamps) > 3:
        step = len(timestamps) // 3
        timestamps = [timestamps[step * i] for i in range(3)]

    results = []
    for i, t in enumerate(timestamps[:3]):
        out = THUMBNAILS_DIR / f"{stem}_frame{i}.jpg"
        if not out.exists():
            cmd = [
                "ffmpeg", "-ss", str(t), "-i", video_path,
                "-vf", "scale=320:-1",
                "-frames:v", "1", str(out), "-y"
            ]
            subprocess.run(cmd, capture_output=True)
        if out.exists():
            results.append({
                "index": i,
                "timestamp_s": round(t, 2),
                "thumbnail_path": str(out),
            })
    return results
