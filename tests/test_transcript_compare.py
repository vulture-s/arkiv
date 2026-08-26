"""Running ASR twice is only useful if the disagreements are the output.

Two engines fail differently: Whisper flattens Taiwanese into Mandarin but gets
proper nouns right; Qwen3-ASR keeps the Taiwanese but writes proper nouns
phonetically. So agreement is shippable and disagreement is a short list worth a
human ear — which means the alignment has to survive the fact that the two engines
don't segment the same way at all.
"""
from __future__ import annotations

import pytest

import transcript_compare as tc


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


# ── alignment ────────────────────────────────────────────────────────────────

def test_segments_pair_by_time_not_by_index():
    """The engines emit different segment counts for the same speech. Pairing by
    index would misalign everything after the first difference — and the result
    would look like disagreement everywhere, burying the real ones."""
    a = [seg(0, 5, "一整句話講完")]
    b = [seg(0, 2, "一整句"), seg(2, 5, "話講完")]

    pairs = tc.align(a, b)

    assert len(pairs) == 2
    matched = [p for p in pairs if p[0] and p[1]]
    assert len(matched) == 1


def test_a_segment_pairs_with_the_one_it_overlaps_not_the_first_available():
    """The sharper version of the above. `a` sits at 10-12s; the first candidate in
    `b` is at 0-2s and shares nothing with it. Pairing greedily by availability
    rather than by overlap would marry those two and report a disagreement between
    sentences that were never spoken at the same time."""
    a = [seg(10, 12, "後半的話")]
    b = [seg(0, 2, "開頭的話"), seg(10, 12, "後半的話")]

    pairs = tc.align(a, b)
    matched = [(x, y) for x, y in pairs if x and y]

    assert len(matched) == 1
    assert matched[0][1]["text"] == "後半的話"


def test_a_segment_with_no_counterpart_pairs_with_none():
    """That is what makes a coverage hole visible, instead of silently shifting
    every later pair by one."""
    a = [seg(0, 2, "第一句"), seg(10, 12, "只有這邊有")]
    b = [seg(0, 2, "第一句")]

    pairs = tc.align(a, b)

    assert (None in [p[1] for p in pairs])
    assert len(pairs) == 2


def test_pairs_come_back_in_time_order():
    a = [seg(10, 12, "後面"), seg(0, 2, "前面")]
    b = [seg(0, 2, "前面")]

    starts = [float((p[0] or p[1])["start"]) for p in tc.align(a, b)]

    assert starts == sorted(starts)


def test_a_b_side_extra_segment_is_not_dropped():
    a = [seg(0, 2, "有")]
    b = [seg(0, 2, "有"), seg(5, 7, "b 多出來的")]

    pairs = tc.align(a, b)

    assert any(p[0] is None and p[1] is not None for p in pairs)


def test_barely_touching_segments_are_not_paired():
    """A 50 ms brush is two different utterances, not the same one."""
    a = [seg(0, 2.0, "甲")]
    b = [seg(1.95, 4.0, "乙")]

    pairs = tc.align(a, b, min_overlap_s=0.2)

    assert all(not (p[0] and p[1]) for p in pairs)


# ── classification ───────────────────────────────────────────────────────────

def test_identical_text_agrees():
    assert tc.classify(seg(0, 1, "一樣的字"), seg(0, 1, "一樣的字")) == tc.AGREE


def test_punctuation_and_spacing_are_not_disagreements():
    """The two engines punctuate differently by nature. Reporting that as something
    to review would bury the differences that matter."""
    assert tc.classify(seg(0, 1, "好，那我們開始。"), seg(0, 1, "好 那我們開始")) == tc.AGREE


def test_one_side_empty_is_a_coverage_hole():
    assert tc.classify(seg(0, 1, "有講話"), seg(0, 1, "")) == tc.COVERAGE
    assert tc.classify(None, seg(0, 1, "只有一邊聽到")) == tc.COVERAGE


def test_one_side_catching_a_fraction_is_also_coverage():
    """Not empty, but two words out of a sentence — same failure, and calling it a
    wording difference would send someone hunting for a nuance that isn't there."""
    assert tc.classify(seg(0, 3, "這一整句話都有被聽到而且很長"),
                       seg(0, 3, "這一整")) == tc.COVERAGE


def test_taiwanese_kept_on_one_side_only_is_flagged_as_taigi():
    """The failure this whole exercise exists for: one engine wrote the Taiwanese,
    the other flattened it into fluent Mandarin — which reads fine, so nothing else
    would ever catch it."""
    assert tc.classify(seg(0, 2, "我們今天來看"), seg(0, 2, "阮今仔日來看")) == tc.TAIGI


def test_taiwanese_on_both_sides_is_not_a_taigi_flag():
    """Both kept it — whatever they disagree about, it isn't the flattening."""
    kind = tc.classify(seg(0, 2, "阮遮有問題"), seg(0, 2, "阮遐有問題"))
    assert kind != tc.TAIGI


def test_markers_shared_with_mandarin_are_not_in_the_set():
    """伊 / 講 / 較 / 欲 are ordinary Mandarin too. A marker that fires on Mandarin
    makes the category cry wolf, and a category that cries wolf gets ignored —
    taking the real ones with it."""
    for ch in "伊講較欲":
        assert ch not in tc.TAIGI_MARKERS


def test_a_plain_wording_difference_is_other():
    """No evidence of either known failure. `other` means 'a person has to listen',
    not 'we classified it'."""
    assert tc.classify(seg(0, 2, "這個沒有問題"), seg(0, 2, "這個不是問題")) == tc.OTHER


def test_no_phonetic_category_is_invented():
    """Classifying 'proper noun written phonetically' needs a pronunciation table,
    and arkiv has none — opencc converts script, not sound. Anything that would
    require guessing at pronunciation must land in `other`."""
    kind = tc.classify(seg(0, 2, "富田的產品"), seg(0, 2, "福田的產品"))
    assert kind == tc.OTHER


# ── the whole thing ──────────────────────────────────────────────────────────

def test_compare_separates_shippable_from_reviewable():
    """A long shared passage, then a stretch only one side heard."""
    shared = "這一段兩邊都有而且完全一樣所以不需要任何人去聽"
    a = [seg(0, 6, shared), seg(6, 12, "這一長段只有其中一邊聽到完全沒有出現在另一份稿裡")]
    b = [seg(0, 6, shared)]

    out = tc.compare(a, b)

    assert out["agreed_chars"] >= len(shared) - 2
    assert out["by_kind"] == {tc.COVERAGE: 1}

def test_review_items_carry_both_readings_and_a_timestamp():
    """The point is to jump to the audio and listen. An item without a window, or
    with only one side, cannot be acted on."""
    out = tc.compare([seg(3, 5, "我們今天")], [seg(3, 5, "阮今仔日")])

    item = out["review"][0]
    # The window is the DIFFERING run, not the whole segment — that is the point:
    # it points at the part worth listening to, not at everything around it.
    assert 3 <= item["start"] <= item["end"] <= 5
    assert item["a"] and item["b"]


def test_two_identical_transcripts_produce_no_review():
    a = [seg(0, 2, "完全一樣"), seg(2, 4, "第二句")]
    out = tc.compare(a, list(a))
    assert out["review"] == []
    assert out["agreed_chars"] == out["total_chars"]


def test_empty_inputs_do_not_raise():
    assert tc.compare([], [])["review"] == []
    assert tc.compare([seg(0, 1, "只有一邊")], [])["by_kind"] == {tc.COVERAGE: 1}


def test_identical_text_agrees_even_without_timestamps():
    """Changed deliberately when alignment moved from time to text.

    Agreement is a property of the TEXT; timestamps only exist to point a human at
    the audio. Two identical transcripts agree whether or not they carry timings —
    the earlier "no timestamps means we cannot judge" rule belonged to segment
    pairing, where matching by position really would have been a guess."""
    out = tc.compare([{"start": None, "end": None, "text": "沒有時間"}],
                     [{"start": None, "end": None, "text": "沒有時間"}])

    assert out["agreed_chars"] > 0
    assert out["review"] == []


# ── the failure that only real data showed ───────────────────────────────────

def test_the_same_sentence_one_window_apart_is_not_two_coverage_holes():
    """Measured on a real 199 s clip (49 vs 64 segments): segment-to-segment
    pairing reported **1 agreement and 68 items to review**, and most of the
    "coverage holes" were text BOTH sides had, sitting one window apart because the
    two engines cut the speech differently.

        whisper  25.5-27.0  還是全部都夾好再調
        qwen     27.0-28.0  還是全部都夾好再調

    Aligning on text instead of boundaries is what fixes it — boundaries are an
    artefact of the engine, not of the speech."""
    a = [seg(25.5, 27.0, "還是全部都夾好再調"), seg(27.0, 28.0, "夾好再調")]
    b = [seg(27.0, 28.0, "還是全部都夾好再調")]

    out = tc.compare(a, b)

    assert out["agreed_chars"] >= len("還是全部都夾好再調") - 1
    assert len(out["review"]) <= 1, out["review"]


def test_a_short_word_swap_is_not_called_a_coverage_hole():
    """`我們` vs `阮` is a 2:1 length ratio, and the ratio rule was firing on it —
    swallowing the Taiwanese category it exists alongside. The rule only means
    something when the longer side is long enough to be 'a sentence vs a
    fragment'."""
    out = tc.compare([seg(0, 2, "我們今天")], [seg(0, 2, "阮今仔日")])

    assert tc.COVERAGE not in out["by_kind"]
    assert tc.TAIGI in out["by_kind"]


def test_agreement_is_counted_in_characters_not_runs():
    """"12 equal runs" is not something a person can act on; "1,180 of 1,240
    characters matched" says how much is left to listen to."""
    a = [seg(0, 4, "完全相同的一整句話")]
    out = tc.compare(a, list(a))

    assert out["agreed_chars"] == out["total_chars"] == len("完全相同的一整句話")


def test_a_run_of_one_word_disagreements_becomes_one_listening_window():
    """On messy audio the two engines argue continuously, and character-level diffs
    produce a stream of one-word entries. Six entries is six trips to the timeline
    for one thing to listen to."""
    a = [seg(30.0, 36.0, "到夾有頭夾到夾去這個他夾到夾")]
    b = [seg(30.0, 36.0, "的話可以可以得它插不進")]

    out = tc.compare(a, b)

    assert len(out["review"]) <= 2, out["review"]
    assert out["review"][0]["end"] - out["review"][0]["start"] > 1.0


def test_merging_keeps_both_sides_text():
    merged = tc._merge_nearby([
        {"start": 1.0, "end": 1.4, "kind": tc.OTHER, "a": "甲", "b": "乙"},
        {"start": 1.6, "end": 2.0, "kind": tc.OTHER, "a": "丙", "b": "丁"},
    ])
    assert len(merged) == 1
    assert merged[0]["a"] == "甲 丙" and merged[0]["b"] == "乙 丁"


def test_a_taigi_finding_is_not_buried_by_what_it_sits_next_to():
    """"the Taiwanese was flattened here" is the finding worth surfacing; merging it
    into a neighbour labelled `other` would hide the only category that names a
    known, fixable failure."""
    merged = tc._merge_nearby([
        {"start": 1.0, "end": 1.4, "kind": tc.OTHER, "a": "x", "b": "y"},
        {"start": 1.5, "end": 2.0, "kind": tc.TAIGI, "a": "我們", "b": "阮"},
    ])
    assert len(merged) == 1 and merged[0]["kind"] == tc.TAIGI


def test_distant_disagreements_stay_separate():
    merged = tc._merge_nearby([
        {"start": 1.0, "end": 1.4, "kind": tc.OTHER, "a": "甲", "b": "乙"},
        {"start": 40.0, "end": 41.0, "kind": tc.OTHER, "a": "丙", "b": "丁"},
    ])
    assert len(merged) == 2


# ── the marker set, after measuring it ───────────────────────────────────────

def test_only_unambiguous_characters_are_markers():
    """Measured across 541 real transcripts (22,799 chars): 19 of the original 24
    markers never appeared, and the 5 that did are ordinary Mandarin. The category
    was labelling noise.

    Anything that also occurs in written Mandarin must stay out — a marker that
    fires on Mandarin does not make the category noisy, it makes it wrong."""
    for ch in "怎焦物啥按爸母孫熱歹勢多謝鬧囝兜箍伊講較欲":
        assert ch not in tc.TAIGI_MARKERS, "{0} occurs in ordinary Mandarin".format(ch)


def test_the_markers_that_remain_are_the_ones_that_only_exist_in_taiwanese():
    for ch in "毋袂佇阮恁遮遐蹛媠囡":
        assert ch in tc.TAIGI_MARKERS


def test_ordinary_mandarin_never_looks_like_taiwanese():
    """The regression the measurement exposed: two plain Mandarin readings that
    happen to differ must not come back labelled `taigi`."""
    out = tc.compare([seg(0, 3, "他說這個怎麼按下去物件就不見了")],
                     [seg(0, 3, "他說這個怎麼按下去東西就不見了")])
    assert tc.TAIGI not in out["by_kind"], out


def test_real_written_taiwanese_still_registers():
    """Narrowing must not silence the category entirely — when an engine does write
    Taiwanese, this is what has to fire."""
    out = tc.compare([seg(0, 3, "我們不會在這裡")], [seg(0, 3, "阮袂佇遮")])
    assert tc.TAIGI in out["by_kind"], out
