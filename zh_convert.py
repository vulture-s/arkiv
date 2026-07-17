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


def to_simplified(text: str) -> str:
    """t2s — Traditional→Simplified. Used only to CLASSIFY (detect Traditional-only
    characters), never to store — arkiv's TA is Taiwan, output is always Traditional."""
    return _convert("t2s", text)


def _char_is_simplified(ch) -> bool:
    """A char neutral s2t rewrites — evaluated on a SINGLE char, so there's no phrase
    context to mis-fire (系 alone → 系, never 係)."""
    return to_traditional(ch) != ch


def to_traditional_charwise(text: str) -> str:
    """Per-CHARACTER Simplified→Taiwan-Traditional. Rewrites ONLY genuinely-Simplified
    chars, each in isolation (s2tw for the Taiwan variant: 里→裡), and leaves every
    Traditional-only / shared char byte-identical. No phrase layer → cannot re-segment
    valid Traditional (系統 stays 系統, 音樂類型 stays 類型); length-preserving → word
    timings survive. NO Taiwan phrase idioms (內存 stays 內存, not 記憶體 — idioms need
    the phrase layer, which corrupts Traditional). This is the SAFE converter for MIXED
    zh rows in the 9.8b backfill, where whole-row s2twp would wreck the Traditional
    parts. Degrades to identity without opencc."""
    if not text:
        return text
    return "".join(_convert("s2tw", ch) if _char_is_simplified(ch) else ch for ch in text)


def is_zh(lang) -> bool:
    return (lang or "").lower().startswith("zh")


def classify_zh(text):
    """Classify a zh string for the 9.8b BACKFILL gate (retraditionalize.py).

    Returns one of "simplified" / "traditional" / "mixed" / "empty". The gate only
    lets a "simplified" row through to s2twp, because phrase-level conversion CORRUPTS
    already-Traditional text: opencc's S→T phrase maps assume Simplified input and
    re-segment valid Traditional (系統→係統, 音樂類型→型別, 設備→裝置). Detection is
    per-CHARACTER (single chars carry no phrase context, so 系 alone → 系, never 係):
      - simplified char  = to_traditional(ch) != ch   (s2t changes it)
      - traditional-only = to_simplified(ch) != ch     (t2s changes it)
    A genuine Mainland-Simplified whisper transcript has Simplified chars and NO
    Traditional-only chars → "simplified". Anything already carrying Traditional-only
    chars is "traditional" (none simplified) or "mixed" (both) and is left untouched.
    If opencc is unavailable both probes are identity → everything reads "traditional"
    → the backfill safely converts nothing."""
    if not text or not text.strip():
        return "empty"
    has_simp = any(to_traditional(ch) != ch for ch in text)
    has_trad_only = any(to_simplified(ch) != ch for ch in text)
    if has_simp and not has_trad_only:
        return "simplified"
    if has_simp and has_trad_only:
        return "mixed"
    return "traditional"


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


def convert_result_charwise(text, lang, segments, words):
    """Char-level SAFE variant of convert_result (no phrase idioms) for MIXED zh rows in
    the 9.8b backfill — see to_traditional_charwise. Timing-safe on every field:
    start/end/score are copied verbatim. Non-zh → untouched."""
    if not is_zh(lang):
        return text, lang, segments, words
    text = to_traditional_charwise(text)
    segments = [{**s, "text": to_traditional_charwise(s.get("text", ""))} for s in (segments or [])]
    words = [{**w, "word": to_traditional_charwise(w.get("word", ""))} for w in (words or [])]
    return text, lang, segments, words
