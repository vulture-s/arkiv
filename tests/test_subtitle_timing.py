"""Cue timing: how long a cue holds, and how it meets its neighbour.

The layout engine had only a spatial dimension — 14 units a line, two lines a
cue — and took every timing decision from Whisper unchanged. Measured on the
34 zh clips in the reel-scout library (312 segments → 315 cues):

    cue 短於 0.8s   28 (8.9%)    shortest 0.24s
    gap == 0        256 (91.1%)  every adjacent pair, Whisper abuts them exactly
    split imbalance up to 28x    9.5 cps on one half of a sentence, 0.3 on the other

After: 3 (1.0%) / 0 (0.0%) / equalised. Cues violating any rule went 10.2% → 1.7%.

One invariant outranks the rest and is asserted from several directions below:
**a cue's START never moves.** Text may linger after the words; it must never
appear before them.
"""
import importlib

import pytest

sub = importlib.import_module("subtitle")

OFF = sub.TimingPolicy(enabled=False)


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


def durs(cues):
    return [round(e - s, 4) for s, e, _ in cues]


# ── the invariant that outranks everything ───────────────────────────────────
@pytest.mark.parametrize("segments", [
    [seg(0.0, 0.3, "真的"), seg(0.3, 4.0, "這是一段比較長的句子")],
    [seg(0.0, 2.0, "你好"), seg(2.0, 2.2, "嗯"), seg(2.2, 5.0, "然後呢")],
    [seg(0.0, 0.2, "對"), seg(0.2, 0.4, "嗯"), seg(0.4, 0.6, "好")],
    [seg(1.5, 9.0, "一段很長的旁白" * 6)],
])
def test_no_cue_starts_before_its_words(segments):
    """Every emitted start is a segment start, or lies strictly inside one.

    The first version of this asserted `any(start == a or start > a for a in
    allowed)`, which is true for ANY value past the earliest segment start — 0.15
    and 3.0 both "passed" for allowed={0.0, 0.3}. It was checking almost nothing.
    A split chunk legitimately starts mid-segment, so that is the shape to state:
    inside SOME segment's own span.
    """
    spans = [(s["start"], s["end"]) for s in segments]
    for start, _end, _lines in sub.layout_cues(segments):
        assert any(a - 1e-9 <= start <= b + 1e-9 for a, b in spans), (
            "cue starts at {0}, outside every segment span {1}".format(start, spans)
        )


@pytest.mark.parametrize("segments", [
    [seg(0.0, 0.3, "真的"), seg(0.3, 4.0, "這是一段比較長的句子")],
    [seg(0.0, 2.0, "你好"), seg(2.0, 2.2, "嗯"), seg(2.2, 5.0, "然後呢")],
    [seg(0.0, 0.2, "對"), seg(0.2, 0.4, "嗯"), seg(0.4, 0.6, "好")],
])
def test_cues_never_overlap(segments):
    cues = sub.layout_cues(segments)
    for a, b in zip(cues, cues[1:]):
        assert b[0] >= a[1] - 1e-9, "cue {0} overlaps {1}".format(a, b)


def test_end_never_passes_the_next_start():
    cues = sub.layout_cues([seg(0.0, 0.3, "真的"), seg(3.0, 6.0, "後面這句")])
    assert cues[0][1] <= cues[1][0] + 1e-9


# ── 1. extend a short cue into the slack after it ────────────────────────────
def test_short_cue_grows_into_following_silence():
    """0.24s of "真的" with three seconds of nothing after it should hold."""
    cues = sub.layout_cues([seg(0.0, 0.24, "真的"), seg(4.0, 6.0, "下一句")])
    assert durs(cues)[0] >= sub.DEFAULT_TIMING.min_dur
    assert cues[0][0] == 0.0  # start untouched


def test_extension_takes_only_what_it_needs():
    """A short cue grows to min_dur and stops, even with seconds of silence
    after it. Filling the silence would park text on screen long after the words,
    which is a different (and unasked-for) editorial choice."""
    cues = sub.layout_cues([seg(0.0, 0.2, "對"), seg(1.5, 3.0, "接著說")])
    assert len(cues) == 2, "it had room to grow, so it must not merge"
    assert cues[0][1] == pytest.approx(sub.DEFAULT_TIMING.min_dur)


def test_the_neighbour_caps_the_cps_pull():
    """Where the neighbour limit actually bites.

    It can never bind on min_dur alone: if the cap is below min_dur the cue
    merges instead, and if it is above, the extension stops at min_dur anyway.
    The cap is only visible on the reading-speed pull, which for a full line of
    text wants far more than min_dur — 12 units at 9 cps is 1.33s, and here it
    may only have 1.0.
    """
    text = "十二個中文字的一整行字"          # ~11 units → ~1.2s at 9 cps
    cues = sub.layout_cues([seg(0.0, 0.9, text), seg(1.08, 5.0, "一段夠長的下一句")])
    assert len(cues) == 2, "long enough not to merge"
    assert cues[0][1] == pytest.approx(1.08 - sub.DEFAULT_TIMING.min_gap)


def test_the_cps_pull_extends_a_cramped_cue_when_there_is_room():
    text = "十二個中文字的一整行字"
    cues = sub.layout_cues([seg(0.0, 0.9, text), seg(6.0, 8.0, "下一句")])
    units = sub.display_units(text)
    assert cues[0][1] == pytest.approx(units / sub.DEFAULT_TIMING.target_cps)


def test_a_cue_that_cannot_grow_enough_merges_instead():
    """0.3s of slack takes a 0.2s cue to 0.42s — still a flash. Growing failed,
    so the merge pass has to finish the job."""
    cues = sub.layout_cues([seg(0.0, 0.2, "對"), seg(0.5, 3.0, "接著說")])
    assert len(cues) == 1
    assert cues[0] == (0.0, 3.0, ["對", "接著說"])


def test_extension_respects_max_dur():
    cues = sub.layout_cues([seg(0.0, 0.3, "短")], sub.DEFAULT_TIMING.min_dur)
    assert durs(cues)[0] <= sub.DEFAULT_TIMING.max_dur + 1e-9


def test_a_long_enough_cue_is_left_alone():
    cues = sub.layout_cues([seg(0.0, 3.0, "這句本來就夠久了"), seg(9.0, 11.0, "下一句")])
    assert cues[0] == (0.0, 3.0, ["這句本來就夠久了"])


# ── 2. the gap is carved out of the OUTGOING cue ─────────────────────────────
def test_abutting_cues_are_pulled_apart():
    """Whisper hands us end[0] == start[1] for 91% of pairs."""
    cues = sub.layout_cues([seg(0.0, 2.0, "第一句話說完了"), seg(2.0, 4.0, "第二句接著說")])
    assert cues[1][0] - cues[0][1] == pytest.approx(sub.DEFAULT_TIMING.min_gap)
    assert cues[1][0] == 2.0, "the later cue's start must not move"


def test_the_gap_comes_from_the_earlier_cue_not_the_later_one():
    before = sub.layout_cues([seg(0.0, 2.0, "第一句話說完了"), seg(2.0, 4.0, "第二句接著說")],
                             timing=OFF)
    after = sub.layout_cues([seg(0.0, 2.0, "第一句話說完了"), seg(2.0, 4.0, "第二句接著說")])
    assert after[0][1] < before[0][1]      # earlier cue gave up the time
    assert after[1][0] == before[1][0]     # later cue did not


# ── 3. merge what cannot grow ────────────────────────────────────────────────
def test_a_short_cue_with_no_slack_merges_forward():
    """Nothing to grow into and nothing to shave — the only fix left is to ride
    along with the next cue."""
    cues = sub.layout_cues([seg(0.0, 0.2, "真的"), seg(0.2, 2.5, "然後我們就去了")])
    assert len(cues) == 1
    assert cues[0][0] == 0.0 and cues[0][1] == 2.5
    assert cues[0][2] == ["真的", "然後我們就去了"]


def test_merge_is_refused_when_the_lines_would_not_fit():
    """Two lines is the cap; a merge that would make three must not happen."""
    long_two_liner = "這是一段夠長的句子會被折成兩行給你看看效果如何呢好的"
    cues = sub.layout_cues([seg(0.0, 0.2, "真的"), seg(0.2, 2.0, long_two_liner)])
    assert len(cues) == 2
    assert all(len(lines) <= 2 for _s, _e, lines in cues)


def test_merge_is_refused_when_the_result_would_overstay():
    cues = sub.layout_cues([seg(0.0, 0.2, "真的"), seg(0.2, 20.0, "很長的一段")])
    assert len(cues) == 2


# ── 4. the split follows the text, not the cue count ─────────────────────────
def test_split_equalises_reading_speed_across_the_halves():
    """🔴 The 28x defect. Equal division gave the wordy half 9.5 cps and the
    short tail 0.3 — same sentence, same speaker, wildly different demand."""
    text = "這是一段很長的旁白長到一行字幕根本放不下所以引擎會把它拆開，短尾巴"
    cues = sub.layout_cues([seg(0.0, 12.0, text)])
    assert len(cues) >= 2
    speeds = [sum(sub.display_units(ln) for ln in lines) / (e - s)
              for s, e, lines in cues]
    assert max(speeds) / min(speeds) < 1.6


def test_split_still_ends_where_the_segment_ends():
    cues = sub.layout_cues([seg(4.0, 16.0, "很長的一段話" * 8)])
    assert cues[0][0] == 4.0
    assert cues[-1][1] == pytest.approx(16.0)


def test_degenerate_span_falls_back_to_equal_division():
    """A zero-length segment cannot be divided by weight without dividing by
    zero; it must behave exactly as before rather than raise."""
    cues = sub.layout_cues([seg(5.0, 5.0, "很長的一段話" * 8)])
    assert cues and all(e >= s for s, e, _ in cues)


# ── 5. the off switch really is off ──────────────────────────────────────────
def test_disabled_policy_reproduces_the_old_engine():
    segments = [seg(0.0, 2.0, "第一句"), seg(2.0, 2.2, "嗯"), seg(2.2, 5.0, "第三句")]
    cues = sub.layout_cues(segments, timing=OFF)
    assert len(cues) == 3
    assert cues[0] == (0.0, 2.0, ["第一句"])
    assert cues[1] == (2.0, 2.2, ["嗯"])          # the 0.2s flash is preserved
    assert cues[2][0] == 2.2                       # still abutting


def test_disabled_policy_keeps_equal_division():
    cues = sub.layout_cues([seg(4.0, 16.0, "很長的一段話" * 8)], timing=OFF)
    spans = durs(cues)
    assert max(spans) - min(spans) < 1e-9


# ── 6. the renderers pass the policy through ─────────────────────────────────
@pytest.mark.parametrize("render", ["segments_to_srt", "segments_to_vtt"])
def test_renderers_thread_the_policy(render):
    segments = [seg(0.0, 2.0, "第一句"), seg(2.0, 4.0, "第二句")]
    timed = getattr(sub, render)(segments)
    legacy = getattr(sub, render)(segments, timing=OFF)
    assert timed != legacy, "{0} ignored the timing policy".format(render)


def test_srt_output_has_a_real_gap_at_millisecond_precision():
    """The float ends land on 0.07999999999999996; what matters is what the file
    says after rounding. (A measurement that missed this counted 132 phantom
    violations — the metric was wrong, not the layout.)"""
    srt = sub.segments_to_srt([seg(0.0, 2.0, "第一句"), seg(2.0, 4.0, "第二句")])
    assert "00:00:00,000 --> 00:00:01,920" in srt
    assert "00:00:02,000 --> 00:00:04,000" in srt


# ── the float flip (audit round 2) ───────────────────────────────────────────
def test_a_cue_that_reached_min_dur_is_not_merged_away():
    """🔴 The bug every earlier test missed by starting cues at 0.0.

    `(37.259 + 0.8) - 37.259` is `0.7999999999999972`. The extend pass lifts a
    short cue to exactly `min_dur`, the merge pass measures it back as SHORT, and
    absorbs it — putting the next sentence on screen early.

    The next cue starts at exactly `min_dur + min_gap`, which is the only place
    the two guards separate: any closer and the cue genuinely cannot reach
    `min_dur` (so merging is right), any further and the lead check refuses the
    merge for its own reasons. Here the epsilon is the sole thing deciding, which
    is what makes this a test OF the epsilon rather than of the pair.
    """
    P = sub.DEFAULT_TIMING
    start = 37.259                                  # a start where the flip fires
    nxt = start + P.min_dur + P.min_gap
    assert (start + P.min_dur) - start < P.min_dur, "premise: this start flips"

    cues = sub.layout_cues([seg(start, start + 0.265, "講感開們到節"),
                            seg(nxt, nxt + 2.0, "每")])

    assert len(cues) == 2, "it reached min_dur; only the float said otherwise"
    assert cues[0][1] == pytest.approx(start + P.min_dur)


@pytest.mark.parametrize("start", [0.001, 12.345, 99.999, 601.237, 37.259])
def test_the_boundary_holds_at_many_starts(start):
    """The same construction across starts that flip and starts that do not."""
    P = sub.DEFAULT_TIMING
    nxt = start + P.min_dur + P.min_gap
    cues = sub.layout_cues([seg(start, start + 0.265, "真的"),
                            seg(nxt, nxt + 2.0, "然後我們就去了")])
    assert len(cues) == 2, "start={0} merged a cue that had room".format(start)


def test_the_lead_a_merge_introduces_stays_within_the_bound():
    """A merge shows the absorbed text from the earlier start — before those
    words are spoken. That is tolerable only because a cue reaching the merge
    pass had no room to grow, which bounds the lead at min_dur + min_gap.

    ⚠️ The `lead_ok` guard in the merge cannot fire under any self-consistent
    policy, and this test does not pretend to make it. A cue is still short after
    the extend pass only when the next one starts within min_dur + min_gap, so
    the lead is inside the bound by construction; raising max_dur to let a wider
    merge through also lets the extend reach min_dur, and lowering it makes
    `(ne - s) <= max_dur` refuse the merge first. A mutation removing `lead_ok`
    survives the suite, and that is correct rather than a gap.

    It is kept because it was NOT unreachable while the float flip was live: a
    cue that had reached min_dur measured as short, and the lead was then bounded
    by max_dur alone. The guard is what turns a repeat of that into a refused
    merge rather than 5.7 seconds of early text.

    What this test does is check the bound empirically across the space, which is
    what would catch a future change that makes merging reachable from further
    away.
    """
    P = sub.DEFAULT_TIMING
    bound = P.min_dur + P.min_gap + 1e-9
    starts = [0.0, 0.001, 12.345, 37.259, 99.999, 601.237]
    gaps = [0.0, 0.01, 0.05, 0.2, 0.5, 0.8, 0.87, 0.88, 0.9, 1.604, 5.741]
    merges = 0
    for s0 in starts:
        for gap in gaps:
            cues = sub.layout_cues([seg(s0, s0 + 0.265, "真的"),
                                    seg(s0 + gap, s0 + gap + 2.0, "然後我們就去了")])
            if len(cues) == 1:
                merges += 1
                lead = (s0 + gap) - cues[0][0]
                assert lead <= bound, (
                    "start={0} gap={1}: merged text leads its words by "
                    "{2:.4f}s, bound {3:.4f}".format(s0, gap, lead, bound)
                )
    assert merges, "the sweep must actually produce merges or it proves nothing"
