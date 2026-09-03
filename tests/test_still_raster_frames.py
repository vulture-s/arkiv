"""A still image must never be seeked into — regression for the silent JPEG hole.

Measured 2026-09-03 on a 3-image library (png / jpg / webp, identical pixels):

    [1/3] alpha-bars.png    >thumb >frames [OK]
    [2/3] bravo-bars.jpg    >thumb >frames  WARNING: extracted 0 frames
    [3/3] charlie-bars.webp >thumb >frames [OK]

Two facts combine into the bug:

1. ffprobe reports a `.jpg` as codec ``mjpeg`` with duration ``0.040000`` (one
   frame at an assumed 25fps), while `.png` / `.webp` report ``duration=N/A``
   → 0.0. So ``duration_s <= 0``, the old "is this a still?" test, called every
   JPEG a video and sent it down the mid-point-seek path.
2. ``-ss`` placed *before* ``-i`` on a single-frame mjpeg makes ffmpeg exit 0
   having encoded nothing ("Output file is empty, nothing was encoded"), which
   _run_ffmpeg then correctly rejects as a 0-byte result. This holds even for
   ``-ss 0`` — the seek is fatal, not merely misplaced.

The visible damage was not the missing poster. Zero frames means ``ingest.py``'s
``if not skip_vision and frame_data:`` never fires, so the vision model never saw
a single JPEG: no scene description, no auto tags, and the clip is unreachable by
any content search.
"""
import importlib
import pathlib
import shutil
import subprocess

import pytest

frames = importlib.import_module("frames")
mediatypes = importlib.import_module("mediatypes")

_needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg not on PATH"
)


# ── the extension set is the single source of truth ─────────────────────────
def test_still_raster_ext_is_image_ext_minus_vector():
    assert mediatypes.STILL_RASTER_EXT < mediatypes.IMAGE_EXT
    assert ".svg" not in mediatypes.STILL_RASTER_EXT
    assert mediatypes.IMAGE_EXT - mediatypes.STILL_RASTER_EXT == {".svg"}


def test_ingest_alias_is_the_same_object():
    """ingest must not re-derive its own copy — that drift is what mediatypes exists to stop."""
    ingest = importlib.import_module("ingest")
    assert ingest.RASTER_IMAGE_EXTS is mediatypes.STILL_RASTER_EXT


@pytest.mark.parametrize("name", ["a.png", "a.jpg", "a.jpeg", "a.webp", "a.gif", "A.JPG"])
def test_is_still_raster_true(name):
    assert frames._is_still_raster(name) is True


@pytest.mark.parametrize("name", ["a.svg", "a.mp4", "a.mov", "a.insv", "a.360", "a.wav"])
def test_is_still_raster_false(name):
    assert frames._is_still_raster(name) is False


# ── the seek must be absent for stills, present for video ───────────────────
def _capture_cmd(monkeypatch):
    seen = {}

    def fake_run(cmd, out_path=None, timeout=60):
        seen["cmd"] = cmd
        return False  # don't touch the filesystem

    monkeypatch.setattr(frames, "_run_ffmpeg", fake_run)
    monkeypatch.setattr(frames, "_frame_vf_args", lambda p: ["-vf", "scale=320:-1"])
    return seen


def test_still_command_carries_no_ss(monkeypatch, tmp_path):
    seen = _capture_cmd(monkeypatch)
    frames._extract_frame_to("/x/shot.jpg", 0.02, tmp_path / "o.jpg")
    assert "-ss" not in seen["cmd"], seen["cmd"]
    # and the input is still the very next thing ffmpeg is told about
    assert seen["cmd"][1] == "-i"


def test_video_command_keeps_ss(monkeypatch, tmp_path):
    seen = _capture_cmd(monkeypatch)
    frames._extract_frame_to("/x/clip.mp4", 4.5, tmp_path / "o.jpg")
    assert seen["cmd"][1:4] == ["-ss", "4.5", "-i"], seen["cmd"]


# ── the JPEG duration lie must not reach the seek maths ─────────────────────
def test_thumbnail_of_jpeg_samples_t0_despite_nonzero_duration(monkeypatch, tmp_path):
    """A .jpg probes as 0.04s. The old code turned that into max(0.02, 1.0) = 1.0."""
    monkeypatch.setattr(frames, "THUMBNAILS_DIR", tmp_path)
    seen = {}
    monkeypatch.setattr(
        frames, "_extract_frame_to", lambda p, t, out: seen.setdefault("t", t) is None or True
    )
    frames.extract_thumbnail("/x/shot.jpg", 0.04, force=True)
    assert seen["t"] == 0.0


def test_thumbnail_of_video_still_uses_midpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(frames, "THUMBNAILS_DIR", tmp_path)
    seen = {}
    monkeypatch.setattr(
        frames, "_extract_frame_to", lambda p, t, out: seen.setdefault("t", t) is None or True
    )
    frames.extract_thumbnail("/x/clip.mp4", 30.0, force=True)
    assert seen["t"] == 15.0


def test_fixed_frames_of_still_is_one_frame_at_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(frames, "THUMBNAILS_DIR", tmp_path)

    def fake_extract(p, t, out):
        out.write_bytes(b"\xff\xd8jpegbytes")
        return True

    monkeypatch.setattr(frames, "_extract_frame_to", fake_extract)
    res = frames._extract_fixed_persistent(
        "/x/shot.jpg", 0.04, 25.0, "shot", n_frames=3, force=True
    )
    assert len(res) == 1
    assert res[0]["timestamp_s"] == 0.0


# ── end-to-end: the test that would actually have caught this ───────────────
def _synth(tmp_path, name):
    """One solid-colour still, written by Pillow.

    Deliberately NOT written by ffmpeg's lavfi source: a JPEG ffmpeg encodes that
    way probes as duration 0.0, so it would quietly sidestep the very condition
    under test. An ordinary Pillow-written JPEG reproduces the real 0.04s reading,
    the same as any file a camera or an editor produces. Pillow is a declared
    dependency (requirements.txt) and CI installs it in every job, and it also
    writes WebP without needing ffmpeg's optional webp *encoder* (ffmpeg only has
    to decode it here).
    """
    from PIL import Image

    # 1600x900, not a thumbnail-sized swatch: ffprobe only reports a duration for a
    # JPEG once it has a size/bit_rate to compute one from. A 320x180 solid colour
    # compresses to ~1.5 KB, probes as `duration=N/A`, and survives the old seek —
    # so a small fixture would pass while every real photo still broke.
    out = tmp_path / name
    Image.new("RGB", (1600, 900), (255, 140, 0)).save(out)
    assert out.stat().st_size > 0
    return out


def _probed_duration(path):
    """Whatever ffprobe actually claims — 0.04 for jpg, N/A→0.0 for png/webp.

    Read live rather than hard-coded so the test keeps exercising the real
    discrepancy if a future ffmpeg changes what it reports.
    """
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


@_needs_ffmpeg
@pytest.mark.parametrize("name", ["still.png", "still.jpg", "still.webp"])
def test_real_ffmpeg_thumbnail_for_every_raster_format(tmp_path, monkeypatch, name):
    monkeypatch.setattr(frames, "THUMBNAILS_DIR", tmp_path / "thumbs")
    src = _synth(tmp_path, name)
    got = frames.extract_thumbnail(str(src), _probed_duration(src), force=True)
    assert got is not None, "{0}: no thumbnail produced".format(name)
    assert pathlib.Path(got).stat().st_size > 0


@_needs_ffmpeg
@pytest.mark.parametrize("name", ["still.png", "still.jpg", "still.webp"])
def test_real_ffmpeg_frames_for_every_raster_format(tmp_path, monkeypatch, name):
    monkeypatch.setattr(frames, "THUMBNAILS_DIR", tmp_path / "thumbs")
    src = _synth(tmp_path, name)
    res = frames.extract_frames(str(src), _probed_duration(src), 25.0, force=True)
    # Non-empty is the whole point: zero frames means vision never runs.
    assert res, "{0}: extracted 0 frames".format(name)
    assert all(pathlib.Path(r["thumbnail_path"]).stat().st_size > 0 for r in res)


@_needs_ffmpeg
def test_jpeg_really_does_report_a_nonzero_duration(tmp_path):
    """Pin the premise. If this ever fails, the fix's rationale changed, not the fix."""
    assert _probed_duration(_synth(tmp_path, "still.jpg")) > 0
    assert _probed_duration(_synth(tmp_path, "still.png")) == 0.0
