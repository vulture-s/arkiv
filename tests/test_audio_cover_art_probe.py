"""Issue #521: an audio file's embedded cover art is not its video track.

ffprobe reports cover art (ID3v2 APIC, m4a `covr`, FLAC PICTURE) as a VIDEO
stream — `codec_name=mjpeg`, `r_frame_rate=90000/1`, the cover's width/height —
marked only by `disposition.attached_pic=1`. `ingest.probe()` took "the first
video stream" and `codec.probe_codec()` used `-select_streams v:0`, so a music
file landed in `media` as a 300x300 mjpeg at 90000 fps.

Two layers of tests:

* stream-dict tests (always run, including CI where ffmpeg is not installed):
  feed `probe()` the exact ffprobe JSON shape and check what it keeps.
* real-file tests (skip without ffmpeg): build an MP3 / M4A / FLAC with cover
  art on the spot and run the real ffprobe through both probe paths. These are
  the ones that prove `V:0` behaves as claimed on a real binary.
"""
from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import types

import pytest

import codec
import ingest
import pathres

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg/ffprobe")

_COVER = {
    "codec_type": "video", "codec_name": "mjpeg", "width": 300, "height": 300,
    "r_frame_rate": "90000/1", "disposition": {"attached_pic": 1},
}
_AUDIO = {"codec_type": "audio", "codec_name": "mp3", "disposition": {"attached_pic": 0}}
_H264 = {
    "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
    "r_frame_rate": "25/1", "disposition": {"attached_pic": 0},
}


def _fake_ffprobe(monkeypatch, streams):
    payload = json.dumps({"format": {"duration": "2.0", "size": "1000"},
                          "streams": streams})

    def run(cmd, **kw):
        return types.SimpleNamespace(returncode=0, stdout=payload, stderr="")

    monkeypatch.setattr(ingest.subprocess, "run", run)


# ── stream-dict layer (runs everywhere) ──────────────────────────────────────
@pytest.mark.parametrize("name", ["song.mp3", "song.m4a", "song.flac"])
def test_cover_art_is_not_read_as_the_picture_track(monkeypatch, tmp_path, name):
    _fake_ffprobe(monkeypatch, [_AUDIO, _COVER])
    meta = ingest.probe(str(tmp_path / name))
    assert meta["codec"] is None
    assert meta["fps"] is None
    assert meta["width"] is None and meta["height"] is None
    assert meta["has_audio"] == 1


def test_attached_pic_is_skipped_even_on_a_video_extension(monkeypatch, tmp_path):
    """A video container can carry a cover too (iTunes .mp4/.m4v). The cover
    comes first here on purpose: only the disposition check can skip it."""
    _fake_ffprobe(monkeypatch, [_COVER, _H264, _AUDIO])
    meta = ingest.probe(str(tmp_path / "clip.mp4"))
    assert meta["codec"] == "h264"
    assert meta["fps"] == 25.0
    assert (meta["width"], meta["height"]) == (1920, 1080)


def test_audio_extension_never_yields_video_geometry(monkeypatch, tmp_path):
    """An audio-extension file whose picture stream is NOT flagged attached_pic
    (some taggers write it that way) is still artwork, not footage."""
    unflagged = dict(_COVER, disposition={"attached_pic": 0})
    _fake_ffprobe(monkeypatch, [_AUDIO, unflagged])
    meta = ingest.probe(str(tmp_path / "song.mp3"))
    assert meta["codec"] is None and meta["fps"] is None and meta["width"] is None


def test_plain_video_unchanged(monkeypatch, tmp_path):
    _fake_ffprobe(monkeypatch, [_H264, _AUDIO])
    meta = ingest.probe(str(tmp_path / "clip.mov"))
    assert (meta["codec"], meta["fps"], meta["width"]) == ("h264", 25.0, 1920)


def test_probe_codec_asks_for_capital_V(monkeypatch, tmp_path):
    f = tmp_path / "x.mp3"
    f.write_bytes(b"ID3")
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(codec.subprocess, "run", run)
    codec._codec_cache.clear()
    codec.probe_codec(str(f))
    i = seen[0].index("-select_streams")
    assert seen[0][i + 1] == "V:0"


# ── stream endpoint backfill ─────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["legacy.mp3", "legacy.m4a", "legacy.flac"])
def test_stream_endpoint_does_not_backfill_mjpeg_into_audio_rows(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch, name
):
    """Legacy row, codec NULL. Even if the probe says `mjpeg` (an older ffprobe,
    a mocked one, a stream without the disposition flag), an audio row must not
    end up with a video codec written into it."""
    db = importlib.import_module("db")
    snd = tmp_path / "music" / name
    snd.parent.mkdir(parents=True, exist_ok=True)
    snd.write_bytes(b"ID3" + b"\x00" * 32)
    db.upsert(sample_record(path=str(snd), filename=snd.name, ext=snd.suffix,
                            codec=None, duration_s=30.0, fps=0.0, has_audio=1))
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 30.0)
    monkeypatch.setattr(codec, "probe_codec", lambda path, **k: "mjpeg")

    with db.get_conn() as conn:
        mid = conn.execute("SELECT id FROM media WHERE path=?", (str(snd),)).fetchone()[0]

    assert fastapi_client.get("/api/stream/%d" % mid).status_code == 200
    with db.get_conn() as conn:
        stored = conn.execute("SELECT codec FROM media WHERE id=?", (mid,)).fetchone()[0]
    assert stored is None


# ── real ffmpeg layer ────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def media_dir(tmp_path_factory):
    if not HAVE_FFMPEG:
        pytest.skip("needs ffmpeg/ffprobe")
    d = tmp_path_factory.mktemp("cover_art")

    def ff(*args):
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True,
                       capture_output=True, cwd=str(d))

    ff("-f", "lavfi", "-i", "sine=d=1", "a.wav")
    ff("-f", "lavfi", "-i", "color=red:s=300x300", "-frames:v", "1", "cover.jpg")
    for out, acodec, extra in (("art.mp3", "libmp3lame", ["-id3v2_version", "3"]),
                               ("art.m4a", "aac", []),
                               ("art.flac", "flac", [])):
        ff("-i", "a.wav", "-i", "cover.jpg", "-map", "0", "-map", "1",
           "-c:a", acodec, "-c:v", "mjpeg", *extra,
           "-disposition:v", "attached_pic", out)
    ff("-f", "lavfi", "-i", "testsrc=d=1:s=320x240:r=25", "-c:v", "libx264",
       "-pix_fmt", "yuv420p", "v.mp4")
    return d


@needs_ffmpeg
@pytest.mark.parametrize("name", ["art.mp3", "art.m4a", "art.flac"])
def test_real_cover_art_file_through_ingest_probe(media_dir, name):
    # Precondition: the file really does carry an attached picture, otherwise
    # this test would pass on a file that never had the bug.
    raw = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type:stream_disposition=attached_pic", "-of", "json",
         str(media_dir / name)],
        capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert any(s.get("disposition", {}).get("attached_pic") == 1
               for s in json.loads(raw)["streams"])

    meta = ingest.probe(str(media_dir / name))
    assert meta is not None
    assert meta["codec"] is None
    assert meta["fps"] is None
    assert meta["width"] is None and meta["height"] is None
    assert meta["has_audio"] == 1


@needs_ffmpeg
@pytest.mark.parametrize("name", ["art.mp3", "art.m4a", "art.flac"])
def test_real_cover_art_file_through_probe_codec(media_dir, name):
    codec._codec_cache.clear()
    assert codec.probe_codec(str(media_dir / name)) is None


@needs_ffmpeg
@pytest.mark.parametrize("name,expected", [("cover.jpg", "mjpeg"), ("v.mp4", "h264")])
def test_real_stills_and_video_still_probe(media_dir, name, expected):
    """`V:0` must not cost us the picture of a JPEG still or a real video."""
    codec._codec_cache.clear()
    assert codec.probe_codec(str(media_dir / name)) == expected


def test_camera_report_probe_asks_for_capital_V(monkeypatch):
    camera_report = importlib.import_module("camera_report")
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(camera_report.subprocess, "run", run)
    camera_report._probe_codec("/x/song.mp3")
    i = seen[0].index("-select_streams")
    assert seen[0][i + 1] == "V:0"
