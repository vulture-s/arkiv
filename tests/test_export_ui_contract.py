"""Every format the UI offers must be a format the server actually serves.

The two lists drifted for months in the cheap direction — the server had served
`.txt` since Phase 12 and the Inspector never offered a button, so the one export
an editor pastes straight into a script document was unreachable from the app. It
drifts the expensive direction just as easily: a button for a format the server
rejects is a 400 in the user's face.

Nothing links the two, so this test does — by parsing the button lists out of the
Svelte source and calling the real endpoints with them. A text comparison against a
hard-coded set would only restate the drift somewhere else.

Two lists, two endpoints, deliberately different:
  Inspector  → GET /api/media/{id}/export/{fmt}   (one clip)
  MainLive   → GET /api/export/timeline/{fmt}     (several clips on one timeline;
                                                   only sequence formats make sense)
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

_FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"


def _fmt_list(rel_path: str) -> list:
    """Pull `const EXPORT_FMTS = ['a', 'b']` out of a .svelte file."""
    src = (_FRONTEND / rel_path).read_text(encoding="utf-8")
    m = re.search(r"const EXPORT_FMTS = \[([^\]]*)\]", src)
    assert m, "EXPORT_FMTS not found in {0} — did the constant get renamed?".format(rel_path)
    return re.findall(r"'([^']+)'", m.group(1))


def _seed(db):
    segs = json.dumps([{"start": 0.0, "end": 2.0, "text": "第一句"},
                       {"start": 2.0, "end": 4.0, "text": "第二句"}], ensure_ascii=False)
    db.upsert({
        "path": "/tmp/a.mp4", "filename": "a.mp4", "ext": ".mp4",
        "duration_s": 4.0, "size_mb": 5.0, "width": 1920, "height": 1080,
        "fps": 30.0, "has_audio": 1, "transcript": "第一句 第二句", "lang": "zh",
        "frame_tags": "", "thumbnail_path": "", "processed_at": "2026-05-01T09:00:00",
        "segments_json": segs,
    })


def test_the_lists_are_not_empty():
    """Guard the regex itself: a silent [] would make every check below vacuous."""
    assert _fmt_list("lib/Inspector.svelte")
    assert _fmt_list("routes/MainLive.svelte")


@pytest.mark.parametrize("fmt", _fmt_list("lib/Inspector.svelte"))
def test_every_inspector_button_is_served(fastapi_client, server_module, fmt):
    _seed(importlib.import_module("db"))
    r = fastapi_client.get("/api/media/1/export/{0}".format(fmt))
    assert r.status_code == 200, "{0} button → {1}: {2}".format(fmt, r.status_code, r.text[:200])
    assert r.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize("fmt", _fmt_list("routes/MainLive.svelte"))
def test_every_timeline_button_is_served(fastapi_client, server_module, fmt):
    _seed(importlib.import_module("db"))
    r = fastapi_client.get("/api/export/timeline/{0}?ids=1".format(fmt))
    assert r.status_code == 200, "{0} button → {1}: {2}".format(fmt, r.status_code, r.text[:200])


def test_txt_is_offered_for_a_single_clip(fastapi_client, server_module):
    """The button this test was written for. Deliberately spelled out rather than
    left implicit in the parametrised sweep above."""
    assert "txt" in _fmt_list("lib/Inspector.svelte")

    _seed(importlib.import_module("db"))
    r = fastapi_client.get("/api/media/1/export/txt")

    assert r.status_code == 200
    assert r.text == "第一句 第二句"  # the transcript, not the segment join
    assert r.headers["content-type"].startswith("text/plain")


def test_trimmed_txt_is_the_segment_text_not_the_polished_transcript(fastapi_client, server_module):
    """Why the button carries a tooltip. These two exports are different documents:
    untrimmed returns `transcript` (LLM-polished), trimmed can only rebuild the text
    from segments, which hold raw Whisper output. Claiming parity would be a lie the
    user discovers by diffing two downloads."""
    db = importlib.import_module("db")
    segs = json.dumps([{"start": 0.0, "end": 2.0, "text": "第一句"},
                       {"start": 2.0, "end": 4.0, "text": "第二句"}], ensure_ascii=False)
    db.upsert({
        "path": "/tmp/b.mp4", "filename": "b.mp4", "ext": ".mp4",
        "duration_s": 4.0, "size_mb": 5.0, "width": 1920, "height": 1080,
        "fps": 30.0, "has_audio": 1, "transcript": "潤稿後的整篇逐字稿。", "lang": "zh",
        "frame_tags": "", "thumbnail_path": "", "processed_at": "2026-05-01T09:00:00",
        "segments_json": segs,
    })

    whole = fastapi_client.get("/api/media/1/export/txt")
    trimmed = fastapi_client.get("/api/media/1/export/txt?in_s=0&out_s=2")

    assert whole.text == "潤稿後的整篇逐字稿。"
    assert trimmed.text == "第一句"
    assert whole.text != trimmed.text


def test_the_tooltip_says_so():
    """The asymmetry above is invisible in the UI unless the button explains it."""
    src = (_FRONTEND / "lib" / "Inspector.svelte").read_text(encoding="utf-8")
    m = re.search(r"const EXPORT_TITLES = \{(.*?)\n  \}", src, re.S)
    assert m, "EXPORT_TITLES not found"
    assert "txt:" in m.group(1)
    assert "✂" in m.group(1), "the tooltip must mention the trim case, not just say '純文字'"
