"""Microtasks B3/B4/B6 — ingest CLI polish."""
import importlib

import pytest

import db


@pytest.fixture
def ingest_mod(tmp_db):
    ingest = importlib.import_module("ingest")
    return importlib.reload(ingest)


# --------------------------------------------------------------------------
# B3: .mkv/.avi/.webm are treated as video (so they get thumbnail/frames/vision)
# --------------------------------------------------------------------------
def test_b3_video_ext_includes_mkv_avi_webm(ingest_mod):
    for ext in (".mkv", ".avi", ".webm"):
        assert ext in ingest_mod.VIDEO_EXT, f"{ext} should be a video ext"


def test_b3_video_ext_subset_of_supported(ingest_mod):
    assert ingest_mod.VIDEO_EXT <= ingest_mod.SUPPORTED


# --------------------------------------------------------------------------
# 360 rigs: .insv (Insta360) / .360 (GoPro Max) are HEVC-in-MOV/MP4 — ffmpeg
# probes + extracts frames. Verified 2026-06-12 on a real .insv (dual 2880×2880
# HEVC fisheye + AAC, thumbnail decodes). Competitor StoryCube shows the same
# files as UNKNOWN; arkiv must treat them as first-class video.
# --------------------------------------------------------------------------
def test_360_formats_are_video(ingest_mod):
    for ext in (".insv", ".360"):
        assert ext in ingest_mod.VIDEO_EXT, f"{ext} should be a video ext"
        assert ext in ingest_mod.SUPPORTED, f"{ext} should be ingestible"


# --------------------------------------------------------------------------
# B6: size helpers
# --------------------------------------------------------------------------
def test_b6_dir_size_bytes(ingest_mod, tmp_path):
    assert ingest_mod._dir_size_bytes(tmp_path / "missing") == 0
    d = tmp_path / "sizedir"  # isolated — tmp_path also holds the tmp_db test.db
    d.mkdir()
    (d / "a.mp4").write_bytes(b"x" * 100)
    (d / "b.mp4").write_bytes(b"y" * 50)
    assert ingest_mod._dir_size_bytes(d) == 150


def test_b6_fmt_size_delta(ingest_mod):
    assert ingest_mod._fmt_size_delta(2_500_000) == "+2.5 MB"
    assert ingest_mod._fmt_size_delta(-3_000_000) == "-3.0 MB"
    assert ingest_mod._fmt_size_delta(0) == "+0.0 MB"


# --------------------------------------------------------------------------
# B4: --regenerate-thumbnails rebuilds poster for video records only
# --------------------------------------------------------------------------
def test_b4_regenerate_thumbnails_video_only(ingest_mod, sample_record, tmp_path, monkeypatch):
    vid = tmp_path / "a.mp4"; vid.write_text("x")
    aud = tmp_path / "b.mp3"; aud.write_text("x")
    db.upsert(sample_record(path=str(vid), filename="a.mp4", ext=".mp4", thumbnail_path=None))
    db.upsert(sample_record(path=str(aud), filename="b.mp3", ext=".mp3", thumbnail_path=None))

    monkeypatch.setattr(ingest_mod, "probe", lambda p: {"duration_s": 10.0, "fps": 30})
    fake_thumb = tmp_path / "thumb_a.jpg"; fake_thumb.write_text("t")
    calls = []
    def _fake_extract(src, dur, force=False):
        calls.append((src, force))
        return str(fake_thumb)
    monkeypatch.setattr(ingest_mod.frm, "extract_thumbnail", _fake_extract)

    ingest_mod._regenerate_thumbnails()

    # only the video source was processed, AND force=True so it truly rebuilds
    # (Codex SHOULD-FIX: without force, extract_thumbnail reuses the old poster).
    assert len(calls) == 1 and calls[0][0].endswith("a.mp4")
    assert calls[0][1] is True, "must force=True or regeneration is a no-op"
    assert db.get_record_by_id(1)["thumbnail_path"]       # video updated
    assert not db.get_record_by_id(2)["thumbnail_path"]   # audio left alone


def test_b4_regenerate_thumbnails_skips_missing_source(ingest_mod, sample_record, monkeypatch):
    # path points nowhere -> skipped, no crash
    db.upsert(sample_record(path="/nonexistent/x.mp4", filename="x.mp4", ext=".mp4"))
    called = []
    monkeypatch.setattr(ingest_mod.frm, "extract_thumbnail", lambda *a: called.append(1))
    ingest_mod._regenerate_thumbnails()  # must not raise
    assert called == []


def test_b4_extract_thumbnail_force_bypasses_cache(monkeypatch, tmp_path):
    import frames as frm
    monkeypatch.setattr(frm, "THUMBNAILS_DIR", tmp_path)
    monkeypatch.setattr(frm, "_ensure_thumbnails_dir", lambda: None)
    # Thumbnails are now keyed by stem + abs-path hash (C1: kill cross-card
    # same-name collisions), so the pre-existing poster must use the safe stem.
    (tmp_path / f"{frm._safe_stem('/x/clip.mp4')}.jpg").write_text("old")
    ran = []
    monkeypatch.setattr(frm, "_run_ffmpeg", lambda cmd, out: ran.append(1) or True)

    frm.extract_thumbnail("/x/clip.mp4", 10.0, force=False)
    assert ran == []  # reuse — no rebuild
    frm.extract_thumbnail("/x/clip.mp4", 10.0, force=True)
    assert ran == [1]  # force actually re-runs ffmpeg


def test_b4_no_videos_is_noop(ingest_mod, sample_record, monkeypatch):
    db.upsert(sample_record(path="/m/only.mp3", filename="only.mp3", ext=".mp3"))
    monkeypatch.setattr(ingest_mod.frm, "extract_thumbnail",
                        lambda *a: pytest.fail("should not be called"))
    ingest_mod._regenerate_thumbnails()  # prints "No video records..." and returns
