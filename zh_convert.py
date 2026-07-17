"""Simplified→Traditional (Taiwan) conversion for zh transcripts (Phase 9.8b).

Whisper large-v3 emits Simplified Chinese for zh; arkiv's TA is Taiwan. Applied at
TRANSCRIBE time so the STORED transcript / segments are Traditional — search recall,
UI, and every export get Traditional for free. It has to be write-path, not a
display filter: the semantic + lexical search INDEX is built from the stored text,
so only converting on display would leave recall broken (a 記憶體 query missing a
內存-indexed clip).

Conversion is s2twp (Taiwan idioms: 内存→記憶體 / 软件→軟體 / 视频→影片) across the
transcript, segment, AND word text. It is timing-safe on every field because
start/end/score are COPIED verbatim — a phrase idiom that changes a token's length
can never shift its timestamps (timings are explicit boundaries, not char-derived).
(t2twp does not exist in OpenCC and Taiwan idioms only exist on the Simplified→ path,
which is why the idiom pass runs here at store time, not as an export-time layer over
already-converted text.) `to_traditional` (s2t, neutral Traditional, length-preserving)
stays available as a utility but is not the default — the TA wants Taiwan idioms.

Degrades to identity if opencc is unavailable — a missing wheel must never break a
transcribe; it just leaves that clip Simplified until re-transcribed. Existing
Simplified transcripts are NOT retroactively converted (write-path only) — batch
backfill of the stored transcript/segments/words columns + re-embed is a documented
follow-up (roadmap Phase 9.8b), not silent.
"""
import functools


@functools.lru_cache(maxsize=4)
def _converter(config):
    try:
        import opencc
        return opencc.OpenCC(config)
    except Exception:  # noqa: BLE001 — missing/broken opencc → identity passthrough
        return None


def _convert(config, text):
    if not text:
        return text
    conv = _converter(config)
    if conv is None:
        return text
    try:
        return conv.convert(text)
    except Exception:  # noqa: BLE001
        return text


def to_taiwan(text: str) -> str:
    """s2twp — Taiwan Traditional + idioms. Phrase-level (may change length)."""
    return _convert("s2twp", text)


def to_traditional(text: str) -> str:
    """s2t — neutral Traditional, length-preserving. Utility (not the default path)."""
    return _convert("s2t", text)


def is_zh(lang) -> bool:
    return (lang or "").lower().startswith("zh")


def convert_result(text, lang, segments, words):
    """Convert one whisper zh result to Taiwan Traditional (s2twp — with idioms) across
    transcript, segment, AND word text. Timing-safe on every field: start/end/score are
    copied verbatim (`{**s}`/`{**w}`), so a phrase idiom that changes a token's length
    can't shift its timestamps. Non-zh → untouched."""
    if not is_zh(lang):
        return text, lang, segments, words
    text = to_taiwan(text)
    segments = [{**s, "text": to_taiwan(s.get("text", ""))} for s in (segments or [])]
    words = [{**w, "word": to_taiwan(w.get("word", ""))} for w in (words or [])]
    return text, lang, segments, words
