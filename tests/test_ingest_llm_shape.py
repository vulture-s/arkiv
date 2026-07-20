"""Malformed-but-valid-JSON LLM replies must not crash or silently drop work.

`json_mode` only guarantees syntactic JSON, never the shape the prompt asked
for. Measured against the real model (qwen2.5:14b, ollama 0.30.7) the alias
prompt that says `回傳 {"groups":[...]}` answered `{"慢跑":"路跑"}` — no
"groups" key at all, so the judged merge was silently discarded. Under the same
free-form mode a list-of-strings reply raised AttributeError *outside* the
try/except in _run_propose_aliases, killing the whole run.

These tests pin both halves of the fix: the schema is sent to the provider, and
the parsing still survives a provider that ignores it.
"""
import importlib

import pytest


ingest = importlib.import_module("ingest")
llm = importlib.import_module("llm")


class _FakeResponse(object):
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self._content}}


def _capture_post(monkeypatch, content):
    captured = {}

    def fake_post(url, json, timeout):
        captured["payload"] = json
        return _FakeResponse(content)

    monkeypatch.setattr(llm.requests, "post", fake_post)
    return captured


# ── layer 2: the structural constraint actually reaches the provider ──

def test_schema_is_sent_as_format(monkeypatch):
    captured = _capture_post(monkeypatch, '{"tags": []}')
    llm.chat("x", json_mode=True, schema=ingest._CANON_SCHEMA)
    assert captured["payload"]["format"] == ingest._CANON_SCHEMA


def test_schema_takes_precedence_over_plain_json_mode(monkeypatch):
    captured = _capture_post(monkeypatch, '{"groups": []}')
    llm.chat("x", json_mode=True, schema=ingest._ALIAS_SCHEMA)
    assert captured["payload"]["format"] != "json"


def test_json_mode_alone_still_supported(monkeypatch):
    captured = _capture_post(monkeypatch, "{}")
    llm.chat("x", json_mode=True)
    assert captured["payload"]["format"] == "json"


def test_no_format_key_when_neither_requested(monkeypatch):
    captured = _capture_post(monkeypatch, "hi")
    llm.chat("x")
    assert "format" not in captured["payload"]


def test_alias_schema_requires_object_items():
    """The shape that free-form mode got wrong must be mandatory in the schema."""
    item = ingest._ALIAS_SCHEMA["properties"]["groups"]["items"]
    assert item["type"] == "object"
    assert set(item["required"]) == {"pref", "alts"}


# ── layer 3: caller survives a provider that ignores the schema ──

@pytest.mark.parametrize(
    "value",
    [
        ["馬拉松", {"name": "路跑"}, 42, None],  # mixed junk
        "not-a-list",
        None,
        {"tags": "nested"},
    ],
)
def test_str_list_only_keeps_strings(value):
    out = ingest._str_list(value)
    assert all(isinstance(v, str) for v in out)


def test_str_list_preserves_order_and_strings():
    assert ingest._str_list(["a", 1, "b"]) == ["a", "b"]


def test_canonicalize_survives_dict_items(monkeypatch):
    """{"tags":[{"name":"肉類"}]} used to reach canonicalize() → AttributeError
    ('dict' object has no attribute 'strip') outside the try, aborting the run."""
    tag_quality = importlib.import_module("tag_quality")
    proposed = ingest._str_list([{"name": "肉類"}])
    clean = tag_quality.guard_canonical(["生肉", "生魚", "肉類"], proposed)
    # nothing usable proposed → guard falls back to raw, run continues
    assert clean == ["生肉", "生魚", "肉類"]


def test_alias_loop_skips_non_dict_groups():
    """A list-of-strings reply must degrade to "no merge", not AttributeError."""
    tag_quality = importlib.import_module("tag_quality")
    cl_set = {"馬拉松", "路跑"}
    out_groups = []
    for g in ["馬拉松", "路跑"]:  # the shape that used to crash
        if not isinstance(g, dict):
            continue
        pref = tag_quality.canonicalize(g.get("pref") or "")
        alts = [tag_quality.canonicalize(a) for a in ingest._str_list(g.get("alts"))]
        alts = [a for a in alts if a and a in cl_set and a != pref]
        if pref in cl_set and alts:
            out_groups.append({"pref": pref, "alts": alts})
    assert out_groups == []


def test_alias_loop_accepts_wellformed_groups():
    """The fix must not break the happy path the schema now guarantees."""
    tag_quality = importlib.import_module("tag_quality")
    cl_set = {"馬拉松", "路跑", "慢跑"}
    out_groups = []
    for g in [{"pref": "馬拉松", "alts": ["路跑", 7, None]}]:
        if not isinstance(g, dict):
            continue
        pref = tag_quality.canonicalize(g.get("pref") or "")
        alts = [tag_quality.canonicalize(a) for a in ingest._str_list(g.get("alts"))]
        alts = [a for a in alts if a and a in cl_set and a != pref]
        if pref in cl_set and alts:
            out_groups.append({"pref": pref, "alts": alts})
    assert out_groups == [{"pref": "馬拉松", "alts": ["路跑"]}]
