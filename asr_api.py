"""asr_api.py — transcribe through an OpenAI-compatible `/audio/transcriptions`
endpoint.

The second engine in a two-pass setup has to come from somewhere, and the useful
observation is that the thing we want to reach locally (QwenASR ships a
`/audio/transcriptions` + `/health` server) speaks the same shape as Groq,
Cloudflare Workers AI, and every whisper.cpp server. So this is not a
"Qwen backend" — it is one adapter that reaches all of them, and which one is in
use is a URL.

That also means the awkward part is real: those services do not agree on the
response body. Three shapes turn up in practice and all three are handled here:

* `verbose_json` — `{"text", "segments":[{"start","end","text"}]}`. The good case.
* plain `{"text": "..."}` — no timings at all.
* SRT — QwenASR's own default. Timings are there, just in a subtitle format.

**A response with no timings does not get invented ones.** It comes back as
`(text, [])` — the words, and an empty segment list saying plainly that nothing
here is placed on a timeline. A fabricated start/end is the class of bug this
project just spent a week removing, and nothing downstream can tell a guessed
timestamp from a measured one.

**A 200 is not a promise that the body is readable.** Every shape here comes from
someone else's server, so each is parsed defensively: a segment that is not an
object, or whose timing is not a number, is left out of the timed list while its
words still reach `text`. The one thing that is never done is returning an
unparsed body as the transcript — a failed SRT parse used to come back with its
own index numbers and `-->` lines as the spoken words, which is worse than an
error because nothing about it looks wrong.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

import requests

# Where the second engine lives. Unset means "no second engine configured", which
# is the normal state — the feature is opt-in per machine, because the model has
# to actually be installed somewhere.
ASR_API_BASE = os.getenv("ARKIV_ASR_API_BASE", "").rstrip("/")
ASR_API_KEY = os.getenv("ARKIV_ASR_API_KEY", "")
ASR_API_MODEL = os.getenv("ARKIV_ASR_API_MODEL", "whisper-1")
ASR_API_TIMEOUT = int(os.getenv("ARKIV_ASR_API_TIMEOUT", "1800"))

# Hours are `\d+`, not `\d{2}`: plenty of emitters write `0:00:01,000`, and the
# consequence of missing one was not "no segments" but the whole raw SRT coming
# back as the transcript text. Minutes and seconds are `\d{1,2}` for the same
# reason. `-->` is the anchor; everything around it is written more loosely than
# the format implies.
_SRT_TIME = re.compile(
    r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")

# What a subtitle body looks like even when it will not parse. Used only to tell
# "this was meant to be SRT and I failed" apart from "this is plain text".
_LOOKS_LIKE_SRT = "-->"


def configured() -> bool:
    return bool(ASR_API_BASE)


def _srt_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(srt_text: str) -> List[Dict]:
    """SRT → arkiv segments. Tolerant of `,` or `.` as the millisecond separator
    (SRT says comma, WebVTT says dot, and servers emit both)."""
    segments: List[Dict] = []
    for block in re.split(r"\n\s*\n", (srt_text or "").strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        timing_at = next((i for i, ln in enumerate(lines) if _SRT_TIME.search(ln)), None)
        if timing_at is None:
            continue
        m = _SRT_TIME.search(lines[timing_at])
        text = " ".join(ln.strip() for ln in lines[timing_at + 1:]).strip()
        if not text:
            continue
        segments.append({
            "start": _srt_seconds(*m.groups()[:4]),
            "end": _srt_seconds(*m.groups()[4:]),
            "text": text,
        })
    return segments


def _seg_text(seg) -> str:
    """A segment's words, or "" if this object has none we can use."""
    if not isinstance(seg, dict):
        return ""
    text = seg.get("text")
    return text.strip() if isinstance(text, str) else ""


def _timing(seg) -> Optional[Tuple[float, float]]:
    """(start, end) if both are numbers, else None — never a substitute value.

    `float(s.get("start") or 0.0)` used to raise on a string and, worse, would
    silently place a segment at 0.0 for a `null`. Both are the invented-timestamp
    failure wearing different clothes: a caller cannot tell either one from a
    measurement.
    """
    start, end = seg.get("start"), seg.get("end")
    if isinstance(start, bool) or isinstance(end, bool):
        return None  # bool is an int; a True start is not a time
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return None
    return float(start), float(end)


def _from_json(data: Dict) -> Tuple[str, List[Dict]]:
    """(text, segments) from a decoded JSON object, tolerating a hostile shape.

    A segment that is not an object, or whose timing is unusable, is dropped from
    the TIMED list — but its words are not lost, because they are still part of
    the transcript: either the server's own top-level `text` (which every
    OpenAI-compatible response carries) or, absent that, the join of every
    segment's words including the dropped ones.
    """
    raw = data.get("segments")
    raw = raw if isinstance(raw, list) else []
    segments = []
    for seg in raw:
        text = _seg_text(seg)
        if not text:
            continue
        span = _timing(seg)
        if span is None:
            continue
        segments.append({"start": span[0], "end": span[1], "text": text})

    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        # No usable top-level transcript: rebuild it from every segment that had
        # words, timed or not, so a bad timing never costs a sentence.
        text = " ".join(t for t in (_seg_text(seg) for seg in raw) if t)
    return text.strip(), segments


def _segments_from_payload(body: str, content_type: str) -> Tuple[str, List[Dict]]:
    """(text, segments) from whichever of the three shapes came back."""
    body = body or ""
    if "json" in (content_type or "").lower() or body.lstrip().startswith("{"):
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return _from_json(data)
    # not JSON → SRT (QwenASR's default) or bare text
    segments = parse_srt(body)
    if segments:
        return " ".join(s["text"] for s in segments), segments
    if _LOOKS_LIKE_SRT in body:
        # It was meant to be a subtitle file and we could not read it. Returning
        # the body would put index numbers and `--> ` lines into the transcript
        # and call them speech — silent, plausible-looking garbage. An error is
        # the smaller harm: the caller can log it, and nobody ships it by mistake.
        raise ValueError(
            "response looks like a subtitle file but no cue parsed "
            "(first 80 chars: {0!r})".format(body.strip()[:80]))
    return body.strip(), []


def transcribe(wav_path: str, language: Optional[str] = None,
               base_url: Optional[str] = None, api_key: Optional[str] = None,
               model: Optional[str] = None, timeout: Optional[int] = None) -> Tuple:
    """arkiv's four-tuple contract: (text, language, segments, words).

    `words` is always empty: none of these endpoints return word timings, and the
    alternative — deriving them by splitting a segment evenly — would be exactly
    the invented-timestamp bug this project just removed.
    """
    base = (base_url if base_url is not None else ASR_API_BASE).rstrip("/")
    if not base:
        raise RuntimeError("no ASR API configured (set ARKIV_ASR_API_BASE)")
    key = api_key if api_key is not None else ASR_API_KEY
    headers = {"Authorization": "Bearer {0}".format(key)} if key else {}
    data = {
        "model": model if model is not None else ASR_API_MODEL,
        # Ask for timings. A server that doesn't know this format ignores it and
        # answers in its own, which the parser above already handles.
        "response_format": "verbose_json",
    }
    if language:
        data["language"] = language
    with open(wav_path, "rb") as fh:
        resp = requests.post(
            "{0}/audio/transcriptions".format(base),
            headers=headers,
            data=data,
            files={"file": (os.path.basename(wav_path), fh, "audio/wav")},
            timeout=timeout if timeout is not None else ASR_API_TIMEOUT,
        )
    resp.raise_for_status()
    text, segments = _segments_from_payload(
        resp.text, resp.headers.get("Content-Type", ""))
    return text, (language or ""), segments, []
