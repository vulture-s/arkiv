"""Lightweight codec detection — no ML deps, importable from server hot path.

Phase 7.7g audit follow-up: server.py /api/stream used to do `import ingest`
inside the request handler, which dragged transcribe + silero_vad + torch +
mlx_whisper + chromadb on first call. Stream endpoints now `import codec` at
module level instead. ingest.py also imports from here to avoid duplicating
PROXY_CODECS and the ffprobe wrapper.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from config import FFPROBE_PATH  # resolved ffprobe (handles headless Windows / WinError 448)

PROXY_CODECS = frozenset({
    "hevc", "hev1",
    "prores", "ap4h", "ap4x", "apch", "apcn", "apcs", "apco",
})

# Containers a browser cannot demux, whatever is inside them. AVCHD camcorder
# footage (.mts/.m2ts) is almost always plain H.264 — so the codec check says
# "playable", we hand over the original bytes labelled video/mp4, and the
# demuxer fails without a word. The user sees a black player and no error.
# The container has to be decided BEFORE the codec, because the codec answer is
# the thing that misleads here.
REMUX_CONTAINERS = frozenset({".mts", ".m2ts", ".m2t", ".ts", ".mxf"})

# Tri-state verdict: distinguishes "ffprobe says H.264" from "ffprobe failed",
# so callers (e.g. stream endpoint) can pick a different fallback for each.
NEEDED = "needed"
NOT_NEEDED = "not_needed"
UNKNOWN = "unknown"

# Process-local cache: (abs_path, mtime_ns, size) → codec_name or None.
# Key includes mtime + size so re-encoded / replaced files re-probe automatically.
_codec_cache: dict = {}


def probe_codec(path: str, timeout: float = 10.0) -> Optional[str]:
    """Return ffprobe-detected video codec_name (lowercase), or None on failure.
    Cached by (path, mtime, size) so repeat calls avoid re-running ffprobe.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key in _codec_cache:
        return _codec_cache[key]
    cmd = [
        # audit M15: -v error (was quiet) so failure reasons reach stderr.
        FFPROBE_PATH, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path),
    ]
    try:
        # encoding pinned to utf-8: Windows cp950 default can choke on ffprobe
        # output bytes (headless ingest crash), so decode explicitly.
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except Exception:
        # audit M15: transient failure (timeout / missing binary) — do NOT
        # negative-cache, so the next call can re-probe and self-heal.
        return None
    # getattr: real CompletedProcess always has returncode; some test fakes don't.
    rc = getattr(r, "returncode", 0)
    if rc != 0:
        # audit M15: probe error — log tail, skip caching so it can self-heal.
        err = (getattr(r, "stderr", "") or "").strip()
        print(f"[WARN] ffprobe rc={rc} for {path}: {err[-200:]}", file=sys.stderr)
        return None
    codec = r.stdout.strip().strip(",").lower() or None
    _codec_cache[key] = codec  # audit M15: only successful probes are cached
    return codec


def container_needs_remux(path: str) -> bool:
    """True for containers no browser can demux, independent of the codec.

    Extension-only on purpose: this must answer before (and without) a probe,
    since the probe is what produces the misleading "h264, looks fine" verdict.
    """
    return os.path.splitext(str(path))[1].lower() in REMUX_CONTAINERS


def needs_proxy(path: str, timeout: float = 10.0) -> str:
    """Tri-state proxy verdict.

    - NEEDED: the browser can't play the original — an unplayable codec
      (HEVC/ProRes) OR an undemuxable container (AVCHD .mts, .ts, .mxf).
    - NOT_NEEDED: codec is browser-friendly (H.264 etc).
    - UNKNOWN: ffprobe failed or file unreachable; caller decides fallback.

    The container is checked first and without probing: an .mts holding H.264
    probes as perfectly playable, which is precisely how it slipped through.
    """
    if container_needs_remux(path):
        return NEEDED
    codec = probe_codec(path, timeout=timeout)
    if codec is None:
        return UNKNOWN
    return NEEDED if codec in PROXY_CODECS else NOT_NEEDED


def clear_cache() -> None:
    """Drop the process-local probe cache (used by tests)."""
    _codec_cache.clear()
