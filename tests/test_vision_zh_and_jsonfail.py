"""vision.py write-path quality (audit 2026-07-30):
  Fix 1 — qwen3-vl ignores the "繁體中文" prompt and emits Simplified; the write-path
          never routed descriptions through zh_convert, so they were stored raw. Now every
          description goes through zh_convert.to_taiwan (s2twp), mirroring transcribe.py.
  Fix 3 — a truncated/garbled JSON reply (a bare "{") fell into the free-text fallback and
          was stored as description="{" with no error → passed downstream failure-detection.
          Now malformed-JSON / degenerate output is flagged as a failure instead of stored.
"""
import importlib

import pytest

vision = importlib.import_module("vision")
zh = importlib.import_module("zh_convert")

_HAVE_OPENCC = zh._converter("s2t") is not None
_skip_no_opencc = pytest.mark.skipif(not _HAVE_OPENCC, reason="opencc not installed")


# ── Fix 1: descriptions converted to Taiwan Traditional ───────────────────────
@_skip_no_opencc
def test_normalize_result_converts_description_to_traditional():
    r = vision._normalize_result({"description": "一台白色设备，黑色电缆", "tags": ["设备"]})
    assert "電纜" in r["description"]                     # 电缆→電纜 (设备→裝置, 台→臺 via s2twp)
    assert zh.classify_zh(r["description"]) == "traditional"   # no Simplified residue
    assert "设备" not in r["description"] and "电缆" not in r["description"]
    assert not r.get("error")


@_skip_no_opencc
def test_describe_one_json_success_converted(monkeypatch):
    monkeypatch.setattr(vision, "_call_vision", lambda *a, **k: '{"description": "黑色电缆", "tags": []}')
    r = vision._describe_one("/tmp/x.jpg")
    assert not r.get("error")
    assert "電纜" in r["description"] and "电缆" not in r["description"]


@_skip_no_opencc
def test_describe_one_plain_text_fallback_converted(monkeypatch):
    # genuine free-text (non-JSON) reply is kept AND converted, not flagged failed
    monkeypatch.setattr(vision, "_call_vision", lambda *a, **k: "一台设备在桌上")
    r = vision._describe_one("/tmp/x.jpg")
    assert not r.get("error")
    assert zh.classify_zh(r["description"]) == "traditional" and "设备" not in r["description"]


# ── Fix 3: malformed / degenerate JSON output is flagged as a failure ─────────
def test_normalize_result_non_dict_is_flagged_failure():
    # a double-encoded JSON string parses to a str/list, not a dict — a parse failure
    # masquerading as success; must be flagged, not have .get() blow up or store garbage.
    r = vision._normalize_result(["not", "a", "dict"])
    assert r.get("error")
    assert not r.get("description")


def test_describe_one_bare_brace_flagged_failure(monkeypatch):
    # the exact audit symptom: qwen3-vl returns a truncated "{" → must be a failure,
    # never description="{".
    monkeypatch.setattr(vision, "_call_vision", lambda *a, **k: "{")
    r = vision._describe_one("/tmp/x.jpg")
    assert r.get("error")
    assert r.get("description", "") == ""


def test_describe_one_truncated_json_flagged_failure(monkeypatch):
    monkeypatch.setattr(vision, "_call_vision", lambda *a, **k: '{"description": "一個人')
    r = vision._describe_one("/tmp/x.jpg")
    assert r.get("error")
    assert "{" not in (r.get("description") or "")


def test_describe_one_light_bare_brace_flagged_failure(monkeypatch):
    # the light path shares the bug; empty description → downstream marks it failed
    monkeypatch.setattr(vision, "_call_vision", lambda *a, **k: "{")
    r = vision._describe_one_light("/tmp/x.jpg")
    assert r.get("description", "") == ""
