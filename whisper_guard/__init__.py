"""whisper-guard — text-level hallucination filters for Whisper transcripts.

Whisper (and faster-whisper / MLX variants) hallucinate on silence and music:
looping phrases ("字幕由字幕由字幕由…"), repeated n-grams, and degenerate
character cycles. These are pure, dependency-free string checks extracted from
arkiv's transcription pipeline so any project can reuse them.

Public API (stable):
    is_repetitive(text, window=6, threshold=0.35) -> bool
    has_char_loops(text, min_pattern=2, min_repeats=3) -> bool
    remove_char_loops(text) -> str
    HallucinationGuard().clean(text) -> str
    HallucinationGuard().is_hallucination(text) -> bool

Design rule: detectors are conservative — they target the obvious degenerate
patterns Whisper emits on non-speech, not normal repetition in real language.
"""
from __future__ import annotations

import re

__version__ = "0.1.0"
__all__ = [
    "is_repetitive",
    "has_char_loops",
    "remove_char_loops",
    "HallucinationGuard",
]

# A short pattern (2-4 chars) repeated 3+ times back-to-back — the signature of
# a Whisper character-loop hallucination.
_CHAR_LOOP_RE = re.compile(r"(.{2,4})\1{2,}")


def is_repetitive(text: str, window: int = 6, threshold: float = 0.35) -> bool:
    """True when `text` is dominated by repeated fixed-width chunks.

    Slices the text into `window`-sized chunks and measures their uniqueness;
    below `threshold` unique it's almost certainly a looped hallucination rather
    than natural language. Short text (< 3 windows) is never flagged.
    """
    if len(text) < window * 3:
        return False
    chunks = [text[i:i + window] for i in range(0, len(text) - window, window)]
    if not chunks:
        return False
    unique = len(set(chunks))
    return unique / len(chunks) < threshold


def has_char_loops(text: str, min_pattern: int = 2, min_repeats: int = 3) -> bool:
    """True when a 2-4 char pattern repeats 3+ times consecutively."""
    return bool(_CHAR_LOOP_RE.search(text))


def remove_char_loops(text: str) -> str:
    """Collapse each consecutive 2-4 char loop down to a single occurrence."""
    return _CHAR_LOOP_RE.sub(r"\1", text)


class HallucinationGuard:
    """Convenience wrapper bundling the text-level filters.

    >>> g = HallucinationGuard()
    >>> g.clean("字幕由字幕由字幕由")
    '字幕由'
    >>> g.is_hallucination("好好好好好好好好好好")
    True
    """

    def __init__(self, window: int = 6, threshold: float = 0.35):
        self.window = window
        self.threshold = threshold

    def is_hallucination(self, text: str) -> bool:
        """True if the text looks like a looped/degenerate hallucination."""
        return is_repetitive(text, self.window, self.threshold) or has_char_loops(text)

    def clean(self, text: str) -> str:
        """Collapse char-loops. (Repetition is a reject signal, not repairable,
        so callers should drop a segment where is_hallucination() is True.)"""
        return remove_char_loops(text)
