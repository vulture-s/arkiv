"""transcript_compare.py — align two transcripts of the same audio and say where
they disagree.

Running ASR twice and diffing is not about picking a winner. Two engines fail in
different, recognisable ways, so the disagreements are worth more than either
transcript alone:

* Whisper flattens Taiwanese into Mandarin — the sentence reads fluently and the
  Taiwanese is simply gone. It is also the one that honours a term dictionary, so
  proper nouns and model numbers come out right.
* Qwen3-ASR keeps Taiwanese far more faithfully, but tends to write the sound
  rather than the word, so proper nouns drift.

Where they agree, you can ship it. Where they don't, you have a short list that is
worth a human ear — and usually you can see at a glance which failure it is,
because the two look nothing alike.

**What this module deliberately does NOT do**: decide which side is right, and
classify "proper noun written phonetically". The latter needs a pronunciation
table, and arkiv has none — opencc converts script, not sound. Guessing would
manufacture a confident-looking category out of nothing, so a difference that
isn't demonstrably one of the known kinds is returned as `other`, meaning
"a person has to listen".

Alignment is by TEXT: `difflib` over the two normalised character streams. Segment
boundaries are an artefact of the engine, not of the speech, and the two engines cut
differently — pairing segment-to-segment marries the same sentence to the wrong
neighbour, which on a real clip produced 1 agreement and 68 mostly-phantom review
items. Timestamps are used only to point a difference back at the audio, never to
decide what pairs with what.

(`align()` is the earlier time-overlap pairing. `compare()` does not use it, and
nothing else in the repo does either — it survives as a public helper for callers
that genuinely want segment pairs, and its tests pin it.)

Pure functions, stdlib only. No I/O, no model, no engine assumptions — it takes
two segment lists in arkiv's usual `{"start", "end", "text"}` shape.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# What actually distinguishes these two engines is **spoken texture**, not
# orthography — and that took two measurements to establish.
#
# First attempt used written-Taiwanese characters (毋 袂 佇 阮 恁 遮 遐 蹛 媠 囡).
# Across 541 real transcripts (22,799 characters) they fired **zero** times, because
# neither engine writes Taiwanese orthography: Whisper flattens Taiwanese into
# Mandarin by design, and Qwen3-ASR has no Taiwanese in its supported languages at
# all — it transcribes Taiwanese speech AS Chinese. Both write Mandarin characters;
# they differ in WHICH ones.
#
# The 3-way meeting bench (2026-07-16) measured the same thing successfully, and
# looking at its definition explains why. Its set in full is
#   啦 齁 乎 嘛 蛤 欸 咧 吼 唷 呴 · 按呢 這馬 · 袂 毋 敢 攏 矣
# — mostly sentence-final PARTICLES, plus a few Taiwanese words. Those are
# what survives when someone speaks Mandarin with Taiwanese speech habits, and an
# engine either keeps them or smooths them into tidy prose. Re-measured on the same
# 22,799 characters: **134 hits (0.59%) against zero** for the orthographic set.
#
# So the category detects "one engine kept the spoken texture, the other tidied it
# away". It is a PROXY for Taiwanese fidelity — the bench used it as one and said so
# — not a detector of Taiwanese, and the bench's own note applies here too: the
# robust signal is the ordering between engines, not the absolute count.
#
# **Four of the bench's members are dropped here: 敢 乎 攏 矣.** All four are also
# ordinary Mandarin — 敢 = dare, 乎 = classical particle, 攏 = gather (靠攏/拉攏/
# 合攏), 矣 = classical final particle (足矣). They are harmless in a density metric
# over a whole corpus, where they add the same background to both sides, but this
# module also classifies individual windows — and there an ambiguous member produces
# exactly the false label the first version of this category was producing.
#
# 攏 and 矣 shipped anyway in the first pass of this rule, because they came in with
# the bench's set while only 敢 and 乎 were tested against the rule. The cost was
# measured afterwards: `他拉攏了對手也靠攏了盟友最後合攏` — plain Mandarin, no
# Taiwanese in it — scored 18.75%, and `如此足矣` vs `如此足够` came back `taigi`.
# The rule was right; it just was not applied to everything it was written for.
PARTICLE_MARKERS = "啦齁嘛蛤欸咧吼唷呴"
# Written Taiwanese proper. Effectively never fires on these two engines, and that
# is correct — it becomes meaningful the day an engine that writes 台語漢字 is added.
TAIGI_MARKERS = "毋袂佇阮恁遮遐蹛媠囡"
TAIGI_WORDS = ("按呢", "這馬")

# Below this, a length ratio says nothing: short diff runs are word swaps, not
# missing speech.
_COVERAGE_MIN_CHARS = 8

# Below this many speech characters, no percentage is reported at all — only a
# count. One marker moves the reading by `100/L` points, and the gap this metric
# exists to resolve is 0.56 points (0.90 vs 1.46), so below L = 179 a single
# character outweighs the entire signal. And arkiv's transcripts really are that
# short: 541 real ones averaged 42 characters, where one 啦 reads as 2.4%.
# `scripts/measure_particle_density.py` re-measures both numbers against any
# library, so this threshold can be re-derived rather than believed. Writing it
# turned up a counting trap the original one-off measurement may well have fallen
# into: `media.transcript` and the active row of `transcripts` are the same text,
# so a union over both tables counts every active transcript twice. If the 541 was
# arrived at that way it is nearer 270. The MEAN length and the density ratio are
# unaffected by a uniform double-count, and those are what this threshold rests on.
DENSITY_MIN_CHARS = 200

AGREE = "agree"
COVERAGE = "coverage"   # one side has text the other simply doesn't
TAIGI = "taigi"         # one side kept the spoken texture, the other tidied it
OTHER = "other"         # different wording — needs a human ear


def _overlap(a: Dict, b: Dict) -> float:
    """Seconds the two segments share."""
    lo = max(float(a.get("start") or 0.0), float(b.get("start") or 0.0))
    hi = min(float(a.get("end") or 0.0), float(b.get("end") or 0.0))
    return max(0.0, hi - lo)


def _norm(text: str) -> str:
    """Compare content, not layout: whitespace and the punctuation the two engines
    disagree about anyway are not differences worth a human's time.

    `str.isspace` rather than a list of space characters — the ideographic space
    U+3000 is what a CJK engine actually emits, and spelling out `" \t\n"` missed
    it, so `你好　嗎` and `你好嗎` came back as a coverage hole on identical speech.
    """
    drop = "，。、！？；：「」『』（）…,.!?;:\"'"
    return "".join(ch for ch in (text or "") if not ch.isspace() and ch not in drop)


def particle_count(text: str) -> int:
    """How many spoken-texture markers a transcript carries."""
    return _taigi_count(text)


def _speech_chars(text: str) -> int:
    """The denominator: characters that carry speech.

    An allowlist (`str.isalnum`), not the drop-list `_norm` uses for comparison.
    Punctuation only ever enters the denominator, never the numerator, so any
    punctuation the drop-list happened to miss deflates the reading — and the two
    engines do not punctuate alike, because the Whisper path runs through LLM polish
    and the Qwen path does not.

    Measured, same sentence, same two markers, with and without polish punctuation:
    **9.09% vs 11.76% — a 29% difference from punctuation alone**, against a
    `_kept_more` threshold of 20%. Punctuation could flip the winner by itself.
    """
    return sum(1 for ch in (text or "") if ch.isalnum())


def particle_density(text: str) -> float:
    """Markers per 100 characters of speech.

    **This is the cheap half of the whole exercise, and on Taiwanese-heavy material
    it is the useful half.** Measured on a 10-minute Taiwanese talk-show slice:
    Whisper 0.90%, Qwen3-ASR 1.46% — the same ordering the 3-way bench found, and
    it needs no alignment, no second opinion, and no human ear. One number per
    transcript answers "which engine kept more of how these people actually spoke".

    Why it matters that this is separate from `compare()`: on that same material the
    two transcripts agreed on only 49% of characters and the review list covered 94%
    of the timeline — because the engines disagree SYSTEMATICALLY on Taiwanese
    (one flattens it to Mandarin, the other writes it phonetically), not
    occasionally. A diff is the wrong instrument for a systematic difference. This
    number is the right one.

    It is a PROXY and a relative one — the absolute value depends on the marker set,
    and the robust signal is the ordering between two transcripts of the SAME audio.
    Comparing densities across different clips says more about the speakers than
    about the engine.

    The 0.90/1.46 pair above was measured with raw string length as the denominator,
    before `_speech_chars` existed. That confound deflates whichever side carries
    more punctuation — the Whisper side, which is the lower of the two — so the real
    gap is if anything wider than 0.56 points, not narrower. The ordering stands;
    the digits are stale. `scripts/measure_particle_density.py` replaces them with
    clean ones the day those libraries are mounted again.
    """
    n = _speech_chars(text)
    return 100.0 * particle_count(text) / n if n else 0.0


def particle_reading(text: str) -> Optional[Dict]:
    """One transcript's texture reading — `{"count", "density"}` — or None.

    **None entirely when the text carries no CJK.** Every marker is a CJK character,
    so an English transcript scores a structural zero. Rendering "0.0%" there looks
    like a measurement and is a category error: the honest answer is that this
    instrument does not apply, and None is how you say that.

    **`density` is None below `DENSITY_MIN_CHARS`**, and the count is returned
    alone. A count is honest at any length; a percentage needs a denominator. This
    is the shape the UI renders, so the decision not to answer lives here, next to
    the reasons for it, rather than in a component that would have to re-derive it.
    """
    text = text or ""
    if not any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return None
    count = particle_count(text)
    n = _speech_chars(text)
    return {"count": count,
            "density": round(100.0 * count / n, 2) if n >= DENSITY_MIN_CHARS else None}


def _particles_only(text: str) -> bool:
    """Every character present is a spoken-texture marker (and there is at least
    one). An empty side is not "particles only" — that is a hole."""
    stripped = "".join((text or "").split())
    if not stripped:
        return False
    return all(ch in PARTICLE_MARKERS or ch in TAIGI_MARKERS for ch in stripped)


def _taigi_count(text: str) -> int:
    """Spoken-texture markers: particles, plus written Taiwanese if it ever shows up."""
    text = text or ""
    n = sum(1 for ch in text if ch in PARTICLE_MARKERS or ch in TAIGI_MARKERS)
    return n + sum(text.count(w) for w in TAIGI_WORDS)


def align(a_segments: List[Dict], b_segments: List[Dict],
          min_overlap_s: float = 0.2) -> List[Tuple[Optional[Dict], Optional[Dict]]]:
    """Pair segments by time overlap, in order.

    Kept for callers that want raw pairs. `compare()` no longer uses it — see
    `cluster()` for why one-to-one pairing is the wrong shape for two engines.
    """
    pairs: List[Tuple[Optional[Dict], Optional[Dict]]] = []
    used_b = set()
    for a in a_segments:
        best, best_overlap, best_i = None, 0.0, -1
        for i, b in enumerate(b_segments):
            if i in used_b:
                continue
            ov = _overlap(a, b)
            if ov > best_overlap:
                best, best_overlap, best_i = b, ov, i
        if best is not None and best_overlap >= min_overlap_s:
            used_b.add(best_i)
            pairs.append((a, best))
        else:
            pairs.append((a, None))
    for i, b in enumerate(b_segments):
        if i not in used_b:
            pairs.append((None, b))
    # `p[0] or p[1]` reads an empty dict as absent and then dereferences None —
    # a segment can legitimately be `{}` (no text, no timing) and must still sort.
    pairs.sort(key=lambda p: float(
        (p[0] if p[0] is not None else p[1]).get("start") or 0.0))
    return pairs


def classify(a: Optional[Dict], b: Optional[Dict]) -> str:
    """What KIND of difference this is — or AGREE."""
    ta, tb = _norm((a or {}).get("text", "")), _norm((b or {}).get("text", ""))
    if ta == tb:
        return AGREE
    # BEFORE the coverage check: a difference that is nothing but particles is the
    # smoothing case, not a hole. `做` vs `做啦` is one engine tidying the speech
    # away — and with the empty-side rule first it came back as "the other engine
    # missed something", which is the opposite of what happened.
    longer, shorter = max(len(ta), len(tb)), min(len(ta), len(tb))
    _lopsided = longer >= _COVERAGE_MIN_CHARS and shorter * 2 <= longer
    if (_particles_only(ta) or _particles_only(tb)) and not _lopsided:
        # ...but only when the particle IS the difference. A lone 齁 against a full
        # sentence is a hole with a particle sitting in it, and calling that
        # "smoothed texture" hides the one thing a person needed to be sent to.
        return TAIGI
    if not ta or not tb:
        return COVERAGE
    # A large length gap is a coverage hole too: one engine heard a sentence, the
    # other caught two words of it. Only for runs long enough for "a sentence vs a
    # fragment" to mean anything — on a two-character difference the ratio fires on
    # everything (`我們` vs `阮` is 2:1) and swallows the categories that matter.
    if _lopsided:
        return COVERAGE
    if (_taigi_count(ta) > 0) != (_taigi_count(tb) > 0):
        return TAIGI
    return OTHER


def _char_timeline(segments: List[Dict]) -> Tuple[str, List[Tuple[float, float]]]:
    """Flatten segments into one normalised character stream, keeping each
    character's (start, end) so a difference can be pointed back at the audio."""
    chars: List[str] = []
    times: List[Tuple[float, float]] = []
    for seg in segments:
        text = _norm(seg.get("text") or "")
        if not text:
            continue
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or 0.0)
        span = max(0.0, end - start)
        for k, ch in enumerate(text):
            # Within a segment the per-character time is interpolated. That is an
            # estimate and only ever used to LOCATE a difference for a human, never
            # written anywhere — the moment it were stored it would be the invented
            # timestamp problem again.
            frac = k / len(text)
            nxt = (k + 1) / len(text)
            chars.append(ch)
            times.append((start + span * frac, start + span * nxt))
    return "".join(chars), times


# Two engines arguing across a messy passage produce a run of one-word
# disagreements. Six entries of a single character each are six trips to the
# timeline for what is one thing to listen to, so runs closer together than this
# are reported as one window.
_MERGE_GAP_S = 1.5


def _kept_more(a_text: str, b_text: str):
    """"a" / "b" / None — which transcript kept more spoken texture.

    None when the difference is under a fifth, because below that the marker set's
    own arbitrariness is doing the talking, not the engines.
    """
    da, db = particle_density(a_text), particle_density(b_text)
    if max(da, db) <= 0:
        return None
    if abs(da - db) / max(da, db) < 0.2:
        return None
    return "a" if da > db else "b"


def _merge_nearby(review: List[Dict], gap_s: float = _MERGE_GAP_S) -> List[Dict]:
    """Collapse review items separated by less than `gap_s` into one window.

    The merged item keeps both sides' text joined in order, so nothing is lost —
    only the number of places a person has to visit changes. A merged run takes the
    kind of its most specific member: `taigi` outranks `other` outranks `coverage`,
    because "the Taiwanese was flattened here" is the finding worth surfacing and it
    would otherwise be buried by whatever it sits next to.
    """
    if not review:
        return []
    priority = {TAIGI: 3, OTHER: 2, COVERAGE: 1}
    ordered = sorted(review, key=lambda r: (r["start"], r["end"]))
    out = [dict(ordered[0])]
    for item in ordered[1:]:
        last = out[-1]
        if item["start"] - last["end"] <= gap_s:
            last["end"] = max(last["end"], item["end"])
            last["a"] = (last["a"] + " " + item["a"]).strip()
            last["b"] = (last["b"] + " " + item["b"]).strip()
            if priority.get(item["kind"], 0) > priority.get(last["kind"], 0):
                last["kind"] = item["kind"]
        else:
            out.append(dict(item))
    return out


def compare(a_segments: List[Dict], b_segments: List[Dict],
            min_overlap_s: float = 0.2) -> Dict:
    """Two transcripts in, one review list out.

    **Aligned on text, not on segment boundaries.** Boundaries are an artefact of
    the engine, not of the speech, and the two engines cut differently — so pairing
    segment-to-segment marries the same sentence to the wrong neighbour. Measured on
    a real 199 s clip (49 vs 64 segments), pairing produced **1 agreement and 68
    review items**, most of them phantom coverage holes for text that both sides
    had. That is the same as telling someone to listen to the whole clip.

    `difflib` over the normalised character stream ignores boundaries entirely.
    Equal runs are agreement; each differing run becomes one review item, located
    back on the timeline by the character it starts and ends at.
    """
    import difflib

    a_text, a_times = _char_timeline(a_segments)
    b_text, b_times = _char_timeline(b_segments)
    if not a_text and not b_text:
        return {"agreed_chars": 0, "total_chars": 0, "review": [], "by_kind": {}}

    review = []
    agreed_chars = 0
    matcher = difflib.SequenceMatcher(None, a_text, b_text, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            agreed_chars += (i2 - i1)
            continue
        a_part, b_part = a_text[i1:i2], b_text[j1:j2]
        kind = classify({"text": a_part} if a_part else None,
                        {"text": b_part} if b_part else None)
        if kind == AGREE:
            agreed_chars += (i2 - i1)
            continue
        spans = a_times[i1:i2] + b_times[j1:j2]
        review.append({
            "start": min(s for s, _e in spans) if spans else 0.0,
            "end": max(e for _s, e in spans) if spans else 0.0,
            "kind": kind,
            "a": a_part,
            "b": b_part,
        })
    review = _merge_nearby(review)
    by_kind: Dict[str, int] = {}
    for item in review:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    # Characters, not runs: "12 equal runs" says nothing a person can act on,
    # "1,180 of 1,240 characters matched" says how much is left to listen to.
    #
    # `texture` is reported even when the review list is useless, and on
    # Taiwanese-heavy audio that is exactly the case: measured 49% agreement and a
    # review list covering 94% of the timeline, while the densities (0.90 vs 1.46)
    # cleanly said which engine kept more. When the two disagree systematically the
    # diff has nothing to offer and this still does.
    return {"agreed_chars": agreed_chars, "total_chars": max(len(a_text), len(b_text)),
            "review": review, "by_kind": by_kind,
            "texture": {"a": round(particle_density(a_text), 3),
                        "b": round(particle_density(b_text), 3),
                        "kept_more": _kept_more(a_text, b_text)}}
