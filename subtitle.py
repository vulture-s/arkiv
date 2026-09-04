"""subtitle.py — Phase 12.5 subtitle layout engine.

Re-wraps raw Whisper transcript text into broadcast-style caption lines:

- line length capped in CJK "units" (default 14 — Netflix zh-Hant spec; Latin
  chars count as 1/3 of a unit, so a line holds ~14 Chinese or ~42 Latin chars);
- breaks at natural boundaries — CJK punctuation or spaces — and never splits a
  Latin word or separates a number from its measure word (量詞);
- optional bilingual cues (original on top, translation below).

Pure functions, no I/O — `segments_to_srt()` ties it to Whisper segments_json.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

# One laid-out cue: start seconds, end seconds, the lines to show.
Cue = Tuple[float, float, List[str]]


class TimingPolicy(object):
    """How long a cue stays up, and how it relates to its neighbours.

    Until this existed the layout engine had only a SPATIAL dimension — line
    width in units, two lines per cue — and inherited every timing decision
    from Whisper unchanged. Measured on 34 real zh clips (312 segments → 315
    cues) before this was added:

        cue 短於 0.8s   28 (8.9%)   shortest 0.24s — "真的" on screen for a
                                    quarter second, read as a flicker
        gap == 0        256 (91.1%) Whisper segments abut exactly, so one cue
                                    is replaced by the next with no visual break
        split imbalance up to 28x   a segment split across cues divided its span
                                    EQUALLY while the text divided unequally:
                                    9.5 cps on the first cue, 0.3 on the second

    CPS deliberately is not the headline. The same measurement put the median at
    4.7 and only 1.3% above the zh-Hant guideline of 9 — the repo's own todo
    assumed reading speed was the gap, and on real material it is not. The
    target is kept as a soft pull (extend into slack when there is room) rather
    than a hard constraint that would fight the three real defects above.

    Every adjustment moves a cue's END, never its START: text may linger after
    the words, but must never appear before them.
    """

    __slots__ = ("min_dur", "max_dur", "min_gap", "target_cps", "enabled")

    def __init__(self, min_dur=0.8, max_dur=7.0, min_gap=0.08,
                 target_cps=9.0, enabled=True):
        self.min_dur = float(min_dur)
        self.max_dur = float(max_dur)
        # ~2 frames at 24fps: below this the eye reads a change as a flicker
        # rather than as one cue ending and another starting.
        self.min_gap = float(min_gap)
        self.target_cps = float(target_cps)
        self.enabled = bool(enabled)


# Applied unless a caller passes its own. `TimingPolicy(enabled=False)` restores
# the pre-timing behaviour exactly, which is what the regression tests compare
# against.
DEFAULT_TIMING = TimingPolicy()

# Punctuation that a line should prefer to break AFTER (kept on the upper line).
_BREAK_AFTER = "。，、！？；：…—)）」』】》”’"
# Latin width relative to one CJK unit (≈ Netflix 42 Latin ≈ 14 CJK).
_LATIN_UNIT = 1.0 / 3.0


def is_cjk(ch: str) -> bool:
    """True for wide East-Asian glyphs (Han / Kana / fullwidth / CJK punct)."""
    o = ord(ch)
    return (
        0x3000 <= o <= 0x303F      # CJK symbols & punctuation
        or 0x3040 <= o <= 0x30FF   # Hiragana + Katakana
        or 0x3400 <= o <= 0x4DBF   # CJK Ext-A
        or 0x4E00 <= o <= 0x9FFF   # CJK Unified
        or 0xF900 <= o <= 0xFAFF   # CJK compatibility
        or 0xFF00 <= o <= 0xFFEF   # fullwidth forms
    )


def display_units(text: str) -> float:
    """Width of `text` in CJK units (CJK char = 1, other = 1/3)."""
    return sum(1.0 if is_cjk(c) else _LATIN_UNIT for c in text)


def _atoms(text: str) -> List[str]:
    """Split into non-breakable atoms.

    An atom is a single CJK char, OR a maximal run of non-CJK non-space chars
    (a "word" — kept whole), OR a whitespace run. Digit+CJK binding: a numeric
    word immediately followed by a single CJK char keeps them together so a
    measure word (`14字`, `3個`) never starts a line on its own. (This binds the
    digit run to whatever CJK char follows — usually but not strictly a 量詞.)
    """
    atoms: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            atoms.append(text[i:j])
            i = j
        elif is_cjk(ch):
            atoms.append(ch)
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and not is_cjk(text[j]):
                j += 1
            word = text[i:j]
            # bind a trailing CJK measure word to a numeric run
            if j < n and is_cjk(text[j]) and any(c.isdigit() for c in word):
                word += text[j]
                j += 1
            atoms.append(word)
            i = j
    return atoms


def wrap(text: str, max_units: float = 14.0) -> List[str]:
    """Wrap `text` into lines each <= max_units, breaking at natural points.

    Prefers to break right after CJK punctuation; falls back to whitespace; and
    if a single atom already exceeds the budget it gets its own line rather than
    being split. Width is a HARD invariant — every returned line is <= max_units
    (except an unbreakable atom that is itself wider). Line count is unbounded;
    callers that need a per-cue line cap (e.g. segments_to_srt) group/time-split
    rather than merging lines, which would break the width cap.
    """
    text = " ".join(text.split())  # collapse whitespace runs to single spaces
    if not text:
        return []
    atoms = _atoms(text)

    lines: List[str] = []
    cur: List[str] = []
    cur_w = 0.0
    last_break = -1  # index in `cur` just after a preferred break point

    def flush(upto: Optional[int] = None):
        nonlocal cur, cur_w, last_break
        if upto is None:
            piece = cur
            cur = []
        else:
            piece = cur[:upto]
            cur = cur[upto:]
        lines.append("".join(piece).strip())
        cur_w = display_units("".join(cur))
        last_break = -1

    for atom in atoms:
        w = display_units(atom)
        if cur and cur_w + w > max_units:
            # over budget: break at the last preferred point if we have one,
            # otherwise break before this atom.
            if last_break > 0:
                flush(last_break)
                # re-evaluate: maybe the carried-over remainder + atom still fit
            else:
                flush()
        if atom.isspace():
            if not cur:
                continue  # don't start a line with a space
            cur.append(atom)
            cur_w += w
            last_break = len(cur)  # space is a break point
            continue
        cur.append(atom)
        cur_w += w
        if atom and atom[-1] in _BREAK_AFTER:
            last_break = len(cur)
    if cur:
        flush()

    return [ln for ln in lines if ln]


# Subtitles keep only these. A cue's own start/end already expresses the pause
# that 。 、 ； ： … — and quote marks are doing on a page, so on screen they are
# noise competing with a 14-unit budget. `%` is punctuation by Unicode's
# reckoning and a unit by everyone else's.
_KEEP_PUNCT = "，,！!？?%"


def _is_ascii_alnum(ch: str) -> bool:
    return bool(ch) and ch.isascii() and ch.isalnum()


def restrict_punctuation(text: str) -> str:
    """Strip punctuation a subtitle line does not need, keeping `，！？` (and their
    ASCII twins) — the product decision that came out of Penny's transcripts.

    Two exemptions, and the second is the load-bearing one: a punctuation mark with
    an ASCII alphanumeric on BOTH sides is kept. That one rule saves `3.5`, `12:30`,
    `don't` and `state-of-the-art` without an allowlist of special cases, because in
    every one of them the mark is doing structural work inside a token rather than
    ending a clause.

    Call this AFTER `wrap()`, per line. Whisper's `。、；：…` are the most valuable
    break points `wrap()` has (`_BREAK_AFTER`); removing them first would make the
    layout blind. And call it only when rendering — the stored transcript keeps its
    full punctuation, so .txt stays complete, existing libraries benefit with no
    re-transcription, and the decision stays reversible.
    """
    out = []
    for i, ch in enumerate(text):
        if not unicodedata.category(ch).startswith("P"):
            out.append(ch)
            continue
        if ch in _KEEP_PUNCT:
            out.append(ch)
            continue
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        # The mark itself must be ASCII: a full-width 、 between two digits
        # ("12:30、50%") is still a list separator, not part of either number.
        if ch.isascii() and _is_ascii_alnum(prev) and _is_ascii_alnum(nxt):
            out.append(ch)
            continue
        # dropped — a removed mark can leave two spaces behind, collapsed below.
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def _ts(seconds: float, sep: str = ",") -> str:
    """SRT/VTT timecode HH:MM:SS,mmm.

    Rounds to whole milliseconds via a single total-ms conversion so a value
    like 59.9999 carries all the way up (00:01:00,000), never emitting an
    out-of-range 00:00:60,000 (Codex CRITICAL).
    """
    total_ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return "{0:02d}:{1:02d}:{2:02d}{3}{4:03d}".format(h, m, s, sep, ms)


def format_cue(index: int, start: float, end: float, lines: List[str], sep: str = ",") -> str:
    body = "\n".join(lines)
    return "{0}\n{1} --> {2}\n{3}\n".format(index, _ts(start, sep), _ts(end, sep), body)


def layout_cues(
    segments: List[Dict],
    max_units: float = 14.0,
    max_lines: int = 2,
    translate_key: Optional[str] = None,
    timing: Optional[TimingPolicy] = None,
) -> List[Cue]:
    """Lay Whisper segments out as cues: `[(start, end, lines), ...]`.

    This is the layout engine on its own, with no output format attached. Each
    segment's text is wrapped to width-safe lines. A monolingual segment needing
    more than `max_lines` lines becomes several cues, its span divided
    proportionally — so a long segment yields neither an over-wide line nor a
    wall-of-text cue. A bilingual segment (translate_key present) stays one cue:
    original lines on top, translation below.

    Extracted from `segments_to_srt` because SRT was, for a long time, the only
    caller — while the HTTP export path hand-rolled its own cue emitters and never
    reached any of this. A renderer-independent seam is what lets every path
    (SRT, VTT, timeline, batch) share one layout.
    """
    policy = timing if timing is not None else DEFAULT_TIMING
    cues: List[Cue] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0) or 0.0)
        end = float(seg.get("end", 0.0) or 0.0)

        if translate_key and (seg.get(translate_key) or "").strip():
            lines = wrap(text, max_units) + wrap((seg.get(translate_key) or "").strip(), max_units)
            cues.append((start, end, lines))
            continue

        lines = wrap(text, max_units)
        if not lines:
            continue
        cap = max(1, max_lines)
        chunks = [lines[i:i + cap] for i in range(0, len(lines), cap)]
        span = max(0.0, end - start)
        cues.extend(_split_span(start, span, chunks, policy.enabled))
    return _apply_timing(cues, policy)


def _split_span(start: float, span: float, chunks: List[List[str]],
                proportional: bool = True) -> List[Cue]:
    """Divide one segment's span across its chunks IN PROPORTION TO TEXT.

    The old code gave every chunk `span / n` regardless of how much text it
    held. Measured on real material that produced a 28x reading-speed swing
    inside a single sentence — 9.5 cps on a chunk carrying most of the words,
    0.3 cps on the short tail that inherited an equal slice of time.

    Falls back to equal division when the weights are unusable (all-empty text,
    or a zero-length span), which keeps a degenerate segment behaving exactly as
    before rather than dividing by zero.
    """
    n = len(chunks)
    if n == 1:
        return [(start, start + span, chunks[0])]

    weights = [sum(display_units(ln) for ln in ch) for ch in chunks]
    total = sum(weights)
    # `proportional=False` is the pre-timing behaviour, kept whole so that
    # `TimingPolicy(enabled=False)` reproduces the old output byte for byte —
    # which is what the layout-extraction regression guard asserts.
    if not proportional or total <= 0 or span <= 0:
        return [(start + span * i / n, start + span * (i + 1) / n, ch)
                for i, ch in enumerate(chunks)]

    out: List[Cue] = []
    at = start
    for i, ch in enumerate(chunks):
        # Last chunk closes on the segment end exactly, so accumulated float
        # error cannot leave a sliver of time unassigned or overshoot the audio.
        end_i = start + span if i == n - 1 else at + span * weights[i] / total
        out.append((at, end_i, ch))
        at = end_i
    return out


def _apply_timing(cues: List[Cue], policy: TimingPolicy) -> List[Cue]:
    """Adjust cue ENDS so short cues linger and neighbours do not butt together.

    Three passes, in this order because each one's room depends on the previous:

    1. extend — pull each end toward `min_dur`, then toward `target_cps`, but
       never past the next cue's start minus `min_gap`.
    2. gap — where two cues still abut (Whisper hands us `end[i] == start[i+1]`
       for 91% of adjacent pairs), shave the earlier one's end. Shaving the
       outgoing cue is the only safe direction: moving the later cue's start
       would put its text on screen before the words are spoken.
    3. merge — a cue still under `min_dur` had no slack to grow into. Fold it
       into the next one when the combined lines still fit, so "真的" stops
       being a 0.24s flash and rides along with the sentence it belongs to.

    `max_dur` only ever shortens, and only a cue that has room to spare.
    """
    if not policy.enabled or not cues:
        return cues

    out = [(s, e, list(ln)) for s, e, ln in cues]

    # ── 1. extend into available slack ───────────────────────────────────────
    for i, (s, e, lines) in enumerate(out):
        limit = (out[i + 1][0] - policy.min_gap) if i + 1 < len(out) else float("inf")
        want = s + policy.min_dur
        units = sum(display_units(ln) for ln in lines)
        if policy.target_cps > 0:
            want = max(want, s + units / policy.target_cps)
        want = min(want, s + policy.max_dur)
        if want > e:
            out[i] = (s, max(e, min(want, limit)), lines)

    # ── 2. carve the gap out of the earlier cue ──────────────────────────────
    for i in range(len(out) - 1):
        s, e, lines = out[i]
        nxt = out[i + 1][0]
        if e > nxt - policy.min_gap:
            # Never shave a cue out of existence: a tiny cue with no room keeps
            # its original end and is dealt with by the merge pass below.
            shaved = nxt - policy.min_gap
            if shaved > s:
                out[i] = (s, shaved, lines)

    # ── 3. merge what is still too short ─────────────────────────────────────
    merged: List[Cue] = []
    i = 0
    while i < len(out):
        s, e, lines = out[i]
        if (e - s) < policy.min_dur and i + 1 < len(out):
            ns, ne, nlines = out[i + 1]
            if len(lines) + len(nlines) <= 2 and (ne - s) <= policy.max_dur:
                merged.append((s, ne, lines + nlines))
                i += 2
                continue
        merged.append((s, e, lines))
        i += 1
    return merged


def _render_lines(lines: List[str], restrict_punct: bool) -> List[str]:
    """Apply the subtitle punctuation policy to already-laid-out lines.

    A line can empty out entirely (`「。」`), and an empty line inside a cue would
    render as a blank row, so those are dropped. A cue whose every line empties is
    left with one empty string rather than no rows at all — dropping the cue here
    would renumber everything after it, and the cue's timing is still real.
    """
    if not restrict_punct:
        return lines
    kept = [ln for ln in (restrict_punctuation(ln) for ln in lines) if ln]
    return kept or [""]


def segments_to_srt(
    segments: List[Dict],
    max_units: float = 14.0,
    max_lines: int = 2,
    translate_key: Optional[str] = None,
    restrict_punct: bool = True,
    timing: Optional[TimingPolicy] = None,
) -> str:
    """Render Whisper segments to laid-out SRT. Layout lives in `layout_cues`."""
    cues = layout_cues(segments, max_units, max_lines, translate_key, timing)
    return "\n".join(format_cue(i, start, end, _render_lines(lines, restrict_punct))
                     for i, (start, end, lines) in enumerate(cues, 1))


def segments_to_vtt(
    segments: List[Dict],
    max_units: float = 14.0,
    max_lines: int = 2,
    translate_key: Optional[str] = None,
    restrict_punct: bool = True,
    timing: Optional[TimingPolicy] = None,
) -> str:
    """Same layout, WebVTT syntax: `.` for the millisecond separator, no cue
    numbers (they are optional in WebVTT, and the exports users already have
    don't carry them)."""
    cues = layout_cues(segments, max_units, max_lines, translate_key, timing)
    body = "\n".join("{0} --> {1}\n{2}\n".format(_ts(start, "."), _ts(end, "."),
                                                "\n".join(_render_lines(lines, restrict_punct)))
                     for start, end, lines in cues)
    return "WEBVTT\n\n" + body
