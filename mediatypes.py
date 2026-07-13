"""Single source of truth for the media file-extension sets.

Round-5 #57: the video / audio / all-media extension sets were hand-copied in
~7 modules (ingest.py, server.py, watch.py, db.py, query_builder.py, frames.py,
...). db.py's SQL video filter had already DRIFTED — its `ext IN (...)` literal
was missing `.insv` / `.360`, so 360 clips vanished from the non-search video
filter. Every module now imports these constants instead of re-declaring a
literal, and db.py builds its SQL predicate from `VIDEO_EXT` / `AUDIO_EXT`, so
the filter can never drift again.

Kept import-safe under Python 3.8+ (pure stdlib, plain literals, no `X | Y`
type unions, no walrus) because watch.py / db.py run on the NAS.

Every extension is lowercase with a leading dot; callers must lowercase the
suffix before a membership test (`Path(...).suffix.lower()`), exactly as before.
"""
from typing import FrozenSet

# 360 dual-fisheye rigs — Insta360 `.insv` / GoPro Max `.360`. HEVC-in-MOV/MP4
# that ffmpeg / ffprobe probe and extract frames from like any other video.
# Broken out as a named subset because frames.py stitches these to equirectangular
# BEFORE frame extraction (Phase 8.3b); the video pipeline otherwise treats them
# as ordinary video (verified 2026-06-12: dual 2880x2880 HEVC fisheye + AAC).
VIDEO_360_EXT = frozenset({".insv", ".360"})

# Video containers the ingest pipeline probes / thumbnails / extracts frames from.
# `.mkv` / `.avi` / `.webm` are ffmpeg-handled containers and count as video (B3).
VIDEO_EXT = frozenset({
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts",
}) | VIDEO_360_EXT

# Audio the pipeline transcribes.
AUDIO_EXT = frozenset({".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"})

# Everything the ingest pipeline accepts (video + audio partition MEDIA_EXT).
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT


def sql_in_literal(exts: FrozenSet[str]) -> str:
    """Return a SQL tuple literal — e.g. ``('.mp4', '.mov', ...)`` — for a fixed
    extension set, for use as ``ext IN <literal>``.

    Only ever called on this module's own constant frozensets (never on user
    input), so literal interpolation carries no injection risk and lets the
    predicate stay parameterless like the hand-written SQL it replaces. Sorted
    for a deterministic string; SQL ``IN`` is order-independent.
    """
    quoted = ", ".join("'{0}'".format(ext) for ext in sorted(exts))
    return "({0})".format(quoted)
