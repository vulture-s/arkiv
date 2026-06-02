"""Phase 11.5b — ingest probe integration (_ensure_vision_ready).

Verifies the backpressure backoff + warm-up wiring with injected probe/sleep,
and the RED LINE: a degraded/disabled probe never blocks ingest.
"""
import importlib

import pytest


@pytest.fixture
def ingest_mod(tmp_db, monkeypatch):
    # tmp_db points db.DB_PATH at a temp DB so jobs.active_count() is hermetic.
    ingest = importlib.import_module("ingest")
    ingest = importlib.reload(ingest)
    # Don't fire real Ollama warm-up HTTP; just count calls.
    calls = {"warmup": 0}
    monkeypatch.setattr(ingest, "_warm_up_vision_model", lambda: calls.__setitem__("warmup", calls["warmup"] + 1))
    ingest._test_calls = calls
    return ingest


def _full(overrides):
    """Probe always returns the full key shape; tests merge onto that base."""
    base = {
        "degraded": False, "errors": [], "backend": "apple",
        "gpu_mem_used_mb": None, "gpu_mem_total_mb": None, "gpu_mem_pct": None,
        "ollama_vram_mb": 0.0, "models_loaded": [], "models_known": True,
        "system_mem_used_mb": 8000.0, "system_mem_total_mb": 16000.0,
        "system_mem_pct": 0.5, "active_jobs": None,
    }
    base.update(overrides)
    return base


def _probe_seq(*results):
    """A probe() stub that returns each result in turn, repeating the last."""
    seq = [_full(r) for r in results]

    def _p(active_jobs=None):
        r = seq.pop(0) if len(seq) > 1 else seq[0]
        return dict(r, active_jobs=active_jobs)
    return _p


# --------------------------------------------------------------------------
def test_proceeds_immediately_when_under_threshold(ingest_mod):
    sleeps = []
    probe = _probe_seq({"backend": "apple", "system_mem_pct": 0.4, "models_known": True, "models_loaded": ["qwen3-vl:8b"]})
    ingest_mod._ensure_vision_ready(_probe=probe, _sleep=sleeps.append)
    assert sleeps == []  # no backoff
    assert ingest_mod._test_calls["warmup"] == 0  # model already loaded


def test_backs_off_then_proceeds(ingest_mod):
    sleeps = []
    # busy, busy, then clear
    probe = _probe_seq(
        {"backend": "apple", "system_mem_pct": 0.95, "models_known": True, "models_loaded": ["qwen3-vl:8b"]},
        {"backend": "apple", "system_mem_pct": 0.95, "models_known": True, "models_loaded": ["qwen3-vl:8b"]},
        {"backend": "apple", "system_mem_pct": 0.4, "models_known": True, "models_loaded": ["qwen3-vl:8b"]},
    )
    ingest_mod._ensure_vision_ready(max_wait_s=120, _probe=probe, _sleep=sleeps.append)
    assert len(sleeps) == 2  # waited twice before clearing
    # exponential: 2s then 4s
    assert sleeps[0] == 2.0 and sleeps[1] == 4.0


def test_proceeds_after_max_wait_even_if_busy(ingest_mod):
    sleeps = []
    probe = _probe_seq({"backend": "apple", "system_mem_pct": 0.99, "models_known": True, "models_loaded": ["qwen3-vl:8b"]})
    # max_wait small -> total backoff is STRICTLY bounded by max_wait (Codex
    # SHOULD-FIX: clamp each sleep to the remaining budget, no overshoot).
    ingest_mod._ensure_vision_ready(max_wait_s=5, _probe=probe, _sleep=sleeps.append)
    assert sum(sleeps) <= 5  # never overshoots the budget
    assert sum(sleeps) == 5  # and uses it fully before giving up
    assert len(sleeps) <= 4  # didn't spin forever


def test_degraded_probe_proceeds_and_warms_up(ingest_mod):
    # No signal (psutil + ollama down) -> PROCEED, and warm up defensively
    # because model state is unknown.
    sleeps = []
    probe = _probe_seq({"backend": "unknown", "system_mem_pct": None, "models_known": False, "models_loaded": []})
    ingest_mod._ensure_vision_ready(_probe=probe, _sleep=sleeps.append)
    assert sleeps == []  # degraded never waits
    assert ingest_mod._test_calls["warmup"] == 1  # unknown -> defensive warm-up


def test_warms_up_when_model_not_loaded(ingest_mod):
    probe = _probe_seq({"backend": "apple", "system_mem_pct": 0.3, "models_known": True, "models_loaded": ["llama3:8b"]})
    ingest_mod._ensure_vision_ready(_probe=probe, _sleep=lambda s: None)
    assert ingest_mod._test_calls["warmup"] == 1


def test_skips_warmup_when_model_loaded(ingest_mod):
    probe = _probe_seq({"backend": "apple", "system_mem_pct": 0.3, "models_known": True, "models_loaded": ["qwen3-vl:8b"]})
    ingest_mod._ensure_vision_ready(_probe=probe, _sleep=lambda s: None)
    assert ingest_mod._test_calls["warmup"] == 0


def test_probe_that_raises_does_not_block_ingest(ingest_mod):
    # Codex CRITICAL-3: even if the probe itself raises, the vision boundary
    # treats it as degraded (PROCEED) and never propagates.
    sleeps = []

    def _boom(active_jobs=None):
        raise RuntimeError("probe blew up")

    ingest_mod._ensure_vision_ready(_probe=_boom, _sleep=sleeps.append)  # must not raise
    assert sleeps == []  # degraded -> no wait
    assert ingest_mod._test_calls["warmup"] == 1  # unknown -> defensive warm-up


def test_probe_disable_env_makes_it_proceed(ingest_mod, monkeypatch):
    # With the real probe under ARKIV_PROBE_DISABLE, decision is PROCEED and
    # (models unknown) it warms up — never blocks.
    monkeypatch.setenv("ARKIV_PROBE_DISABLE", "true")
    sleeps = []
    ingest_mod._ensure_vision_ready(_sleep=sleeps.append)
    assert sleeps == []
    assert ingest_mod._test_calls["warmup"] == 1
