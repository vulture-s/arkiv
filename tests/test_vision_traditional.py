"""Vision tags and structured fields still leaked Simplified into the index.

Phase 9.8b routed the frame *description* through `zh_convert` because qwen3-vl
ignores the "繁體中文" instruction and emits Simplified anyway. It converted one
field out of eleven.

Tags are the expensive omission, because **tags are index keys**. A Simplified tag
is a permanently unfindable one: the user searches 場景 and the library holds 场景.
Worse, once both spellings exist they are two separate tags and the pool splits.
The structured fields (`content_type`, `atmosphere`, `energy`, `edit_position`,
`edit_reason`) are Chinese phrases too, and they are displayed and filtered on.

The fix is one normalisation of the whole result at each return, rather than four
per-field assignments — four assignments is how one came to be missing.
"""
from __future__ import annotations

import json

import pytest

import vision as vis
import zh_convert

# opencc has no cp39 arm64 wheel, so `zh_convert` degrades to identity on the 3.9
# CI leg — the same guard the Phase 9.8b tests use. Skipping is right: identity is
# a deliberate, tested degradation, and asserting conversion there would be
# asserting that opencc is installed, which is a packaging question, not this one.
_needs_opencc = pytest.mark.skipif(
    zh_convert._converter("s2twp") is None, reason="opencc not installed"
)


SIMPLIFIED = {
    "description": "这是一个访谈场景，人物在讲话。",
    "tags": ["访谈", "场景", "人物"],
    "content_type": "访谈",
    "focus_score": 5,
    "exposure": "正常",
    "stability": "稳定",
    "audio_quality": "清晰",
    "atmosphere": "严肃",
    "energy": "中",
    "edit_position": "中段",
    "edit_reason": "适合作为开场",
}


def _vision_returns(monkeypatch, raw):
    monkeypatch.setattr(vis, "_call_vision", lambda *a, **k: raw)


@_needs_opencc
def test_tags_are_traditionalised(monkeypatch):
    """The one that costs recall: a Simplified tag can never be searched for."""
    _vision_returns(monkeypatch, json.dumps(SIMPLIFIED, ensure_ascii=False))

    result = vis._describe_one("/fake.jpg")

    assert result["tags"] == ["訪談", "場景", "人物"]


@_needs_opencc
def test_the_structured_fields_are_traditionalised(monkeypatch):
    _vision_returns(monkeypatch, json.dumps(SIMPLIFIED, ensure_ascii=False))

    result = vis._describe_one("/fake.jpg")

    assert result["content_type"] == "訪談"
    assert result["stability"] == "穩定"
    assert result["atmosphere"] == "嚴肅"
    assert result["edit_reason"] == "適合作為開場"


@_needs_opencc
def test_the_description_still_is_too(monkeypatch):
    """The one field that already worked — a regression here would be silent."""
    _vision_returns(monkeypatch, json.dumps(SIMPLIFIED, ensure_ascii=False))

    assert "訪談" in vis._describe_one("/fake.jpg")["description"]
    assert "这" not in vis._describe_one("/fake.jpg")["description"]


def test_a_numeric_field_survives_conversion(monkeypatch):
    """`focus_score` is an int, and opencc raises on one (`'int' object has no
    attribute 'encode'`).

    Two things stop that reaching anyone: the `isinstance(value, str)` guard here,
    and `zh_convert._convert` swallowing converter exceptions and returning its
    input. So no single mutation makes this red — it pins the outcome, which is
    what a stored `focus_score` of 5 depends on."""
    _vision_returns(monkeypatch, json.dumps(SIMPLIFIED, ensure_ascii=False))

    assert vis._describe_one("/fake.jpg")["focus_score"] == 5


def test_a_missing_field_stays_none(monkeypatch):
    """Absent is not the same as empty: a NULL column means "the model did not
    say", and "" would claim it answered with nothing."""
    _vision_returns(monkeypatch, json.dumps(
        {"description": "简单", "tags": []}, ensure_ascii=False))

    result = vis._describe_one("/fake.jpg")

    assert result["content_type"] is None
    assert result["focus_score"] is None


@_needs_opencc
def test_the_light_path_converts_the_same_things(monkeypatch):
    """Every frame except the representative one goes through this path — it is
    the majority of what lands in the index."""
    _vision_returns(monkeypatch, json.dumps(SIMPLIFIED, ensure_ascii=False))

    result = vis._describe_one_light("/fake.jpg")

    assert result["tags"] == ["訪談", "場景", "人物"]
    assert result["content_type"] == "訪談"


@_needs_opencc
def test_the_non_json_fallback_path_converts_too(monkeypatch):
    """When the model answers in prose instead of JSON, the tags come off the last
    line. Same index, same requirement."""
    _vision_returns(monkeypatch, "这是访谈画面\n访谈, 场景, 人物")

    result = vis._describe_one("/fake.jpg")

    assert result["tags"] == ["訪談", "場景", "人物"]
    assert "這" in result["description"]


@_needs_opencc
def test_the_light_non_json_fallback_converts_too(monkeypatch):
    _vision_returns(monkeypatch, "这是访谈画面\n访谈, 场景")

    result = vis._describe_one_light("/fake.jpg")

    assert result["tags"] == ["訪談", "場景"]


@_needs_opencc
def test_text_that_is_already_traditional_is_untouched(monkeypatch):
    """Conversion must be idempotent — re-running vision on an existing library
    must not churn its tags."""
    _vision_returns(monkeypatch, json.dumps(
        {"description": "訪談畫面", "tags": ["訪談", "場景"], "content_type": "訪談"},
        ensure_ascii=False))

    result = vis._describe_one("/fake.jpg")

    assert result["tags"] == ["訪談", "場景"]
    assert result["description"] == "訪談畫面"
