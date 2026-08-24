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


# ── layout / render split (Phase 12.5 → the HTTP export rewiring) ─────────────
# `segments_to_srt` was the layout engine AND the only way to reach it, which is
# exactly why every HTTP export grew its own cue emitter instead. Layout now lives
# in `layout_cues`; SRT and VTT are two renderers over the same list.

GOLDEN_SEGMENTS = [
    {"start": 0.0, "end": 3.0, "text": "今天天氣很好，我們去了海邊。"},
    {"start": 3.0, "end": 4.0, "text": "   "},
    {"start": 4.0, "end": 16.0,
     "text": "這是一段很長的旁白，長到一行字幕根本放不下，所以引擎會把它拆成好幾個 cue，時間按比例分。"},
    {"start": 16.0, "end": 19.5, "text": "Hello world, state-of-the-art 3.5公斤。",
     "translation": "你好世界。"},
]

# Captured from the pre-refactor implementation. A refactor that changes one byte
# of this changes what lands in someone's Resolve timeline.
GOLDEN_SRT = (
    "1\n00:00:00,000 --> 00:00:03,000\n今天天氣很好，我們去了海邊。\n"
    "\n"
    "2\n00:00:04,000 --> 00:00:10,000\n這是一段很長的旁白，\n長到一行字幕根本放不下，\n"
    "\n"
    "3\n00:00:10,000 --> 00:00:16,000\n所以引擎會把它拆成好幾個\ncue，時間按比例分。\n"
    "\n"
    "4\n00:00:16,000 --> 00:00:19,500\nHello world, state-of-the-art 3.5公斤。\n你好世界。\n"
)


def test_srt_layout_is_byte_identical_after_the_extraction():
    """`restrict_punct=False` is the pre-punctuation-policy output. Layout has not
    moved a byte since the layout/render split; only the punctuation policy did."""
    assert sub.segments_to_srt(GOLDEN_SEGMENTS, translate_key="translation",
                               restrict_punct=False) == GOLDEN_SRT


def test_layout_cues_returns_start_end_lines():
    cues = sub.layout_cues(GOLDEN_SEGMENTS, translate_key="translation")

    assert cues[0] == (0.0, 3.0, ["今天天氣很好，我們去了海邊。"])
    assert all(isinstance(lines, list) for _s, _e, lines in cues)


def test_layout_cues_drops_blank_segments():
    """The blank third segment must not become an empty cue with a live timestamp."""
    cues = sub.layout_cues(GOLDEN_SEGMENTS)
    assert all(any(ln.strip() for ln in lines) for _s, _e, lines in cues)
    assert not any(3.0 <= s < 4.0 for s, _e, _lines in cues)


def test_layout_cues_splits_a_long_segment_and_divides_the_span():
    cues = sub.layout_cues([{"start": 4.0, "end": 16.0, "text": GOLDEN_SEGMENTS[2]["text"]}])

    assert len(cues) == 2
    assert cues[0][0] == 4.0 and cues[1][1] == 16.0
    assert cues[0][1] == cues[1][0] == 10.0  # contiguous, evenly split by cue count


def test_layout_cues_keeps_a_bilingual_segment_as_one_cue():
    cues = sub.layout_cues([GOLDEN_SEGMENTS[3]], translate_key="translation")

    assert len(cues) == 1
    assert cues[0][2][-1] == "你好世界。"


def test_vtt_is_the_same_layout_in_webvtt_syntax():
    srt = sub.segments_to_srt(GOLDEN_SEGMENTS, translate_key="translation")
    vtt = sub.segments_to_vtt(GOLDEN_SEGMENTS, translate_key="translation")
    # 。 is gone from both by then — the punctuation policy, not the renderer.

    assert vtt.startswith("WEBVTT\n\n")
    # Same cue count, same text, same times — only the syntax differs.
    assert vtt.count(" --> ") == srt.count(" --> ")
    assert "00:00:00.000 --> 00:00:03.000" in vtt
    assert "00:00:00,000" not in vtt, "comma separator is SRT's, not WebVTT's"
    for line in ("今天天氣很好，我們去了海邊", "cue，時間按比例分", "你好世界"):
        assert line in vtt


def test_vtt_carries_no_cue_numbers():
    """WebVTT identifiers are optional and the exports users already have omit
    them. Adding them now would change every downloaded file."""
    vtt = sub.segments_to_vtt(GOLDEN_SEGMENTS)
    for block in vtt.split("\n\n")[1:]:
        if block.strip():
            assert block.lstrip().startswith("00:"), "cue must open with its timing line"


def test_both_renderers_read_the_same_layout(monkeypatch):
    """The point of the seam: change layout once, both formats follow. A renderer
    that quietly kept its own copy would ignore this."""
    monkeypatch.setattr(sub, "layout_cues", lambda *a, **k: [(1.0, 2.0, ["哨兵"])])

    assert "哨兵" in sub.segments_to_srt(GOLDEN_SEGMENTS)
    assert "哨兵" in sub.segments_to_vtt(GOLDEN_SEGMENTS)


def test_a_blank_original_is_dropped_even_when_a_translation_exists():
    """The `if not text` guard earns its place only here — in the monolingual path
    `wrap("  ")` already returns no lines. With a translate_key, dropping the guard
    would emit a cue carrying the translation alone, over the silence's timestamp."""
    cues = sub.layout_cues(
        [{"start": 1.0, "end": 2.0, "text": "   ", "translation": "你好世界。"}],
        translate_key="translation",
    )
    assert cues == []


def test_whitespace_only_text_never_becomes_a_cue():
    """Tabs and newlines count as blank too. (Two guards can catch this — the
    `if not text` skip above and the `if not lines` skip after wrapping — and only
    the first is reachable; the second stays as defence, not as behaviour.)"""
    assert sub.layout_cues([{"start": 1.0, "end": 2.0, "text": " \t\n "}]) == []


# ── the punctuation policy (product decision, from Penny's transcripts) ──────
# On screen a cue's own start/end already says "pause here", so 。、；：…— and
# quote marks compete with a 14-unit line budget for nothing. Subtitles keep
# ，！？ only. The stored transcript is untouched, so .txt stays complete —
# one test below pins both halves at once, because the decision IS both halves.

PUNCT_SAMPLE = "他說：「這很好。」對嗎？我想是的，3.5公斤、12:30、50%"


def test_subtitles_keep_only_the_three_marks():
    out = sub.restrict_punctuation(PUNCT_SAMPLE)

    for gone in "。：「」、…；—":
        assert gone not in out, "{0} should not survive into a cue".format(gone)
    assert "，" in out and "？" in out


def test_numbers_and_hyphenated_words_survive_intact():
    """The exemption that makes an allowlist unnecessary: a mark with ASCII
    alphanumerics on both sides is structural, not clause-ending."""
    out = sub.restrict_punctuation("3.5公斤 12:30 don't state-of-the-art 50% a,b")

    assert "3.5" in out
    assert "12:30" in out
    assert "don't" in out
    assert "state-of-the-art" in out
    assert "50%" in out
    assert "a,b" in out  # ASCII comma is in the keep list anyway


def test_a_fullwidth_mark_between_digits_is_still_dropped():
    """The exemption requires the MARK to be ASCII too. `12:30、50%` — that 、 has a
    digit on each side but it is a list separator, not part of either number."""
    assert "、" not in sub.restrict_punctuation("12:30、50%")


def test_removing_a_mark_does_not_leave_a_double_space():
    assert sub.restrict_punctuation("word — word") == "word word"
    assert sub.restrict_punctuation("…leading and trailing…") == "leading and trailing"


def test_a_line_of_pure_punctuation_disappears_rather_than_rendering_blank():
    srt = sub.segments_to_srt([{"start": 0.0, "end": 1.0, "text": "「。」"}])
    assert srt.count("-->") == 1        # the cue and its timing are still real
    assert "「" not in srt and "。" not in srt


def test_an_emptied_line_is_removed_not_left_as_a_blank_row():
    """A bilingual cue whose translation is all punctuation. Keeping the emptied
    line would put a blank row inside the cue — which in SRT reads as the end of
    the cue, so every parser downstream sees a stray fragment."""
    srt = sub.segments_to_srt(
        [{"start": 0.0, "end": 1.0, "text": "今天天氣很好", "translation": "……"}],
        translate_key="translation",
    )

    assert srt == "1\n00:00:00,000 --> 00:00:01,000\n今天天氣很好\n"


def test_currency_and_math_are_not_punctuation():
    """Unicode calls these Sc/Sm, not P. Dropping them would mangle prices."""
    assert sub.restrict_punctuation("$30 + 5 = 35") == "$30 + 5 = 35"


def test_the_policy_is_off_when_the_caller_says_so():
    """`restrict_punct=False` is what .txt-shaped callers use. Without a way off,
    the decision would not be reversible."""
    assert "。" in sub.segments_to_srt([{"start": 0.0, "end": 1.0, "text": "好。"}],
                                      restrict_punct=False)


def test_both_renderers_apply_the_policy():
    segs = [{"start": 0.0, "end": 1.0, "text": PUNCT_SAMPLE}]
    for out in (sub.segments_to_srt(segs), sub.segments_to_vtt(segs)):
        assert "。" not in out and "「" not in out
        assert "，" in out and "？" in out


def test_layout_still_sees_the_punctuation_it_breaks_on():
    """Order matters: 。、；： are `wrap()`'s most valuable break points. Stripping
    before layout would leave it breaking on width alone."""
    text = "第一句話說完了。第二句話也說完了。第三句話同樣說完了。"
    cues = sub.layout_cues([{"start": 0.0, "end": 9.0, "text": text}])
    lines = [ln for _s, _e, ls in cues for ln in ls]

    assert len(lines) > 1
    assert all(ln.endswith("。") for ln in lines[:-1]), (
        "layout must break after 。 — it can only do that if it still sees them"
    )
    # ...and the rendered cue has none of them left.
    assert "。" not in sub.segments_to_srt([{"start": 0.0, "end": 9.0, "text": text}])
