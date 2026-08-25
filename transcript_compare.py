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

    Time, not text — the two engines segment differently (one may emit twice as
    many segments for the same speech), so pairing by index would misalign
    everything after the first disagreement. A segment with no counterpart pairs
    with None, which is what makes a coverage hole visible instead of silently
    shifting every later pair.
    """
    pairs: List[Tuple[Optional[Dict], Optional[Dict]]] = []
    used_b = set()
    for a in a_segments:
        best, best_overlap = None, 0.0
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
    # A large length gap in the same window is a coverage hole too: one engine
    # heard a sentence, the other caught two words of it.
    longer, shorter = max(len(ta), len(tb)), min(len(ta), len(tb))
    if shorter * 2 <= longer:
        return COVERAGE
    if (_taigi_count(ta) > 0) != (_taigi_count(tb) > 0):
        return TAIGI
    return OTHER


def compare(a_segments: List[Dict], b_segments: List[Dict],
            min_overlap_s: float = 0.2) -> Dict:
    """Two transcripts in, one review list out.

    Returns `{"agreed": n, "review": [...], "by_kind": {...}}` where each review
    item carries both readings and the window they cover, so a person can jump
    straight to the audio.
    """
    review = []
    agreed = 0
    for a, b in align(a_segments, b_segments, min_overlap_s):
        kind = classify(a, b)
        if kind == AGREE:
            agreed += 1
            continue
        anchor = a or b
        review.append({
            "start": float(anchor.get("start") or 0.0),
            "end": float(anchor.get("end") or 0.0),
            "kind": kind,
            "a": (a or {}).get("text", ""),
            "b": (b or {}).get("text", ""),
        })
    by_kind: Dict[str, int] = {}
    for item in review:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    return {"agreed": agreed, "review": review, "by_kind": by_kind}
