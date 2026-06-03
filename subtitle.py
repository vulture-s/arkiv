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

from typing import Dict, List, Optional

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


def segments_to_srt(
    segments: List[Dict],
    max_units: float = 14.0,
    max_lines: int = 2,
    translate_key: Optional[str] = None,
) -> str:
    """Render Whisper segments to laid-out SRT.

    Each segment's text is wrapped to width-safe lines. A monolingual segment
    that needs more than `max_lines` lines is split into multiple cues, its
    time span divided proportionally — so a long segment never produces an
    over-wide line nor a wall-of-text cue. A bilingual segment (translate_key
    present) stays a single cue: original lines on top, translation below.
    """
    out: List[str] = []
    idx = 1
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0) or 0.0)
        end = float(seg.get("end", 0.0) or 0.0)

        if translate_key and (seg.get(translate_key) or "").strip():
            lines = wrap(text, max_units) + wrap((seg.get(translate_key) or "").strip(), max_units)
            out.append(format_cue(idx, start, end, lines))
            idx += 1
            continue

        lines = wrap(text, max_units)
        if not lines:
            continue
        cap = max(1, max_lines)
        chunks = [lines[i:i + cap] for i in range(0, len(lines), cap)]
        n = len(chunks)
        span = max(0.0, end - start)
        for ci, chunk in enumerate(chunks):
            c_start = start + span * ci / n
            c_end = start + span * (ci + 1) / n
            out.append(format_cue(idx, c_start, c_end, chunk))
            idx += 1
    return "\n".join(out)
