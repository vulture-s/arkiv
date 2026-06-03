"""Phase 12.5 — subtitle layout engine tests."""
import importlib
import json

import pytest

import db
import subtitle as sub


# --------------------------------------------------------------------------
# width / cjk detection
# --------------------------------------------------------------------------
def test_is_cjk():
    assert sub.is_cjk("中")
    assert sub.is_cjk("あ")
    assert sub.is_cjk("，")  # fullwidth punct
    assert not sub.is_cjk("a")
    assert not sub.is_cjk("1")
    assert not sub.is_cjk(" ")


def test_display_units_cjk_vs_latin():
    assert sub.display_units("中文") == pytest.approx(2.0)
    assert sub.display_units("abc") == pytest.approx(1.0)  # 3 latin = 1 unit


# --------------------------------------------------------------------------
# wrap — line length cap
# --------------------------------------------------------------------------
def test_wrap_caps_cjk_line_length():
    text = "一二三四五六七八九十一二三四五六七八九十"  # 20 CJK
    lines = sub.wrap(text, max_units=14)
    assert len(lines) == 2
    for ln in lines:
        assert sub.display_units(ln) <= 14


def test_wrap_short_text_single_line():
    assert sub.wrap("你好世界", max_units=14) == ["你好世界"]


def test_wrap_empty():
    assert sub.wrap("", max_units=14) == []
    assert sub.wrap("   ", max_units=14) == []


# --------------------------------------------------------------------------
# natural break points
# --------------------------------------------------------------------------
def test_wrap_breaks_after_punctuation():
    # Should break after the comma, not mid-clause.
    text = "今天天氣很好，我們一起去公園散步好嗎"
    lines = sub.wrap(text, max_units=8)
    assert lines[0].endswith("，")


def test_wrap_never_splits_latin_word():
    text = "the quick brown fox jumps over lazy dog again now"
    lines = sub.wrap(text, max_units=6)
    rejoined = " ".join(lines).split()
    assert "quick" in rejoined and "jumps" in rejoined
    # no fragment of a word appears split across lines
    for w in ("quick", "brown", "jumps"):
        assert any(w == tok for ln in lines for tok in ln.split())


def test_wrap_keeps_number_with_measure_word():
    # "14字" must not break between the number and 字.
    text = "每行最多14字才符合規範這條規則很重要喔"
    lines = sub.wrap(text, max_units=8)
    # find which line holds the digits; it must also hold 字
    for ln in lines:
        if "14" in ln:
            assert "14字" in ln


def test_wrap_oversized_atom_gets_own_line():
    # A single Latin word longer than the budget shouldn't be split.
    text = "supercalifragilisticexpialidocious yes"
    lines = sub.wrap(text, max_units=3)
    assert "supercalifragilisticexpialidocious" in lines


def test_wrap_width_is_hard_invariant():
    # Codex SHOULD-FIX: every line must be <= max_units (no merge-overflow that
    # violates the cap). Only an unbreakable atom may exceed it.
    text = "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸"  # 20 CJK
    for mu in (3, 5, 8, 14):
        for ln in sub.wrap(text, max_units=mu):
            assert sub.display_units(ln) <= mu


# --------------------------------------------------------------------------
# SRT rendering
# --------------------------------------------------------------------------
def test_ts_format():
    assert sub._ts(0) == "00:00:00,000"
    assert sub._ts(3661.5) == "01:01:01,500"


def test_ts_rounding_spill():
    # 0.9999s rounds ms to 1000 -> must carry into seconds, not emit ,1000
    assert sub._ts(0.9999) == "00:00:01,000"


def test_ts_rounding_carries_to_minutes_and_hours():
    # Codex CRITICAL: spill must carry s->m->h, never emit 00:00:60,000.
    assert sub._ts(59.9999) == "00:01:00,000"
    assert sub._ts(3599.9999) == "01:00:00,000"
    assert sub._ts(3599.4) == "00:59:59,400"


def test_segments_to_srt_basic():
    segs = [
        {"start": 0.0, "end": 2.0, "text": "你好世界"},
        {"start": 2.0, "end": 4.0, "text": "再見"},
    ]
    srt = sub.segments_to_srt(segs)
    assert "1\n00:00:00,000 --> 00:00:02,000\n你好世界\n" in srt
    assert "2\n00:00:02,000 --> 00:00:04,000\n再見\n" in srt


def test_segments_to_srt_skips_empty():
    segs = [{"start": 0, "end": 1, "text": ""}, {"start": 1, "end": 2, "text": "有字"}]
    srt = sub.segments_to_srt(segs)
    assert srt.count("-->") == 1  # only the non-empty cue


def test_segments_to_srt_splits_long_segment_into_timed_cues():
    # 40 CJK at max_units=14, max_lines=2 -> 3 lines -> 2 cues, time split.
    segs = [{"start": 0.0, "end": 6.0,
             "text": "一二三四五六七八九十一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未"}]
    srt = sub.segments_to_srt(segs, max_units=14, max_lines=2)
    cue_count = srt.count("-->")
    assert cue_count >= 2  # split into multiple timed cues
    # cues are contiguous and within [0,6]: first starts at 0, last ends at 6
    assert "00:00:00,000 -->" in srt
    assert "--> 00:00:06,000" in srt
    # every text line stays within the width cap
    for block in srt.strip().split("\n\n"):
        for line in block.splitlines()[2:]:
            assert sub.display_units(line) <= 14


def test_segments_to_srt_bilingual():
    segs = [{"start": 0, "end": 2, "text": "你好", "translation": "Hello"}]
    srt = sub.segments_to_srt(segs, translate_key="translation")
    assert "你好" in srt and "Hello" in srt
    # original above translation
    assert srt.index("你好") < srt.index("Hello")


# --------------------------------------------------------------------------
# export.py srt integration
# --------------------------------------------------------------------------
@pytest.fixture
def ex(tmp_db):
    export = importlib.import_module("export")
    return importlib.reload(export)


def test_export_srt_uses_segments(ex, sample_record):
    segs = json.dumps([{"start": 0.0, "end": 2.0, "text": "字幕測試一段"}])
    db.upsert(sample_record(path="/m/s.mp4", segments_json=segs))
    out = ex.export_srt(1)
    assert "字幕測試一段" in out
    assert "00:00:00,000 --> 00:00:02,000" in out


def test_export_srt_falls_back_to_transcript(ex, sample_record):
    db.upsert(sample_record(path="/m/n.mp4", transcript="沒有分段也要能出字幕", duration_s=3.0))
    out = ex.export_srt(1)
    assert "00:00:00,000 --> 00:00:03,000" in out


def test_export_srt_missing_raises(ex):
    with pytest.raises(KeyError):
        ex.export_srt(99999)


def test_export_srt_non_list_segments_falls_back(ex, sample_record):
    # Codex SHOULD-FIX: a dict (not a list) in segments_json must not crash —
    # fall back to the transcript path.
    db.upsert(sample_record(path="/m/bad.mp4", segments_json='{"not":"a list"}',
                            transcript="壞分段也要能出字幕", duration_s=2.0))
    out = ex.export_srt(1)
    assert "壞分段也要能出字幕" in out
    assert "00:00:00,000 --> 00:00:02,000" in out


def test_export_srt_segments_with_non_dict_items_filtered(ex, sample_record):
    db.upsert(sample_record(path="/m/mix.mp4",
                            segments_json='[{"start":0,"end":1,"text":"好"}, "garbage", 42]',
                            transcript="fallback", duration_s=5.0))
    out = ex.export_srt(1)
    assert "好" in out  # the one valid dict segment is used, junk filtered
