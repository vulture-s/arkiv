"""C3: /remotion-props honours the IN/OUT window, by the trim export's own rule.

The trim export (/export/{fmt}?in_s=&out_s=) already cut segments to the marked
window and rebased them to 0 = IN; /remotion-props ignored the marks and always
returned the whole clip. These tests pin three things:

1. no window → the payload is exactly what it was (back-compat);
2. a window → words are filtered/clipped/rebased and duration is the window;
3. the boundary rule is the SAME one the SRT trim export applies — checked by
   comparing the two endpoints on identical timings, not by restating the rule.
"""
from __future__ import annotations

import importlib
import json
import re

import pytest

import transcript_time

WORDS = [
    {"word": "前", "start": 0.0, "end": 1.0},      # entirely before IN
    {"word": "貼IN", "start": 1.0, "end": 2.0},    # ends exactly at IN → dropped
    {"word": "跨IN", "start": 1.5, "end": 2.5},    # straddles IN → clipped
    {"word": "中", "start": 4.0, "end": 5.0},      # fully inside
    {"word": "跨OUT", "start": 7.5, "end": 8.5},   # straddles OUT → clipped
    {"word": "貼OUT", "start": 8.0, "end": 9.0},   # starts exactly at OUT → dropped
    {"word": "後", "start": 10.0, "end": 11.0},    # entirely after OUT
]


def _seed(db, **over):
    rec = {
        "path": "/tmp/a.mp4", "filename": "a.mp4", "ext": ".mp4",
        "duration_s": 12.0, "size_mb": 5.0, "width": 1920, "height": 1080,
        "fps": 30.0, "has_audio": 1, "transcript": "x", "lang": "zh",
        "frame_tags": "", "thumbnail_path": "", "processed_at": "2026-05-01T09:00:00",
        "words_json": json.dumps(WORDS, ensure_ascii=False),
        # Same timings as segments, so the SRT trim export can be the oracle.
        "segments_json": json.dumps(
            [{"start": w["start"], "end": w["end"], "text": w["word"]} for w in WORDS],
            ensure_ascii=False),
    }
    rec.update(over)
    db.upsert(rec)
    with db.get_conn() as conn:
        return conn.execute("SELECT id FROM media WHERE path=?", (rec["path"],)).fetchone()[0]


def test_no_window_payload_is_unchanged(fastapi_client, server_module):
    mid = _seed(importlib.import_module("db"))
    r = fastapi_client.get(f"/api/media/{mid}/remotion-props")
    assert r.status_code == 200
    assert r.json() == {
        "captions": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in WORDS],
        "duration": 12.0,
        "filename": "a.mp4",
    }


@pytest.mark.parametrize("q", ["in_s=0", "in_s=0&out_s=12", "in_s=0.01&out_s=11.99",
                               "out_s=99", "in_s=9&out_s=3"])
def test_noop_or_invalid_window_is_the_untrimmed_payload(fastapi_client, server_module, q):
    """Within epsilon of the edges, or inverted → whole clip, byte-identical."""
    mid = _seed(importlib.import_module("db"))
    full = fastapi_client.get(f"/api/media/{mid}/remotion-props").content
    assert fastapi_client.get(f"/api/media/{mid}/remotion-props?" + q).content == full


def test_window_filters_clips_and_rebases(fastapi_client, server_module):
    mid = _seed(importlib.import_module("db"))
    r = fastapi_client.get(f"/api/media/{mid}/remotion-props?in_s=2&out_s=8")
    assert r.status_code == 200
    body = r.json()
    assert body["duration"] == pytest.approx(6.0)
    assert body["trim"] == {"in_s": 2.0, "out_s": 8.0}
    assert body["filename"] == "a.mp4"
    got = [(c["word"], round(c["start"], 6), round(c["end"], 6)) for c in body["captions"]]
    assert got == [("跨IN", 0.0, 0.5), ("中", 2.0, 3.0), ("跨OUT", 5.5, 6.0)]


def test_open_ended_window(fastapi_client, server_module):
    mid = _seed(importlib.import_module("db"))
    body = fastapi_client.get(f"/api/media/{mid}/remotion-props?in_s=9.5").json()
    assert body["duration"] == pytest.approx(2.5)
    assert [(c["word"], c["start"], c["end"]) for c in body["captions"]] == [("後", 0.5, 1.5)]


def _srt_spans(srt):
    def sec(ts):
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    return [(sec(a), sec(b)) for a, b in
            re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", srt)]


def test_boundary_rule_matches_the_srt_trim_export(fastapi_client, server_module, monkeypatch):
    """Same timings through both endpoints → same kept spans. The layout engine is
    bypassed so SRT emits one cue per segment, exposing the raw trim result."""
    import subtitle
    monkeypatch.setattr(subtitle, "layout_cues",
                        lambda segs, *a, **k: [(s["start"], s["end"], [s.get("text", "")]) for s in segs])
    mid = _seed(importlib.import_module("db"))
    q = "in_s=2&out_s=8"
    srt = fastapi_client.get(f"/api/media/{mid}/export/srt?" + q).text
    props = fastapi_client.get(f"/api/media/{mid}/remotion-props?" + q).json()
    assert _srt_spans(srt) == [(pytest.approx(c["start"], abs=1e-3), pytest.approx(c["end"], abs=1e-3))
                               for c in props["captions"]]
    assert len(props["captions"]) == 3


def test_null_word_timestamps_do_not_500(fastapi_client, server_module):
    mid = _seed(importlib.import_module("db"), words_json=json.dumps(
        [{"word": "無", "start": None, "end": None}, {"word": "有", "start": 3.0, "end": 4.0}],
        ensure_ascii=False))
    r = fastapi_client.get(f"/api/media/{mid}/remotion-props?in_s=2&out_s=6")
    assert r.status_code == 200
    assert [c["word"] for c in r.json()["captions"]] == ["有"]


def test_trim_helpers_are_the_export_router_seam():
    """The export router must call the shared helpers, not a private copy."""
    import inspect
    src = inspect.getsource(importlib.import_module("routers.export").export_media)
    assert "transcript_time.trim_window(" in src
    assert "transcript_time.trim_rebase(" in src
    assert transcript_time.trim_window(None, None, 10.0) == (0.0, 10.0, False)
