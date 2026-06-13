"""Tests for Phase 8.3b — dual-fisheye 360 reproject in frame extraction.

A dual-fisheye .insv/.360 must be stitched into a full equirectangular panorama
before frames go to vision (Phase 8.3a POC: raw fisheye buries the wearer + on-screen
text in the distorted edge; the equirect stitch surfaces them). Normal video is
untouched.
"""
import importlib
import types

import pytest

frames = importlib.import_module("frames")


@pytest.fixture(autouse=True)
def _clear_cache():
    frames._is_360_cache.clear()
    yield
    frames._is_360_cache.clear()


# ── _frame_vf_args ──────────────────────────────────────────────────────────
def test_normal_video_plain_scale(monkeypatch):
    monkeypatch.setattr(frames, "_is_360_dualfisheye", lambda p: False)
    assert frames._frame_vf_args("clip.mp4") == ["-vf", "scale=320:-1"]


def test_360_video_reproject_filter(monkeypatch):
    monkeypatch.setattr(frames, "_is_360_dualfisheye", lambda p: True)
    args = frames._frame_vf_args("clip.insv")
    assert args[0] == "-filter_complex"
    fc = args[1]
    assert "[0:v:0][0:v:1]hstack" in fc       # both VIDEO lenses (audio-position-safe)
    assert "v360=dfisheye:equirect" in fc     # fisheye → equirect
    assert "scale=1024" in fc                 # larger than normal so text survives
    assert args[2:] == ["-map", "[o]"]


# ── _is_360_dualfisheye ─────────────────────────────────────────────────────
def test_ext_gate_skips_non_360(monkeypatch):
    # A .mp4 must short-circuit before any ffprobe call.
    called = {"n": 0}
    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("ffprobe should not run for .mp4")
    monkeypatch.setattr(frames.subprocess, "run", boom)
    assert frames._is_360_dualfisheye("clip.mp4") is False
    assert called["n"] == 0


def _fake_ffprobe(stream_indices, rc=0):
    out = "\n".join(str(i) for i in stream_indices) + "\n"
    return lambda *a, **k: types.SimpleNamespace(returncode=rc, stdout=out, stderr="")


def test_two_streams_is_360(monkeypatch, tmp_path):
    f = tmp_path / "clip.insv"; f.write_bytes(b"x")
    monkeypatch.setattr(frames.subprocess, "run", _fake_ffprobe([0, 1]))
    assert frames._is_360_dualfisheye(str(f)) is True


def test_single_stream_not_360(monkeypatch, tmp_path):
    # A single-lens file mislabeled .360 must NOT try to hstack a missing stream.
    f = tmp_path / "clip.360"; f.write_bytes(b"x")
    monkeypatch.setattr(frames.subprocess, "run", _fake_ffprobe([0]))
    assert frames._is_360_dualfisheye(str(f)) is False


def test_probe_failure_not_cached(monkeypatch, tmp_path):
    # Transient probe failure → False, and NOT cached (retries; succeeds next time).
    f = tmp_path / "clip.insv"; f.write_bytes(b"x")
    monkeypatch.setattr(frames.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("ffprobe missing")))
    assert frames._is_360_dualfisheye(str(f)) is False
    monkeypatch.setattr(frames.subprocess, "run", _fake_ffprobe([0, 1]))
    assert frames._is_360_dualfisheye(str(f)) is True  # not stuck on the failed result


def test_nonzero_rc_not_cached(monkeypatch, tmp_path):
    f = tmp_path / "clip.insv"; f.write_bytes(b"x")
    monkeypatch.setattr(frames.subprocess, "run", _fake_ffprobe([0, 1], rc=1))
    assert frames._is_360_dualfisheye(str(f)) is False
    monkeypatch.setattr(frames.subprocess, "run", _fake_ffprobe([0, 1], rc=0))
    assert frames._is_360_dualfisheye(str(f)) is True


def test_result_cached_by_mtime_size(monkeypatch, tmp_path):
    f = tmp_path / "clip.insv"; f.write_bytes(b"x")
    calls = {"n": 0}
    def once(*a, **k):
        calls["n"] += 1
        return types.SimpleNamespace(returncode=0, stdout="0\n1\n", stderr="")
    monkeypatch.setattr(frames.subprocess, "run", once)
    frames._is_360_dualfisheye(str(f))
    frames._is_360_dualfisheye(str(f))
    assert calls["n"] == 1  # second call served from (path,mtime,size) cache


def test_missing_file_not_360(monkeypatch):
    # os.stat fails on a nonexistent path → False, no probe.
    monkeypatch.setattr(frames.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no probe")))
    assert frames._is_360_dualfisheye("/nope/clip.insv") is False


# ── issue #53: --refresh forces frame re-extraction (force= bypasses reuse) ──
def _patch_thumbs(monkeypatch, tmp_path):
    monkeypatch.setattr(frames, "THUMBNAILS_DIR", tmp_path)
    monkeypatch.setattr(frames, "_is_360_dualfisheye", lambda p: False)
    calls = {"n": 0}
    def fake_run(cmd, out_path=None, timeout=60):
        calls["n"] += 1
        if out_path is not None:
            out_path.write_bytes(b"\xff\xd8\xff")  # pretend a jpg was written
        return True
    monkeypatch.setattr(frames, "_run_ffmpeg", fake_run)
    return calls


def test_force_false_reuses_existing_frame(monkeypatch, tmp_path):
    calls = _patch_thumbs(monkeypatch, tmp_path)
    stem = frames._safe_stem("/x/clip.mp4")
    (tmp_path / f"{stem}_frame0.jpg").write_bytes(b"\xff\xd8\xff")  # pre-existing
    frames._extract_fixed_persistent("/x/clip.mp4", 5.0, 30.0, stem, n_frames=1, force=False)
    assert calls["n"] == 0  # existing thumbnail reused, no ffmpeg


def test_force_true_reextracts(monkeypatch, tmp_path):
    calls = _patch_thumbs(monkeypatch, tmp_path)
    stem = frames._safe_stem("/x/clip.mp4")
    (tmp_path / f"{stem}_frame0.jpg").write_bytes(b"\xff\xd8\xff")  # pre-existing
    frames._extract_fixed_persistent("/x/clip.mp4", 5.0, 30.0, stem, n_frames=1, force=True)
    assert calls["n"] == 1  # force bypasses reuse → re-extracted


def test_extract_frames_threads_force(monkeypatch, tmp_path):
    calls = _patch_thumbs(monkeypatch, tmp_path)
    stem = frames._safe_stem("/x/clip.mp4")
    (tmp_path / f"{stem}_frame0.jpg").write_bytes(b"\xff\xd8\xff")
    frames.extract_frames("/x/clip.mp4", 5.0, 30.0, force=True)  # short clip → fixed path
    assert calls["n"] >= 1  # force propagated through extract_frames


# ── Codex fix: a failed forced re-extract must NOT destroy the prior good file ─
def test_failed_force_reextract_preserves_old_thumbnail(monkeypatch, tmp_path):
    monkeypatch.setattr(frames, "THUMBNAILS_DIR", tmp_path)
    monkeypatch.setattr(frames, "_ensure_thumbnails_dir", lambda: None)
    monkeypatch.setattr(frames, "_is_360_dualfisheye", lambda p: False)
    stem = frames._safe_stem("/x/clip.mp4")
    canonical = tmp_path / f"{stem}.jpg"
    canonical.write_bytes(b"OLDGOOD")
    # ffmpeg "fails" (and a real _run_ffmpeg would unlink its temp output)
    monkeypatch.setattr(frames, "_run_ffmpeg",
                        lambda cmd, out, timeout=60: (out.unlink(missing_ok=True), False)[1])
    res = frames.extract_thumbnail("/x/clip.mp4", 10.0, force=True)
    assert res is None
    assert canonical.read_bytes() == b"OLDGOOD"   # prior thumbnail survives (atomic temp)
    import glob
    assert not glob.glob(str(tmp_path / f"{stem}.tmp.*.jpg"))  # temp cleaned up


def test_temp_keeps_image_suffix_for_ffmpeg(monkeypatch, tmp_path):
    # ffmpeg infers the output format from the extension; the temp MUST end in the
    # real image suffix (.jpg), not a bare .tmp, or ffmpeg aborts with "Unable to
    # choose an output format" (Codex). Pin the temp filename shape.
    monkeypatch.setattr(frames, "THUMBNAILS_DIR", tmp_path)
    monkeypatch.setattr(frames, "_is_360_dualfisheye", lambda p: False)
    seen = {}
    def cap(cmd, out, timeout=60):
        seen["out"] = out
        out.write_bytes(b"\xff\xd8\xff")
        return True
    monkeypatch.setattr(frames, "_run_ffmpeg", cap)
    frames._extract_frame_to("/x/clip.mp4", 1.0, tmp_path / "poster.jpg")
    assert seen["out"].suffix == ".jpg"   # ffmpeg can infer jpeg from the temp
    assert ".tmp." in seen["out"].name    # …but a distinct temp, not the canonical
    assert seen["out"].name != "poster.jpg"
