"""Map a position in a clip's transcript back to a playback time.

Search hits carry an excerpt of the transcript, but the index has no time
information: chunks are cut by character count, not on Whisper segments. The
timing lives in ``segments_json`` ([{start, end, text}, ...]).

The two texts are NOT guaranteed to match: punctuation restoration, opencc and
re-correction rewrite ``transcript`` without touching the segments (measured
2026-10-14 on a 34-clip zh library: only 19/34 transcripts contain their
segment texts verbatim). So this anchors on short normalized windows instead of
exact substrings, and returns ``None`` rather than guess when no anchor is found —
a wrong timestamp sends the editor to the wrong place, an absent one does not.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional, Sequence, Tuple

WINDOW = 8          # normalized chars per anchor
MAX_SHIFT = 120     # how far past the target position to look for an anchor


def _normalize(text: str) -> Tuple[str, List[int]]:
    """Letters/digits only, lowercased, with each kept char's original index."""
    chars: List[str] = []
    index: List[int] = []
    for i, ch in enumerate(text):
        if ch.isalnum():
            chars.append(ch.lower())
            index.append(i)
    return "".join(chars), index


def parse_segments(segments_json: Any) -> List[dict]:
    if not segments_json:
        return []
    try:
        segs = json.loads(segments_json) if isinstance(segments_json, str) else segments_json
    except (TypeError, ValueError):
        return []
    if not isinstance(segs, list):
        return []
    return [s for s in segs if isinstance(s, dict) and isinstance(s.get("start"), (int, float))]


def _segment_stream(segs: Sequence[dict]) -> Tuple[str, List[int]]:
    """Normalized concatenation of segment texts + which segment each char came from."""
    parts: List[str] = []
    owner: List[int] = []
    for k, s in enumerate(segs):
        norm, _ = _normalize(str(s.get("text") or ""))
        parts.append(norm)
        owner.extend([k] * len(norm))
    return "".join(parts), owner


def locate(transcript: str, segments_json: Any, char_pos: int) -> Optional[float]:
    """Start time (seconds) of the segment that holds ``transcript[char_pos]``,
    or ``None`` when it cannot be placed with confidence."""
    segs = parse_segments(segments_json)
    if not transcript or not segs or char_pos < 0 or char_pos >= len(transcript):
        return None
    if len(segs) == 1:
        return float(segs[0]["start"])   # nothing to disambiguate
    t_norm, t_index = _normalize(transcript)
    s_norm, owner = _segment_stream(segs)
    if len(t_norm) < WINDOW or len(s_norm) < WINDOW:
        return None
    # first normalized char at or after char_pos (else the last one before it)
    p = next((j for j, i in enumerate(t_index) if i >= char_pos), len(t_index) - 1)
    expected = p * len(s_norm) / len(t_norm)   # proportional guess, only to break ties
    # nearest anchors first, on both sides — near the end of a transcript only
    # windows BEFORE the target exist
    shifts = [0] + [d for k in range(2, MAX_SHIFT, 2) for d in (k, -k)]
    for shift in shifts:
        q = p + shift
        if q < 0 or q + WINDOW > len(t_norm):
            continue
        window = t_norm[q:q + WINDOW]
        hits = []
        start = s_norm.find(window)
        while start >= 0 and len(hits) < 50:
            hits.append(start)
            start = s_norm.find(window, start + 1)
        if not hits:
            continue
        best = min(hits, key=lambda h: abs(h - shift - expected))
        # the anchor sits `shift` normalized chars after the target; walk back
        target = min(len(owner) - 1, max(0, best - shift))
        return float(segs[owner[target]]["start"])
    return None


def locate_text(transcript: str, segments_json: Any, needle: str) -> Optional[float]:
    """Time of the first occurrence of ``needle`` in the transcript.

    Compared on normalized text (punctuation, spacing and case ignored), because a
    search excerpt is a re-joined chunk — CJK sentences are concatenated with no
    separator and Latin ones with single spaces — so it is not always a literal
    substring of the stored transcript."""
    if not needle or not transcript:
        return None
    n_norm, _ = _normalize(needle)
    if not n_norm:
        return None
    t_norm, t_index = _normalize(transcript)
    at = t_norm.find(n_norm[:24])
    return locate(transcript, segments_json, t_index[at]) if at >= 0 else None
