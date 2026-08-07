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
#
# `.mxf` is a *container*, not a codec — the distinction that kept it out until
# now. It used to sit in `ingest._PRO_UNSUPPORTED_EXT` next to `.braw` / `.r3d` /
# `.ari`, but those are proprietary RAW codecs with genuinely no ffmpeg decoder,
# whereas MXF wraps codecs ffmpeg decodes fine — so the grouping was over-broad
# and Sony FX6 / FX9 / Venice cards indexed as zero files. Measured 2026-08-07 on
# two samples, whole chain green on both (probe + thumbnail + frames + the 16k
# mono audio decode whisper feeds on):
#   - samples.ffmpeg.org/MXF/C0023S01.mxf — Sony 2006, MPEG-4 part 2 352x288,
#     start_tc 01:43:48:21 read from format-level tags
#   - XAVC-Intra style: H.264 High 4:2:2 all-I 1920x1080, start_tc 01:00:00:00
# An MXF whose inner codec ffmpeg *can't* decode degrades the same way any
# unreadable file does — `ingest.probe()` returns None and the clip is skipped
# with `[ffprobe failed]` — so admitting the container carries no new risk.
VIDEO_EXT = frozenset({
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".mxf",
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
