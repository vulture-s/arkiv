import subprocess
import tempfile
from pathlib import Path

THUMBNAILS_DIR = Path(__file__).parent / "thumbnails"


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


def extract_frames(video_path: str, duration_s: float, fps: float) -> list[str]:
    """
    Extract representative frames from a video.
    - Short clip (< 60s): 3 fixed frames at 25%, 50%, 75%
    - Long clip (>= 60s): ffmpeg scene detect threshold=0.3
    Returns list of temp jpg paths (caller must clean up).
    """
    tmp_dir = tempfile.mkdtemp(prefix="media_frames_")

    if duration_s < 60:
        frames = _extract_fixed(video_path, duration_s, fps, tmp_dir)
    else:
        frames = _extract_scene(video_path, tmp_dir)
        if not frames:
            frames = _extract_fixed(video_path, duration_s, fps, tmp_dir)

    return frames


def _extract_fixed(video_path: str, duration_s: float, fps: float, out_dir: str) -> list[str]:
    positions = [0.25, 0.5, 0.75]
    output = []
    for i, pct in enumerate(positions):
        t = duration_s * pct
        frame_n = int(t * fps)
        out = Path(out_dir) / f"frame_{i:02d}.jpg"
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", f"select='eq(n\\,{frame_n})',scale=640:-1",
            "-vsync", "vfr", "-frames:v", "1",
            str(out), "-y"
        ]
        subprocess.run(cmd, capture_output=True)
        if out.exists():
            output.append(str(out))
    return output


def _extract_scene(video_path: str, out_dir: str) -> list[str]:
    out_pattern = str(Path(out_dir) / "scene_%04d.jpg")
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", "select='gt(scene,0.3)',scale=640:-1",
        "-vsync", "vfr", out_pattern, "-y"
    ]
    subprocess.run(cmd, capture_output=True)
    return sorted(str(p) for p in Path(out_dir).glob("scene_*.jpg"))
