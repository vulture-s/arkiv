"""Round 5 image-support: still images (png/jpg/webp/gif/svg) are first-class
media assets. Raster images get a thumbnail + one vision frame like video; SVG
has no pixel stream so it gets neither (frontend shows a placeholder).
"""
import ingest
import frames as frm
from pathlib import Path


def _stub_light(monkeypatch, tmp_path):
    """Stub the ffmpeg/ffprobe/exif heavy steps so process_file only builds the
    record. Mirrors tests/test_audit_20260610.py's H6 stub."""
    monkeypatch.setattr(ingest, "exiftool_extract", lambda *a, **k: {})
    monkeypatch.setattr(ingest, "parse_xavc_sidecar", lambda p: {})
    monkeypatch.setattr(ingest, "_file_hash", lambda p: "deadbeef")


def test_raster_image_gets_thumbnail_and_frame(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "probe", lambda p: {
        "duration_s": 0.0, "size_mb": 0.1, "width": 1920, "height": 1080,
        "fps": None, "has_audio": 0, "start_tc": None, "codec": "png",
    })
    monkeypatch.setattr(ingest.frm, "extract_thumbnail",
                        lambda *a, **k: str(tmp_path / "t.jpg"))
    monkeypatch.setattr(ingest.frm, "extract_frames", lambda *a, **k: [
        {"index": 0, "timestamp_s": 0.0, "thumbnail_path": str(tmp_path / "f.jpg")},
    ])
    _stub_light(monkeypatch, tmp_path)

    f = tmp_path / "still.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n")  # probe is stubbed; content irrelevant
    rec = ingest.process_file(f, skip_vision=True)
    assert rec.get("thumbnail_path"), "raster image must get a thumbnail"
    frames = rec.get("_frames")
    assert isinstance(frames, list) and len(frames) == 1, "raster image yields 1 frame"
    assert rec.get("transcript") is None, "images have no speech to transcribe"
    assert rec.get("ext") == ".png"


def test_svg_image_gets_no_pixel_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "probe", lambda p: None)  # ffprobe can't read SVG
    calls = {"thumb": 0, "frames": 0}

    def _thumb(*a, **k):
        calls["thumb"] += 1
        return str(tmp_path / "t.jpg")

    def _frames(*a, **k):
        calls["frames"] += 1
        return []

    monkeypatch.setattr(ingest.frm, "extract_thumbnail", _thumb)
    monkeypatch.setattr(ingest.frm, "extract_frames", _frames)
    _stub_light(monkeypatch, tmp_path)

    f = tmp_path / "logo.svg"
    f.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    rec = ingest.process_file(f, skip_vision=True)
    assert rec.get("thumbnail_path") is None, "SVG has no pixel stream → no thumbnail"
    assert rec.get("_frames") is None, "SVG has no pixel stream → no frames"
    assert calls["thumb"] == 0 and calls["frames"] == 0, "pixel steps must not run for SVG"
    assert rec.get("ext") == ".svg"
    assert rec.get("has_audio", 0) == 0


def test_query_builder_image_bucket(monkeypatch):
    import query_builder as qb
    sql, params, _sem = qb._one_condition(
        {"field": "media_type", "op": "eq", "value": "image"}
    )
    assert "ext" in sql.lower()
    assert ".png" in params
    assert ".mp4" not in params
    assert ".mp3" not in params
