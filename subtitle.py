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
    (a "word" — kept whole), OR a whitespace run. Binding rule: a numeric word
    immediately followed by a single CJK char keeps them together so a measure
    word (`14字`, `3個`) never starts a line on its own.
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


def wrap(text: str, max_units: float = 14.0, max_lines: Optional[int] = None) -> List[str]:
    """Wrap `text` into lines each <= max_units, breaking at natural points.

    Prefers to break right after CJK punctuation; falls back to whitespace; and
    if a single atom already exceeds the budget it gets its own line rather than
    being split. max_lines (None = unlimited) caps the number of returned lines —
    extra content is merged into the last line.
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

    lines = [ln for ln in lines if ln]
    if max_lines is not None and len(lines) > max_lines:
        head = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1:])
        lines = head + [tail]
    return lines


def _ts(seconds: float, sep: str = ",") -> str:
    """SRT/VTT timecode HH:MM:SS,mmm."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    if ms == 1000:  # rounding spill
        ms = 0
        s += 1
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

    Each segment becomes one cue; its text is wrapped to <= max_lines lines. If
    translate_key is given and a segment carries that field, the translation is
    wrapped and appended below the original (bilingual: original on top).
    """
    out: List[str] = []
    idx = 1
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines = wrap(text, max_units=max_units, max_lines=max_lines)
        if translate_key:
            tr = (seg.get(translate_key) or "").strip()
            if tr:
                lines = lines + wrap(tr, max_units=max_units, max_lines=max_lines)
        out.append(format_cue(idx, seg.get("start", 0.0) or 0.0, seg.get("end", 0.0) or 0.0, lines))
        idx += 1
    return "\n".join(out)
