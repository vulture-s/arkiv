"""Unit tests for codec.py — lightweight ffprobe wrapper + tri-state needs_proxy."""
import importlib


def test_needs_proxy_returns_needed_for_hevc(monkeypatch):
    codec = importlib.import_module("codec")
    codec.clear_cache()
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: "hevc")
    assert codec.needs_proxy("/tmp/x.mov") == codec.NEEDED


def test_needs_proxy_returns_not_needed_for_h264(monkeypatch):
    codec = importlib.import_module("codec")
    codec.clear_cache()
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: "h264")
    assert codec.needs_proxy("/tmp/x.mp4") == codec.NOT_NEEDED


def test_needs_proxy_returns_unknown_when_probe_fails(monkeypatch):
    """Tri-state: ffprobe 失敗（None）→ UNKNOWN，呼叫端決定 fallback。"""
    codec = importlib.import_module("codec")
    codec.clear_cache()
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: None)
    assert codec.needs_proxy("/tmp/x.mp4") == codec.UNKNOWN


def test_needs_proxy_handles_all_proxy_codecs(monkeypatch):
    """ProRes 變體（ap4h/ap4x/apch/...）和 HEVC 變體（hev1）都應該 NEEDED。"""
    codec = importlib.import_module("codec")
    for c in ["hevc", "hev1", "prores", "ap4h", "ap4x", "apch", "apcn", "apcs", "apco"]:
        codec.clear_cache()
        monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0, _c=c: _c)
        assert codec.needs_proxy("/tmp/x") == codec.NEEDED, f"{c} should need proxy"


def test_probe_codec_caches_by_mtime_and_size(tmp_path, monkeypatch):
    """同一個 (path, mtime, size) 不該重跑 ffprobe；換內容該失效。"""
    codec = importlib.import_module("codec")
    codec.clear_cache()
    fp = tmp_path / "clip.mov"
    fp.write_bytes(b"abc")

    calls = {"n": 0}

    class _FakeRun:
        def __init__(self, stdout):
            self.stdout = stdout

    def _fake_run(cmd, **kwargs):
        calls["n"] += 1
        return _FakeRun("hevc\n")

    monkeypatch.setattr("subprocess.run", _fake_run)

    a = codec.probe_codec(str(fp))
    b = codec.probe_codec(str(fp))
    assert a == b == "hevc"
    assert calls["n"] == 1, "second call should hit cache"

    # Mutate file → cache key changes → ffprobe re-runs
    fp.write_bytes(b"abcdef")
    codec.probe_codec(str(fp))
    assert calls["n"] == 2


def test_probe_codec_returns_none_when_file_missing():
    codec = importlib.import_module("codec")
    codec.clear_cache()
    assert codec.probe_codec("/tmp/this-file-does-not-exist-arkiv-test.mov") is None


def test_ingest_needs_proxy_shim_preserves_bool_contract(monkeypatch):
    """ingest.needs_proxy 對外仍是 bool — 老 callers（Phase 3 ingestion 流程）不破。"""
    import ingest, codec
    codec.clear_cache()
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: "hevc")
    assert ingest.needs_proxy("/tmp/x.mov") is True
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: "h264")
    assert ingest.needs_proxy("/tmp/x.mp4") is False
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: None)
    # UNKNOWN → False 跟舊 except→False 行為一致
    assert ingest.needs_proxy("/tmp/x.mov") is False
