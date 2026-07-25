"""Tests for issue #218 — preflight the vision model before a batch.

A fresh project whose settings table is empty resolves the vision model to the
hardcoded default; if that model isn't pulled, the old behavior was to 404 on
every frame and then halt with a misleading message. `is_model_installed` gives
the ingest preflight a strict, tag-exact signal so it can fail loud instead.
"""
import importlib
import types

import pytest


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def vision():
    return importlib.import_module("vision")


@pytest.fixture
def ingest():
    return importlib.import_module("ingest")


def _patch_tags(vision, monkeypatch, names, raise_exc=False):
    def fake_get(url, timeout=None):
        if raise_exc:
            raise OSError("connection refused")
        return _FakeResp({"models": [{"name": n} for n in names]})
    monkeypatch.setattr(vision.requests, "get", fake_get)


# ── vision.is_model_installed ───────────────────────────────────────────────
def test_installed_exact_tag_true(vision, monkeypatch):
    _patch_tags(vision, monkeypatch, ["qwen3-vl:8b", "bge-m3:latest"])
    assert vision.is_model_installed("qwen3-vl:8b") is True


def test_absent_but_reachable_false(vision, monkeypatch):
    # The exact 404-spam scenario: a bare model whose tag isn't pulled.
    _patch_tags(vision, monkeypatch, ["qwen3-vl:8b"])
    assert vision.is_model_installed("qwen2.5vl:7b") is False


def test_same_base_different_tag_is_false(vision, monkeypatch):
    # model_available() would say True by base match; the strict check must not,
    # because /api/generate needs the exact tag.
    _patch_tags(vision, monkeypatch, ["qwen2.5vl:3b"])
    assert vision.is_model_installed("qwen2.5vl:7b") is False


def test_bare_name_normalizes_to_latest(vision, monkeypatch):
    _patch_tags(vision, monkeypatch, ["llava:latest"])
    assert vision.is_model_installed("llava") is True


def test_unreachable_returns_none(vision, monkeypatch):
    _patch_tags(vision, monkeypatch, [], raise_exc=True)
    assert vision.is_model_installed("qwen3-vl:8b") is None


def test_empty_tags_unconfirmable_none(vision, monkeypatch):
    _patch_tags(vision, monkeypatch, [])
    assert vision.is_model_installed("qwen3-vl:8b") is None


def test_empty_name_none(vision):
    assert vision.is_model_installed("") is None


# ── ingest._vision_model_abort_message ──────────────────────────────────────
def test_abort_message_lists_installed_vision_models(ingest):
    msg = ingest._vision_model_abort_message("qwen2.5vl:7b", ["qwen3-vl:8b", "llava:latest"])
    assert "qwen2.5vl:7b" in msg                     # names the offending model
    assert "not installed" in msg
    assert "qwen3-vl:8b" in msg and "llava:latest" in msg
    assert "ARKIV_OLLAMA_VISION_MODEL" in msg        # actionable


def test_abort_message_no_vision_models_suggests_pull(ingest):
    msg = ingest._vision_model_abort_message("qwen2.5vl:7b", [])
    assert "ollama pull" in msg
