"""Round-5 #50 / R5-21 — the vision fallback model must actually run.

Before the fix, `vision._call_vision` re-read the model from settings on every
call, and the failure-fallback path tried to redirect it by swapping a module
global `vision.VISION_MODEL`. That swap was DEAD: `_call_vision` never read the
global, so the "fallback" silently re-ran the *same* failing primary model. The
log said "trying fallback minicpm-v" while minicpm-v was never invoked.

The fix threads an explicit `model` argument from `describe_frames` down to
`_call_vision`. These tests mock `_call_vision` at that boundary and assert the
fallback model name actually reaches it — which is impossible under the old code
(where `_call_vision` had no `model` parameter at all).
"""
import importlib
import json

import pytest

import config


@pytest.fixture
def vis():
    return importlib.import_module("vision")


@pytest.fixture
def ing():
    return importlib.import_module("ingest")


def test_describe_frames_threads_model_to_call_vision(vis, monkeypatch):
    """An explicit model passed to describe_frames must reach _call_vision verbatim."""
    seen = []

    def fake_call_vision(img_path, prompt, max_retries=2, model=None):
        seen.append(model)
        return json.dumps({"description": "ok", "tags": ["t"]})

    monkeypatch.setattr(vis, "_call_vision", fake_call_vision)
    vis.describe_frames(["/only.jpg"], model="minicpm-v:latest")

    assert seen == ["minicpm-v:latest"]


def test_fallback_model_name_reaches_call_vision(vis, ing, monkeypatch):
    """End-to-end via the ingest fallback orchestrator: the primary attempt runs
    with the default (None) model and fails; the fallback attempt must invoke
    _call_vision with the *configured fallback* model name — the exact thing the
    dead VISION_MODEL swap failed to do."""
    seen_models = []

    def fake_call_vision(img_path, prompt, max_retries=2, model=None):
        seen_models.append(model)
        # Only the fallback model produces a usable result; the primary (model=None)
        # fails, forcing the fallback path.
        if model and "minicpm" in model:
            return json.dumps({"description": "後備模型描述", "tags": ["a"]})
        raise RuntimeError("primary vision model failed")

    monkeypatch.setattr(vis, "_call_vision", fake_call_vision)
    # Fallback path gates on availability; treat the fallback as installed so the
    # rescue runs without a live Ollama.
    monkeypatch.setattr(vis, "model_available", lambda name: True)

    results, still_failed = ing._describe_frames_with_fallback(["/frame_0.jpg"])

    fallback = config.VISION_FALLBACK_MODEL
    assert "minicpm" in fallback, "sanity: fallback must differ from the primary"
    # Primary attempt: default model (None). Fallback attempt: the configured name.
    assert seen_models[0] is None
    assert fallback in seen_models[1:], seen_models
    # And the fallback actually rescued the frame.
    assert still_failed == []
    assert results[0]["description"] == "後備模型描述"
