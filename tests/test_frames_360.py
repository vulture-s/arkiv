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
