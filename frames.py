from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import config

THUMBNAILS_DIR = config.THUMBNAILS_DIR


def extract_thumbnail(video_path: str, duration_s: float) -> Optional[str]:
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


def _adaptive_frame_count(duration_s: float) -> int:
    if duration_s < 2:
        return 1
    if duration_s <= 10:
        return 3
    if duration_s <= 60:
        return 5
    return 5 + max(1, int((duration_s - 60) / 30))


def extract_frames(video_path: str, duration_s: float, fps: float) -> List[Dict]:
    """
    Extract representative frames from a video and persist thumbnails.
    Returns list of {index, timestamp_s, thumbnail_path}.
    - Short clip: fixed evenly-spaced frames
    - Long clip: scene detect with adaptive cap, fallback to fixed frames
    """
    THUMBNAILS_DIR.mkdir(exist_ok=True)
    stem = Path(video_path).stem
    n_frames = _adaptive_frame_count(duration_s)

    if duration_s < 60:
        results = _extract_fixed_persistent(video_path, duration_s, fps, stem, n_frames=n_frames)
    else:
        results = _extract_scene_persistent(video_path, duration_s, stem, max_frames=n_frames)
        if not results:
            results = _extract_fixed_persistent(video_path, duration_s, fps, stem, n_frames=n_frames)

    return results


def _extract_fixed_persistent(
    video_path: str,
    duration_s: float,
    fps: float,
    stem: str,
    n_frames: int = 3,
) -> List[Dict]:
    positions = [float(i) / float(n_frames + 1) for i in range(1, n_frames + 1)]
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


def _extract_scene_persistent(
    video_path: str,
    duration_s: float,
    stem: str,
    max_frames: int = 5,
) -> List[Dict]:
    """Use scene detection, then persist top scene-change frames."""
    # First pass: detect scene timestamps via showinfo (stderr), discard frames
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

    if len(timestamps) > max_frames:
        step = float(len(timestamps)) / float(max_frames)
        timestamps = [timestamps[int(step * i)] for i in range(max_frames)]

    results = []
    for i, t in enumerate(timestamps[:max_frames]):
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
