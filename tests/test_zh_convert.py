"""Phase 9.8b: Simplified→Traditional (Taiwan) transcript conversion.

Verifies the two-config contract: transcript + segments get Taiwan idioms (s2twp),
word tokens get char-level s2t (timing-safe), non-zh passes through, and a missing
opencc degrades to identity (never crashes a transcribe)."""
import importlib

import pytest

zh = importlib.import_module("zh_convert")

_HAVE_OPENCC = zh._converter("s2t") is not None
_skip_no_opencc = pytest.mark.skipif(not _HAVE_OPENCC, reason="opencc not installed")


@_skip_no_opencc
def test_s2t_is_neutral_traditional_and_length_preserving():
    simp = "我在看这个视频，内存和软件"
    trad = zh.to_traditional(simp)
    assert len(trad) == len(simp)      # char-level → word-timing-safe
    assert "视" not in trad            # simplified converted
    assert "記憶體" not in trad        # s2t is NEUTRAL: 内存 stays 內存, no idiom


@_skip_no_opencc
def test_s2twp_applies_taiwan_idioms():
    tw = zh.to_taiwan("内存和软件的视频")
    assert "記憶體" in tw and "軟體" in tw and "影片" in tw  # Taiwan phrase idioms


@_skip_no_opencc
def test_convert_result_idioms_everywhere_and_timing_safe():
    text = "内存和软件"
    segments = [{"start": 0.0, "end": 1.0, "text": "内存和软件"}]
    words = [{"word": "内存", "start": 0.0, "end": 0.5, "score": 0.9}]
    t, lang, segs, wds = zh.convert_result(text, "zh", segments, words)
    assert "記憶體" in t                                       # transcript idioms
    assert "記憶體" in segs[0]["text"]                         # segment idioms
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 1.0   # segment timing intact
    # word token ALSO gets Taiwan idioms (內存→記憶體) — and its timestamps survive
    # despite the length change (2→3 chars), because start/end/score are copied.
    assert wds[0]["word"] == "記憶體"
    assert wds[0]["start"] == 0.0 and wds[0]["end"] == 0.5 and wds[0]["score"] == 0.9


def test_convert_result_non_zh_passthrough():
    text, lang, segs, wds = zh.convert_result(
        "hello world", "en",
        [{"start": 0, "end": 1, "text": "hello"}],
        [{"word": "hello", "start": 0, "end": 1}],
    )
    assert text == "hello world" and segs[0]["text"] == "hello" and wds[0]["word"] == "hello"


def test_degrades_to_identity_without_opencc(monkeypatch):
    monkeypatch.setattr(zh, "_converter", lambda cfg: None)  # simulate missing opencc
    assert zh.to_taiwan("内存") == "内存"        # passthrough, no crash
    assert zh.to_traditional("视频") == "视频"
    t, _, segs, wds = zh.convert_result(
        "内存", "zh",
        [{"start": 0, "end": 1, "text": "内存"}],
        [{"word": "内存", "start": 0, "end": 1}],
    )
    assert t == "内存" and segs[0]["text"] == "内存" and wds[0]["word"] == "内存"


def test_empty_and_none_are_safe():
    assert zh.to_taiwan("") == "" and zh.to_traditional("") == ""
    assert zh.convert_result("", "zh", [], []) == ("", "zh", [], [])
    assert zh.is_zh("zh") and zh.is_zh("ZH-CN") and not zh.is_zh("en") and not zh.is_zh(None)


@_skip_no_opencc
def test_transcribe_wires_zh_conversion(monkeypatch):
    """transcribe() must apply the store-time conversion to whatever the backend
    returns — the single wiring point (transcribe.py return zh_convert.convert_result)."""
    import importlib
    tr = importlib.import_module("transcribe")
    monkeypatch.setattr(tr, "_to_wav", lambda p: "/tmp/arkiv_zhwire_nonexistent.wav")
    monkeypatch.setattr(tr, "_USE_MLX", True)
    monkeypatch.setattr(tr, "_vad_filter", lambda w: w)  # pass through, no cleanup branch
    monkeypatch.setattr(tr, "_transcribe_mlx", lambda w, lang: (
        "内存和软件", "zh",
        [{"start": 0.0, "end": 1.0, "text": "内存和软件"}],
        [{"word": "内存", "start": 0.0, "end": 0.5}],
    ))
    text, lang, segs, words = tr.transcribe("dummy.mp4", language="zh")
    assert "記憶體" in text and "記憶體" in segs[0]["text"]  # idioms applied via s2twp
    assert words[0]["word"] == "記憶體"                      # words get idioms too
