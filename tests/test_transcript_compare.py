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
    a = [seg(0, 2, "第一句一樣"), seg(2, 4, "我們今天"), seg(4, 6, "有講到東西")]
    b = [seg(0, 2, "第一句一樣"), seg(2, 4, "阮今仔日"), seg(4, 6, "")]

    out = tc.compare(a, b)

    assert out["agreed"] == 1
    assert out["by_kind"] == {tc.TAIGI: 1, tc.COVERAGE: 1}
    assert {r["kind"] for r in out["review"]} == {tc.TAIGI, tc.COVERAGE}


def test_review_items_carry_both_readings_and_a_timestamp():
    """The point is to jump to the audio and listen. An item without a window, or
    with only one side, cannot be acted on."""
    out = tc.compare([seg(3, 5, "我們今天")], [seg(3, 5, "阮今仔日")])

    item = out["review"][0]
    assert item["start"] == 3 and item["end"] == 5
    assert item["a"] == "我們今天" and item["b"] == "阮今仔日"


def test_two_identical_transcripts_produce_no_review():
    a = [seg(0, 2, "完全一樣"), seg(2, 4, "第二句")]
    assert tc.compare(a, list(a))["review"] == []


def test_empty_inputs_do_not_raise():
    assert tc.compare([], [])["review"] == []
    assert tc.compare([seg(0, 1, "只有一邊")], [])["by_kind"] == {tc.COVERAGE: 1}


def test_segments_without_timestamps_go_to_review_rather_than_claim_agreement():
    """Legacy rows carry explicit nulls, and alignment is done by time — so with no
    timestamps there is nothing to align on.

    They are reported as needing review, not as agreeing. Matching them up by
    position instead would be a guess, and a guess that says "these agree" is the
    one failure mode this module must not have: it would mark unverified text as
    shippable."""
    out = tc.compare([{"start": None, "end": None, "text": "沒有時間"}],
                     [{"start": None, "end": None, "text": "沒有時間"}])

    assert out["agreed"] == 0
    assert out["review"], "silently dropped instead of flagged"
