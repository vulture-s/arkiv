from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import config

THUMBNAILS_DIR = config.THUMBNAILS_DIR


def _ensure_thumbnails_dir() -> None:
    """Phase 8.0e last-line defense. Dangling symlink (target gone) →
    mkdir(exist_ok=True) raises FileExistsError on every call, which is
    what triggered 222/222 fails on 2026-05-25 overnight. Preflight in
    health.py should catch this earlier; this stays as fallback so the
    failure mode is loud (sys.exit 3) instead of 222× silent retries."""
    if THUMBNAILS_DIR.is_symlink():
        target = THUMBNAILS_DIR.resolve(strict=False)
        if not target.exists():
            import sys
            print(
                f"\n[FATAL] THUMBNAILS_DIR 是 dangling symlink: "
                f"{THUMBNAILS_DIR} -> {target}（target 不存在）。\n"
                f"        修法：rm {THUMBNAILS_DIR} 後重跑；或設 ARKIV_THUMBNAILS_DIR env",
                file=sys.stderr,
            )
            sys.exit(3)
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)


def _run_ffmpeg(cmd, out_path: Optional[Path] = None) -> bool:
    """Strict ffmpeg success: returncode == 0 AND out_path (if given)
    is a non-zero-size file. 0-byte file from ffmpeg-exit-0-but-failed
    is treated as fail (avoids registering empty frames as valid)."""
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        return False
    if out_path is not None:
        if not out_path.exists() or out_path.stat().st_size == 0:
            return False
    return True


def extract_thumbnail(video_path: str, duration_s: float, force: bool = False) -> Optional[str]:
    """
    Extract one representative frame (50% position) and save permanently
    to thumbnails/{stem}.jpg. Returns saved path or None on failure.

    By default an existing non-empty poster is reused (cheap re-ingest). Pass
    force=True to actually rebuild it (used by `ingest.py --regenerate-thumbnails`
    after the thumbnail-rendering logic changes).
    """
    _ensure_thumbnails_dir()
    stem = Path(video_path).stem
    out = THUMBNAILS_DIR / f"{stem}.jpg"
    if not force and out.exists() and out.stat().st_size > 0:
        return str(out)

    t = max(duration_s * 0.5, 1.0)
    cmd = [
        "ffmpeg", "-ss", str(t), "-i", video_path,
        "-vf", "scale=320:-1",
        "-frames:v", "1", str(out), "-y"
    ]
    return str(out) if _run_ffmpeg(cmd, out) else None


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
    _ensure_thumbnails_dir()
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
        already_ok = out.exists() and out.stat().st_size > 0
        if not already_ok:
            cmd = [
                "ffmpeg", "-ss", str(t), "-i", video_path,
                "-vf", "scale=320:-1",
                "-frames:v", "1", str(out), "-y"
            ]
            if not _run_ffmpeg(cmd, out):
                continue
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
    if proc.returncode != 0:
        return []

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
        already_ok = out.exists() and out.stat().st_size > 0
        if not already_ok:
            cmd = [
                "ffmpeg", "-ss", str(t), "-i", video_path,
                "-vf", "scale=320:-1",
                "-frames:v", "1", str(out), "-y"
            ]
            if not _run_ffmpeg(cmd, out):
                continue
        results.append({
            "index": i,
            "timestamp_s": round(t, 2),
            "thumbnail_path": str(out),
        })
    return results
