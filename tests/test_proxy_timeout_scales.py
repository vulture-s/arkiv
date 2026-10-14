"""Proxy encode timeout must scale with clip length.

A flat 600s killed every long 4K HEVC clip (17–21 min clips need ~900–1100s on
an M2 Max), and each one burned the full 600s before failing.
"""
import importlib
import subprocess
import types

import pytest

ingest = importlib.import_module("ingest")
pathres = importlib.import_module("pathres")


@pytest.mark.parametrize("duration, expected", [
    (None, 600.0),          # unprobeable → old floor
    (0.0, 600.0),
    (30.0, 600.0),          # short clip keeps the floor
    (1254.0, 3762.0),       # 20.9 min clip → 3x realtime
    (10 ** 6, 4 * 3600.0),  # bogus duration is capped
])
def test_proxy_timeout_scales_with_duration(monkeypatch, duration, expected):
    monkeypatch.setattr(pathres, "_probe_duration", lambda p: duration)
    assert ingest._proxy_timeout_s("clip.mp4") == expected


def test_generate_proxy_passes_scaled_timeout_to_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest.config, "PROXIES_DIR", tmp_path)
    monkeypatch.setattr(pathres, "_probe_duration", lambda p: 1254.0)
    seen = {}

    def fake_run(cmd, **kw):
        seen["timeout"] = kw.get("timeout")
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    assert ingest.generate_proxy(1, str(tmp_path / "long.mp4"), hwaccel=False) is None
    assert seen["timeout"] == 3762.0


def test_probe_error_falls_back_to_floor(monkeypatch):
    def boom(p):
        raise RuntimeError("ffprobe exploded")
    monkeypatch.setattr(pathres, "_probe_duration", boom)
    assert ingest._proxy_timeout_s("clip.mp4") == 600.0
