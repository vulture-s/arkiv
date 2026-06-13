from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import config

THUMBNAILS_DIR = config.THUMBNAILS_DIR


# Phase 8.3b: dual-fisheye 360 (Insta360 .insv / GoPro Max .360) get stitched into a
# full equirectangular panorama BEFORE frame extraction so vision sees an undistorted
# scene. Phase 8.3a POC (references/plans/arkiv/2026-06-13-arkiv-8.3a-360-indexing-poc):
# the raw fisheye buries the wearer + on-screen text (event name, bib #) in the
# distorted edge and the VLM reads nothing; the equirect stitch surfaces them. 360
# frames extract larger (1024px vs the normal 320px) so reprojected detail survives.
_FISHEYE_360_EXT = {".insv", ".360"}
_V360_360_FILTER = "v360=dfisheye:equirect:ih_fov=193:iv_fov=193"
_360_SCALE = "scale=1024:-1"
_NORMAL_SCALE = "scale=320:-1"
_is_360_cache: dict = {}


def _is_360_dualfisheye(video_path: str) -> bool:
    """True for a dual-fisheye 360 source: ext gate + two video streams confirmed via
    ffprobe (so a single-lens file mislabeled .360 won't try to stitch a missing
    stream). Cache keyed by (path, mtime, size) — like codec.py — so a replaced file
    re-probes; a failed/errored probe is NOT cached so it retries next time."""
    if Path(video_path).suffix.lower() not in _FISHEYE_360_EXT:
        return False
    try:
        st = os.stat(video_path)
        key = (os.path.abspath(video_path), st.st_mtime_ns, st.st_size)
    except OSError:
        return False
    if key in _is_360_cache:
        return _is_360_cache[key]
    try:
        out = subprocess.run(
            [config.FFPROBE_PATH, "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=index", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if out.returncode != 0:
            return False  # transient/failed probe — don't cache, retry next time
        ok = len([r for r in out.stdout.splitlines() if r.strip()]) >= 2
    except Exception:
        return False  # don't cache transient failure
    _is_360_cache[key] = ok
    return ok


def _frame_vf_args(video_path: str) -> List[str]:
    """ffmpeg filter args for frame extraction: a full-360 equirect stitch for
    dual-fisheye sources (both lenses hstacked → dfisheye→equirect), else a plain
    downscale. Slots in where ``["-vf", "scale=320:-1"]`` used to be.

    Uses video-stream-relative mapping ``[0:v:0][0:v:1]`` (not ``[0:0][0:1]``) so an
    audio stream sitting between the two fisheye tracks can't get hstacked by mistake.
    """
    if _is_360_dualfisheye(video_path):
        fc = "[0:v:0][0:v:1]hstack[f];[f]{0},{1}[o]".format(_V360_360_FILTER, _360_SCALE)
        return ["-filter_complex", fc, "-map", "[o]"]
    return ["-vf", _NORMAL_SCALE]


def _safe_stem(video_path: str) -> str:
    """Thumbnail filename stem scoped by a hash of the absolute source path.

    The bare filename stem is NOT unique: camera cards routinely reuse names
    (Sony C0001.MP4, GoPro GX010001.MP4). With --recursive over several card
    dumps, two clips share a stem, the second finds the first's thumbnail
    already on disk and reuses it — so its vision tags/score are computed from
    the wrong frames. Proxies already guard this via config.proxy_path_for;
    thumbnails get the same path-hash treatment here.
    """
    stem = Path(video_path).stem
    digest = hashlib.sha1(os.path.abspath(video_path).encode("utf-8")).hexdigest()[:10]
    return f"{stem}_{digest}"


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


def _run_ffmpeg(cmd, out_path: Optional[Path] = None, timeout: float = 60) -> bool:
    """Strict ffmpeg success: returncode == 0 AND out_path (if given)
    is a non-zero-size file. 0-byte file from ffmpeg-exit-0-but-failed
    is treated as fail (avoids registering empty frames as valid)."""
    try:
        # Single-frame extraction must not hang forever on a corrupt file —
        # an unbounded ffmpeg here wedged whole Phase 1 batches (audit H6).
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # audit H6: timeout → fail loud; drop any partial output so a torn
        # jpg isn't reused as "already_ok" on the next pass
        print(f"  [frames] ffmpeg timed out (>{int(timeout)}s), skipping frame", flush=True)
        if out_path is not None:
            out_path.unlink(missing_ok=True)
        return False
    if result.returncode != 0:
        # audit M18: failures used to be silently dropped — clips ended up
        # permanently frameless with zero diagnostics
        err = (result.stderr or b"").decode("utf-8", "replace").strip()
        print(f"  [frames] ffmpeg failed rc={result.returncode}: {err[-300:] or '(no stderr)'}", flush=True)
        return False
    if out_path is not None:
        if not out_path.exists() or out_path.stat().st_size == 0:
            return False
    return True


def _extract_frame_to(video_path: str, t: float, out: Path) -> bool:
    """Extract one frame at time ``t`` to ``out`` atomically: ffmpeg writes a temp
    sibling, then os.replace onto ``out`` only on success. So a forced re-extraction
    (--refresh) that times out / fails can't destroy the prior good thumbnail while
    the DB still references it (issue #53 / Codex). For 360 sources the filter is the
    full-equirect stitch (see _frame_vf_args)."""
    # Temp MUST keep the real image suffix (…tmp.<pid>.jpg, not …jpg.tmp): ffmpeg
    # infers the output format from the extension, and a bare ".tmp" makes it abort
    # ("Unable to choose an output format"). The <pid> keeps concurrent refreshes of
    # the same output from clobbering each other's temp.
    tmp = out.with_name("{0}.tmp.{1}{2}".format(out.stem, os.getpid(), out.suffix))
    cmd = (
        [config.FFMPEG_PATH, "-ss", str(t), "-i", video_path]
        + _frame_vf_args(video_path)
        + ["-frames:v", "1", str(tmp), "-y"]
    )
    if _run_ffmpeg(cmd, tmp):
        os.replace(str(tmp), str(out))
        return True
    tmp.unlink(missing_ok=True)
    return False


def extract_thumbnail(video_path: str, duration_s: float, force: bool = False) -> Optional[str]:
    """
    Extract one representative frame (50% position) and save permanently
    to thumbnails/{stem}.jpg. Returns saved path or None on failure.

    By default an existing non-empty poster is reused (cheap re-ingest). Pass
    force=True to actually rebuild it (used by `ingest.py --regenerate-thumbnails`
    after the thumbnail-rendering logic changes).
    """
    _ensure_thumbnails_dir()
    stem = _safe_stem(video_path)
    out = THUMBNAILS_DIR / f"{stem}.jpg"
    if not force and out.exists() and out.stat().st_size > 0:
        return str(out)

    t = max(duration_s * 0.5, 1.0)
    return str(out) if _extract_frame_to(video_path, t, out) else None


def _adaptive_frame_count(duration_s: float) -> int:
    if duration_s < 2:
        return 1
    if duration_s <= 10:
        return 3
    if duration_s <= 60:
        return 5
    return 5 + max(1, int((duration_s - 60) / 30))


def extract_frames(video_path: str, duration_s: float, fps: float, force: bool = False) -> List[Dict]:
    """
    Extract representative frames from a video and persist thumbnails.
    Returns list of {index, timestamp_s, thumbnail_path}.
    - Short clip: fixed evenly-spaced frames
    - Long clip: scene detect with adaptive cap, fallback to fixed frames

    force=True re-extracts even when a thumbnail already exists — needed when the
    extraction logic itself changed (e.g. Phase 8.3b 360 reproject), so `--refresh`
    actually re-applies it to already-ingested clips instead of reusing stale frames
    (issue #53). Default reuses existing thumbnails for cheap re-ingest.
    """
    _ensure_thumbnails_dir()
    stem = _safe_stem(video_path)
    n_frames = _adaptive_frame_count(duration_s)

    if duration_s < 60:
        results = _extract_fixed_persistent(video_path, duration_s, fps, stem, n_frames=n_frames, force=force)
    else:
        results = _extract_scene_persistent(video_path, duration_s, stem, max_frames=n_frames, force=force)
        if not results:
            results = _extract_fixed_persistent(video_path, duration_s, fps, stem, n_frames=n_frames, force=force)

    if not results:
        # audit M18: a video ending up with zero frames used to look like [OK]
        # — leave at least one trace in the log
        print(f"  [frames] WARNING: extracted 0 frames for {video_path}", flush=True)
    return results


def _extract_fixed_persistent(
    video_path: str,
    duration_s: float,
    fps: float,
    stem: str,
    n_frames: int = 3,
    force: bool = False,
) -> List[Dict]:
    positions = [float(i) / float(n_frames + 1) for i in range(1, n_frames + 1)]
    results = []
    for i, pct in enumerate(positions):
        t = duration_s * pct
        out = THUMBNAILS_DIR / f"{stem}_frame{i}.jpg"
        already_ok = (not force) and out.exists() and out.stat().st_size > 0
        if not already_ok:
            if not _extract_frame_to(video_path, t, out):
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
    force: bool = False,
) -> List[Dict]:
    """Use scene detection, then persist top scene-change frames."""
    # First pass: detect scene timestamps via showinfo (stderr), discard frames
    cmd = [
        config.FFMPEG_PATH, "-i", video_path,
        "-vf", "select='gt(scene,0.3)',showinfo",
        "-vsync", "vfr", "-f", "null", "-"
    ]
    # Full-file scene-detect pass scales with clip length — bound it so one
    # bad file can't hang the whole batch; timeout falls back to fixed frames
    # via the caller (audit H6).
    scene_timeout = max(120.0, duration_s * 2)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=scene_timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"  [frames] scene-detect timed out (>{int(scene_timeout)}s), falling back to fixed frames", flush=True)  # audit H6
        return []
    if proc.returncode != 0:
        # audit M18: surface why scene detection failed instead of silent []
        err = (proc.stderr or "").strip()
        print(f"  [frames] scene-detect failed rc={proc.returncode}: {err[-300:] or '(no stderr)'}", flush=True)
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
        already_ok = (not force) and out.exists() and out.stat().st_size > 0
        if not already_ok:
            if not _extract_frame_to(video_path, t, out):
                continue
        results.append({
            "index": i,
            "timestamp_s": round(t, 2),
            "thumbnail_path": str(out),
        })
    return results
