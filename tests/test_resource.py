"""Phase 11.5a — resource_probe tests.

All external sources (Ollama HTTP, nvidia-smi, psutil) are mocked. The central
invariant under test: probe() degrades to None and never raises, and decide()
PROCEEDs whenever the pressure signal is absent (probe is a sensor, not a gate).
"""
import io
import json
import types

import pytest

import resource_probe as rp


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _fake_urlopen(payload):
    def _open(req, timeout=None):
        return _FakeResp(json.dumps(payload).encode())
    return _open


def _fake_psutil(percent=42.0, total=32_000_000_000, available=18_000_000_000):
    mod = types.SimpleNamespace()
    mod.virtual_memory = lambda: types.SimpleNamespace(
        percent=percent, total=total, available=available
    )
    return mod


@pytest.fixture(autouse=True)
def _clear_disable(monkeypatch):
    monkeypatch.delenv("ARKIV_PROBE_DISABLE", raising=False)


# --------------------------------------------------------------------------
# probe() — happy path
# --------------------------------------------------------------------------
def test_probe_apple_normal(monkeypatch):
    monkeypatch.setattr(rp, "_detect_backend", lambda: "apple")
    monkeypatch.setattr(rp.urllib.request, "urlopen", _fake_urlopen(
        {"models": [{"name": "qwen3-vl:8b", "size_vram": 8_000_000_000}]}
    ))
    monkeypatch.setattr(rp, "_psutil", _fake_psutil(percent=42.0))

    r = rp.probe()
    assert r["degraded"] is False
    assert r["backend"] == "apple"
    assert r["models_loaded"] == ["qwen3-vl:8b"]
    assert r["models_known"] is True
    assert r["ollama_vram_mb"] == 8000.0
    assert r["system_mem_pct"] == 0.42
    # Apple has no discrete VRAM -> gpu fields stay None
    assert r["gpu_mem_pct"] is None


def test_probe_nvidia_normal(monkeypatch):
    monkeypatch.setattr(rp, "_detect_backend", lambda: "nvidia")
    monkeypatch.setattr(rp.urllib.request, "urlopen", _fake_urlopen({"models": []}))
    monkeypatch.setattr(rp, "_probe_nvidia", lambda timeout=3.0: (4096.0, 8192.0))
    monkeypatch.setattr(rp, "_psutil", _fake_psutil(percent=30.0))

    r = rp.probe()
    assert r["degraded"] is False
    assert r["gpu_mem_pct"] == 0.5
    assert r["gpu_mem_used_mb"] == 4096.0
    assert r["models_loaded"] == []
    assert r["models_known"] is True


def test_probe_passes_active_jobs(monkeypatch):
    monkeypatch.setattr(rp, "_detect_backend", lambda: "apple")
    monkeypatch.setattr(rp.urllib.request, "urlopen", _fake_urlopen({"models": []}))
    monkeypatch.setattr(rp, "_psutil", _fake_psutil())
    r = rp.probe(active_jobs=3)
    assert r["active_jobs"] == 3


# --------------------------------------------------------------------------
# probe() — degrade paths (RED LINE: never raise)
# --------------------------------------------------------------------------
def test_probe_ollama_unreachable_degrades(monkeypatch):
    def _boom(req, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(rp, "_detect_backend", lambda: "apple")
    monkeypatch.setattr(rp.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(rp, "_psutil", _fake_psutil())

    r = rp.probe()
    assert r["degraded"] is True
    assert r["models_known"] is False
    assert r["models_loaded"] == []
    assert any("ollama" in e for e in r["errors"])
    # but system memory still read
    assert r["system_mem_pct"] is not None


def test_probe_ollama_malformed_json_degrades(monkeypatch):
    def _garbage(req, timeout=None):
        return _FakeResp(b"<html>not json</html>")
    monkeypatch.setattr(rp, "_detect_backend", lambda: "apple")
    monkeypatch.setattr(rp.urllib.request, "urlopen", _garbage)
    monkeypatch.setattr(rp, "_psutil", _fake_psutil())

    r = rp.probe()
    assert r["degraded"] is True
    assert r["models_known"] is False


def test_probe_psutil_missing_degrades(monkeypatch):
    monkeypatch.setattr(rp, "_detect_backend", lambda: "apple")
    monkeypatch.setattr(rp.urllib.request, "urlopen", _fake_urlopen({"models": []}))
    monkeypatch.setattr(rp, "_psutil", None)

    r = rp.probe()
    assert r["degraded"] is True
    assert r["system_mem_pct"] is None
    assert r["system_mem_used_mb"] is None
    assert any("psutil" in e for e in r["errors"])


def test_probe_nvidia_smi_fails_degrades(monkeypatch):
    def _boom(timeout=3.0):
        raise FileNotFoundError("nvidia-smi")
    monkeypatch.setattr(rp, "_detect_backend", lambda: "nvidia")
    monkeypatch.setattr(rp.urllib.request, "urlopen", _fake_urlopen({"models": []}))
    monkeypatch.setattr(rp, "_probe_nvidia", _boom)
    monkeypatch.setattr(rp, "_psutil", _fake_psutil())

    r = rp.probe()
    assert r["degraded"] is True
    assert r["gpu_mem_pct"] is None
    assert any("nvidia-smi" in e for e in r["errors"])


def test_probe_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("ARKIV_PROBE_DISABLE", "true")
    # even if sources would work, disabled wins and nothing is called
    monkeypatch.setattr(rp, "_detect_backend", lambda: (_ for _ in ()).throw(AssertionError("should not run")))
    r = rp.probe(active_jobs=7)
    assert r["degraded"] is True
    assert r["backend"] == "unknown"
    assert r["models_loaded"] == []
    assert r["active_jobs"] == 7


# --------------------------------------------------------------------------
# pressure_metric / decide — the backpressure table
# --------------------------------------------------------------------------
def test_pressure_metric_nvidia_uses_gpu():
    r = {"backend": "nvidia", "gpu_mem_pct": 0.9, "system_mem_pct": 0.2}
    assert rp.pressure_metric(r) == 0.9


def test_pressure_metric_apple_uses_system():
    r = {"backend": "apple", "gpu_mem_pct": None, "system_mem_pct": 0.55}
    assert rp.pressure_metric(r) == 0.55


def test_pressure_metric_none_when_degraded():
    r = {"backend": "unknown", "gpu_mem_pct": None, "system_mem_pct": None}
    assert rp.pressure_metric(r) is None


def test_decide_wait_when_over_threshold():
    r = {"backend": "apple", "gpu_mem_pct": None, "system_mem_pct": 0.85}
    decision, reason = rp.decide(r, threshold=0.8)
    assert decision == "WAIT"
    assert "waiting" in reason


def test_decide_proceed_when_under_threshold():
    r = {"backend": "apple", "gpu_mem_pct": None, "system_mem_pct": 0.5}
    decision, _ = rp.decide(r, threshold=0.8)
    assert decision == "PROCEED"


def test_decide_proceed_when_degraded_red_line():
    # No signal must never block ingest.
    r = {"backend": "unknown", "gpu_mem_pct": None, "system_mem_pct": None}
    decision, reason = rp.decide(r, threshold=0.8)
    assert decision == "PROCEED"
    assert "degraded" in reason


def test_decide_uses_config_default_threshold(monkeypatch):
    monkeypatch.setattr(rp.config, "GPU_MEM_THRESHOLD", 0.5)
    r = {"backend": "apple", "system_mem_pct": 0.6}
    decision, _ = rp.decide(r)
    assert decision == "WAIT"


# --------------------------------------------------------------------------
# is_model_loaded
# --------------------------------------------------------------------------
def test_is_model_loaded_exact():
    r = {"models_known": True, "models_loaded": ["qwen3-vl:8b"]}
    assert rp.is_model_loaded(r, "qwen3-vl:8b") is True


def test_is_model_loaded_bare_name():
    r = {"models_known": True, "models_loaded": ["qwen3-vl:8b"]}
    assert rp.is_model_loaded(r, "qwen3-vl") is True


def test_is_model_loaded_absent():
    r = {"models_known": True, "models_loaded": ["llama3:8b"]}
    assert rp.is_model_loaded(r, "qwen3-vl:8b") is False


def test_is_model_loaded_unknown_returns_false():
    # Ollama unreachable -> don't claim loaded -> caller warms up defensively.
    r = {"models_known": False, "models_loaded": []}
    assert rp.is_model_loaded(r, "qwen3-vl:8b") is False


# --------------------------------------------------------------------------
# summary_line — must not crash on any shape
# --------------------------------------------------------------------------
def test_summary_line_degraded_does_not_crash():
    r = rp._degraded_result("test")
    line = rp.summary_line(r)
    assert "degraded" in line
    assert "unknown" in line


def test_summary_line_nvidia():
    r = {
        "backend": "nvidia", "gpu_mem_pct": 0.5,
        "gpu_mem_used_mb": 4096.0, "gpu_mem_total_mb": 8192.0,
        "models_loaded": ["qwen3-vl:8b"], "active_jobs": 2, "degraded": False,
    }
    line = rp.summary_line(r)
    assert "GPU 50%" in line
    assert "qwen3-vl:8b" in line
    assert "active jobs: 2" in line
