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
        n = len(chunks)
        span = max(0.0, end - start)
        for ci, chunk in enumerate(chunks):
            cues.append((start + span * ci / n, start + span * (ci + 1) / n, chunk))
    return cues


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
) -> str:
    """Render Whisper segments to laid-out SRT. Layout lives in `layout_cues`."""
    cues = layout_cues(segments, max_units, max_lines, translate_key)
    return "\n".join(format_cue(i, start, end, _render_lines(lines, restrict_punct))
                     for i, (start, end, lines) in enumerate(cues, 1))


def segments_to_vtt(
    segments: List[Dict],
    max_units: float = 14.0,
    max_lines: int = 2,
    translate_key: Optional[str] = None,
    restrict_punct: bool = True,
) -> str:
    """Same layout, WebVTT syntax: `.` for the millisecond separator, no cue
    numbers (they are optional in WebVTT, and the exports users already have
    don't carry them)."""
    cues = layout_cues(segments, max_units, max_lines, translate_key)
    body = "\n".join("{0} --> {1}\n{2}\n".format(_ts(start, "."), _ts(end, "."),
                                                "\n".join(_render_lines(lines, restrict_punct)))
                     for start, end, lines in cues)
    return "WEBVTT\n\n" + body
