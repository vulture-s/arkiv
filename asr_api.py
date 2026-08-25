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

**A response with no timings does not get invented ones.** It comes back as a
single segment spanning the clip, flagged by `segments == []` upstream, because a
fabricated start/end is exactly the class of bug this project just spent a week
removing. Nothing downstream can tell a guessed timestamp from a measured one.
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

_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})")


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


def _segments_from_payload(body: str, content_type: str) -> Tuple[str, List[Dict]]:
    """(text, segments) from whichever of the three shapes came back."""
    if "json" in (content_type or "").lower() or body.lstrip().startswith("{"):
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if isinstance(data, dict):
            segments = [
                {"start": float(s.get("start") or 0.0),
                 "end": float(s.get("end") or 0.0),
                 "text": (s.get("text") or "").strip()}
                for s in (data.get("segments") or [])
                if (s.get("text") or "").strip()
            ]
            return (data.get("text") or "").strip(), segments
    # not JSON → SRT (QwenASR's default) or bare text
    segments = parse_srt(body)
    if segments:
        return " ".join(s["text"] for s in segments), segments
    return (body or "").strip(), []


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
