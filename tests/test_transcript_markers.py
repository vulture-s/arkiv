"""Roadmap 8.2i — transcript segments → DaVinci markers (edl-markers / fcpxml).

The scene markers (default) come from analysed frames; `?markers=transcript`
sources them from segments_json instead, honouring the same in_s/out_s trim."""
import json
import re

import pytest

import db
from export_builders import transcript_marker_cues, truncate_marker_name


SEGMENTS = [
    {"start": 1.0, "end": 4.0, "text": "我們從上海一路騎到拉薩。"},
    {"start": 5.0, "end": 5.4, "text": "嗯"},          # short → absorbs the next
    {"start": 5.6, "end": 8.0, "text": "第十七天。"},
    {"start": 12.0, "end": 15.0, "text": "Hello <world> & \"quotes\"\nnext line"},
    {"start": 20.0, "end": 22.0, "text": "   "},          # no text → dropped
]


def _seed(sample_record, path, segments=SEGMENTS, **kw):
    rec = dict(path=path, filename=path.rsplit("/", 1)[-1], duration_s=30.0, fps=25.0,
               segments_json=json.dumps(segments, ensure_ascii=False))
    rec.update(kw)
    db.upsert(sample_record(**rec))
    with db.get_conn() as conn:
        mid = conn.execute("SELECT id FROM media WHERE path=?", (path,)).fetchone()[0]
    db.upsert_frame(mid, 0, 2.0, description="SCENE-FRAME-MARKER")
    return mid


# ── pure builder ────────────────────────────────────────────────────────────

def test_cues_one_per_segment_merge_short_and_drop_empty():
    cues = transcript_marker_cues(SEGMENTS)
    assert [c["start"] for c in cues] == [1.0, 5.0, 12.0]
    assert cues[1]["text"] == "嗯第十七天。" and cues[1]["end"] == 8.0
    assert "\n" not in cues[2]["text"]


def test_short_cue_does_not_absorb_across_a_long_pause():
    cues = transcript_marker_cues([
        {"start": 0.0, "end": 0.5, "text": "ok"},
        {"start": 3.0, "end": 4.0, "text": "later"},
    ])
    assert len(cues) == 2


def test_latin_fragments_join_with_space():
    cues = transcript_marker_cues([
        {"start": 0.0, "end": 0.5, "text": "so"},
        {"start": 0.6, "end": 2.0, "text": "anyway"},
    ])
    assert cues[0]["text"] == "so anyway"


def test_truncate_marker_name():
    assert truncate_marker_name("短") == "短"
    out = truncate_marker_name("字" * 100)
    assert len(out) == 60 and out.endswith("…")


# ── HTTP ────────────────────────────────────────────────────────────────────

def test_fcpxml_transcript_markers(fastapi_client, sample_record):
    mid = _seed(sample_record, "/tmp/tm_fcp.mov")
    body = fastapi_client.get(f"/api/media/{mid}/export/fcpxml", params={"markers": "transcript"}).text
    marks = re.findall(r'<marker start="(\d+)/(\d+)s" duration="(\d+)/(\d+)s" value="([^"]*)" note="([^"]*)"', body)
    assert len(marks) == 3
    assert "SCENE-FRAME-MARKER" not in body
    # 25 fps: 1.0s → frame 25 → 25/25s ; duration 3s → 75/25s
    assert marks[0][:4] == ("25", "25", "75", "25")
    assert marks[0][4] == "我們從上海一路騎到拉薩。"           # UTF-8 kept
    assert "&lt;world&gt; &amp; &quot;quotes&quot;" in marks[2][5]


def test_fcpxml_default_is_still_scene_frames(fastapi_client, sample_record):
    mid = _seed(sample_record, "/tmp/tm_default.mov")
    body = fastapi_client.get(f"/api/media/{mid}/export/fcpxml").text
    assert "SCENE-FRAME-MARKER" in body and "拉薩" not in body


def test_fcpxml_transcript_markers_follow_trim(fastapi_client, sample_record):
    mid = _seed(sample_record, "/tmp/tm_trim.mov")
    body = fastapi_client.get(f"/api/media/{mid}/export/fcpxml",
                              params={"markers": "transcript", "in_s": 5, "out_s": 13}).text
    starts = re.findall(r'<marker start="(\d+)/25s"', body)
    # 5.0→0s ("嗯第十七天。"), 12.0→7s (overlaps the OUT point, clamped)
    assert starts == ["0", str(7 * 25)]
    assert "拉薩" not in body


def test_edl_markers_transcript_loc_lines(fastapi_client, sample_record):
    mid = _seed(sample_record, "/tmp/tm_edl.mov")
    body = fastapi_client.get(f"/api/media/{mid}/export/edl-markers", params={"markers": "transcript"}).text
    locs = [ln for ln in body.splitlines() if ln.startswith("* LOC:")]
    assert len(locs) == 3
    assert locs[0].startswith("* LOC: 01:00:01:00 YELLOW ")
    assert "Hello <world>" in locs[2]
    assert all(ln.isascii() for ln in locs)
    assert "SCENE-FRAME-MARKER" not in body


def test_no_segments_means_no_markers_not_fake_timings(fastapi_client, sample_record):
    mid = _seed(sample_record, "/tmp/tm_noseg.mov", segments=[])
    body = fastapi_client.get(f"/api/media/{mid}/export/fcpxml", params={"markers": "transcript"}).text
    assert "<marker" not in body


@pytest.mark.parametrize("fmt,params", [
    ("fcpxml", {"markers": "bogus"}),
    ("srt", {"markers": "transcript"}),
    ("edl", {"markers": "transcript"}),
])
def test_markers_param_validation(fastapi_client, sample_record, fmt, params):
    mid = _seed(sample_record, "/tmp/tm_bad_%s.mov" % fmt)
    assert fastapi_client.get(f"/api/media/{mid}/export/{fmt}", params=params).status_code == 422


def test_batch_passes_markers_through(fastapi_client, sample_record):
    import io
    import zipfile
    mid = _seed(sample_record, "/tmp/tm_batch.mov")
    r = fastapi_client.post("/api/export/batch", json={"ids": [mid], "fmt": "fcpxml", "markers": "transcript"})
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    content = zf.read(zf.namelist()[0]).decode("utf-8")
    assert "拉薩" in content
