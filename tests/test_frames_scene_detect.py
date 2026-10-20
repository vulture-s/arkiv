"""Scene-detect pass must run on the installed ffmpeg.

FFmpeg 7+ removed ``-vsync`` (replaced by ``-fps_mode``, available since 5.1;
README floor is 6.0). With the old flag ffmpeg exits on argument parsing, the
scene pass returns [] and every long clip silently falls back to fixed-position
frames. This runs the real binary on a synthetic two-scene clip so the next
removed flag fails here instead of in an ingest log.
"""
import importlib
import shutil
import subprocess

import pytest

frames = importlib.import_module("frames")
config = importlib.import_module("config")


@pytest.mark.skipif(not shutil.which(config.FFMPEG_PATH or "ffmpeg"), reason="ffmpeg not installed")
def test_scene_detect_finds_cut_on_real_ffmpeg(tmp_path, monkeypatch):
    clip = tmp_path / "two_scenes.mp4"
    subprocess.run(
        [config.FFMPEG_PATH, "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=160x90:d=1:r=10",
         "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=1:r=10",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    monkeypatch.setattr(frames, "THUMBNAILS_DIR", tmp_path)

    out = frames._extract_scene_persistent(str(clip), 2.0, "two_scenes")

    assert out, "scene-detect returned no frames (ffmpeg rejected the command?)"
    assert any(0.8 <= f["timestamp_s"] <= 1.2 for f in out)
