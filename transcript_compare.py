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

Alignment is by TIME, so segments without usable timestamps cannot be aligned and
are reported as needing review. Pairing them by position instead would be a guess,
and a guess that comes out as "these agree" is the one failure this module must not
have — it would mark unverified text as shippable.

Pure functions, stdlib only. No I/O, no model, no engine assumptions — it takes
two segment lists in arkiv's usual `{"start", "end", "text"}` shape.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# Characters that are written Taiwanese and effectively do not appear in written
# Mandarin. Deliberately conservative: 伊 / 講 / 較 / 欲 are common in both and are
# NOT here, because a marker that fires on Mandarin makes the whole category
# meaningless. Missing a few Taiwanese lines is recoverable; a category that cries
# wolf gets ignored, and then so does the real one.
TAIGI_MARKERS = "毋袂佇阮恁遮遐蹛媠囡爸母囝孫兜箍焦鬧熱歹勢多謝按怎啥物"

# Below this, a length ratio says nothing: short diff runs are word swaps, not
# missing speech.
_COVERAGE_MIN_CHARS = 8

AGREE = "agree"
COVERAGE = "coverage"   # one side has text the other simply doesn't
TAIGI = "taigi"         # one side kept Taiwanese, the other flattened it
OTHER = "other"         # different wording — needs a human ear


def _overlap(a: Dict, b: Dict) -> float:
    """Seconds the two segments share."""
    lo = max(float(a.get("start") or 0.0), float(b.get("start") or 0.0))
    hi = min(float(a.get("end") or 0.0), float(b.get("end") or 0.0))
    return max(0.0, hi - lo)


def _norm(text: str) -> str:
    """Compare content, not layout: whitespace and the punctuation the two engines
    disagree about anyway are not differences worth a human's time."""
    drop = " \t\n，。、！？；：「」『』（）,.!?;:\"'"
    return "".join(ch for ch in (text or "") if ch not in drop)


def _taigi_count(text: str) -> int:
    return sum(1 for ch in (text or "") if ch in TAIGI_MARKERS)


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
    pairs.sort(key=lambda p: float((p[0] or p[1]).get("start") or 0.0))
    return pairs


def classify(a: Optional[Dict], b: Optional[Dict]) -> str:
    """What KIND of difference this is — or AGREE."""
    ta, tb = _norm((a or {}).get("text", "")), _norm((b or {}).get("text", ""))
    if ta == tb:
        return AGREE
    if not ta or not tb:
        return COVERAGE
    # A large length gap is a coverage hole too: one engine heard a sentence, the
    # other caught two words of it. Only for runs long enough for "a sentence vs a
    # fragment" to mean anything — on a two-character difference the ratio fires on
    # everything (`我們` vs `阮` is 2:1) and swallows the categories that matter.
    longer, shorter = max(len(ta), len(tb)), min(len(ta), len(tb))
    if longer >= _COVERAGE_MIN_CHARS and shorter * 2 <= longer:
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
    return {"agreed_chars": agreed_chars, "total_chars": max(len(a_text), len(b_text)),
            "review": review, "by_kind": by_kind}
