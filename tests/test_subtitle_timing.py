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


def test_merge_never_creates_a_boundary_the_gap_pass_did_not_see():
    """The merge pass cannot introduce a gap violation, and here is why.

    The worry was reasonable: the three passes run once, in order (extend → gap
    → merge), so anything merging invents afterwards is never gap-checked. If
    merge could fabricate a new adjacency, that adjacency would go out unchecked.

    It cannot. A merge replaces cues `i` and `i+1` with `(out[i].start,
    out[i+1].end)` — it takes the *earlier* cue's start and the *later* cue's
    end. Both of those numbers were already one side of a boundary the gap pass
    examined: `out[i].start` faced `i-1`, and `out[i+1].end` faced `i+2`. So
    merging only ever *deletes* boundaries from the list. It never mints one.

    Asserted rather than argued, because the argument is about the shape of the
    code and the code will be edited again. The sweep runs the pipeline twice —
    once whole, once with pass 3 neutered by making every cue look long enough —
    and requires the boundaries of the merged run to be a subset of the
    unmerged run's. A merge that invented an adjacency would put a pair in the
    first set that is absent from the second.

    A 200k-trial random search over the same space found zero counterexamples
    before this test was written. It did find that the *pipeline* can still emit
    a sub-`min_gap` boundary — but that comes from the gap pass declining to
    shave a cue out of existence, which is `_apply_timing`'s own documented
    exemption and has nothing to do with merging.
    """
    import random

    def boundaries(cues):
        return {(round(cues[i][1], 9), round(cues[i + 1][0], 9))
                for i in range(len(cues) - 1)}

    rng = random.Random(20260917)
    merges_seen = 0
    for _ in range(600):
        segments, t = [], round(rng.uniform(0.0, 40.0), 3)
        for _ in range(rng.randint(2, 7)):
            start = t + rng.choice([0.0, 0.0, 0.0, round(rng.uniform(0.0, 0.3), 3)])
            end = start + round(rng.choice([rng.uniform(0.05, 0.5),
                                            rng.uniform(0.5, 2.0),
                                            rng.uniform(2.0, 9.0)]), 3)
            segments.append(seg(start, end, "字" * rng.randint(1, 16)))
            t = end

        merged = sub.layout_cues(segments)

        real_is_too_short = sub._is_too_short
        sub._is_too_short = lambda *a, **k: False
        try:
            unmerged = sub.layout_cues(segments)
        finally:
            sub._is_too_short = real_is_too_short

        if len(merged) < len(unmerged):
            merges_seen += 1
        invented = boundaries(merged) - boundaries(unmerged)
        assert not invented, (
            "merge invented {0} boundary/boundaries the gap pass never saw: "
            "{1}".format(len(invented), sorted(invented)[:3])
        )

    assert merges_seen, "the sweep must actually produce merges or it proves nothing"


# ── the gap pass must not manufacture a reason to merge ──────────────────────
def test_a_cue_the_gap_pass_shaved_is_not_merged_away():
    """Being shaved below `min_dur` is not the same as having had no room.

    Pass 3 folds a cue forward when it is still under `min_dur`, and its licence
    to show the absorbed text early rests entirely on *that cue had no slack to
    grow into*. A cue that measured 0.84s until pass 2 took `min_gap` off its
    end had slack — pass 2 spent it. Reading the duration alone, pass 3 cannot
    tell the two apart, so it used to treat the second as the first and merge.

    The trade it made was bad in both directions: it gave up an 80ms trim and
    took on up to `min_dur + min_gap` of the next sentence on screen before
    those words are spoken, which is the one thing this module promises never
    to do.

    Measured on a real 43-transcript library (1232 cues): 22 cues were
    compliant until pass 2 shaved them, and 17 were then merged on a shortness
    pass 2 had manufactured. End to end, through `layout_cues`, the exemption
    removes 15 cues carrying early text (95 → 80) and leaves 18 more cues
    sitting at 0.72–0.78s. That is the trade, chosen deliberately.
    """
    P = sub.DEFAULT_TIMING
    # 0.84s long — comfortably over min_dur, and the next cue abuts it, so the
    # gap pass must take min_gap out of its end and land it at 0.76.
    cues = sub.layout_cues([seg(0.0, 0.84, "有啦"), seg(0.84, 3.2, "我下午再過去")])

    assert len(cues) == 2, "the shaved cue was merged away: {0}".format(cues)
    assert cues[0][2] == ["有啦"], "cue 0 absorbed its neighbour's text"
    assert cues[0][1] == pytest.approx(0.84 - P.min_gap), durs(cues)
    assert cues[1][0] == pytest.approx(0.84), "the later cue's start moved"


def test_the_exemption_does_not_save_a_cue_that_really_had_no_room():
    """The narrow half of the same rule, or it would just disable merging.

    A cue that was already under `min_dur` before pass 2 touched it is exactly
    what pass 3 exists for, and it must still merge. Without this the exemption
    could be written as "never merge a shaved cue" and every 0.24s flash would
    go back to being a flash.
    """
    cues = sub.layout_cues([seg(0.0, 0.26, "真的"), seg(0.26, 2.6, "就是這樣")])
    assert len(cues) == 1, "a genuinely short cue stopped merging: {0}".format(cues)


def test_the_exemption_is_keyed_on_the_shave_not_on_the_duration():
    """A compliant cue with room to spare is untouched by either rule.

    Guards against an exemption written as "any cue near min_dur" — the
    distinguishing fact is that pass 2 moved this end, not where the end landed.
    """
    cues = sub.layout_cues([seg(0.0, 2.0, "這句話夠長"), seg(2.5, 4.0, "下一句")])
    assert len(cues) == 2
    assert cues[0][1] == pytest.approx(2.0), "an untouched cue was adjusted"


def test_a_cue_sitting_exactly_on_min_dur_counts_as_compliant():
    """`min_dur` itself is inside the exemption, and real material lands there.

    Of the 22 cues the measurement found, three measured exactly 0.800s before
    pass 2 shaved them — Whisper hands out round boundaries often enough that
    the edge is not hypothetical. An exemption written `> min_dur` would merge
    those three and read as correct in every other respect.
    """
    P = sub.DEFAULT_TIMING
    cues = sub.layout_cues([seg(0.0, P.min_dur, "是喔"), seg(P.min_dur, 3.0, "那就這樣")])
    assert len(cues) == 2, "a cue exactly at min_dur was merged away: {0}".format(cues)
    assert cues[0][1] == pytest.approx(P.min_dur - P.min_gap), durs(cues)
