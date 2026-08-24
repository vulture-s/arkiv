"""Every subtitle a user downloads now goes through the layout engine.

`subtitle.py` — the Phase 12.5 CJK layout engine: 14-unit lines, break points,
bilingual cues — was reachable from exactly one place: the `export.py` CLI. Every
SRT and VTT served over HTTP came out of a hand-written `f"{i}\\n{ts} --> ..."` loop,
one copy per endpoint, three copies in all. So the roadmap's "12.5 ✅" was true for
a command nobody runs and false for every file a user actually downloaded: those
carried one raw Whisper segment per cue, lines as long as Whisper felt like, and
none of the punctuation policy.

These tests are written so that any path quietly growing its own emitter again goes
red immediately — the sentinel test replaces the engine and demands the sentinel
appear in each output.
"""
from __future__ import annotations

import importlib
import io
import json
import re
import zipfile

import pytest

import subtitle

LONG = "這是一段很長的旁白，長到一行字幕根本放不下，所以引擎會把它拆成好幾行才對。"
PUNCT = "他說：「這很好。」對嗎？我想是的，3.5公斤、12:30、50%"


def _seed(db, **over):
    rec = {
        "path": "/tmp/a.mp4", "filename": "a.mp4", "ext": ".mp4",
        "duration_s": 12.0, "size_mb": 5.0, "width": 1920, "height": 1080,
        "fps": 30.0, "has_audio": 1, "transcript": "第一句 第二句", "lang": "zh",
        "frame_tags": "", "thumbnail_path": "", "processed_at": "2026-05-01T09:00:00",
        "segments_json": json.dumps(
            [{"start": 0.0, "end": 6.0, "text": "第一句"},
             {"start": 6.0, "end": 12.0, "text": "第二句"}], ensure_ascii=False),
    }
    rec.update(over)
    db.upsert(rec)
    return rec


# ── the seam ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt", ["srt", "vtt"])
def test_single_clip_export_goes_through_the_layout_engine(
    fastapi_client, server_module, monkeypatch, fmt
):
    _seed(importlib.import_module("db"))
    monkeypatch.setattr(subtitle, "layout_cues", lambda *a, **k: [(1.0, 2.0, ["哨兵"])])

    r = fastapi_client.get("/api/media/1/export/{0}".format(fmt))

    assert r.status_code == 200
    assert "哨兵" in r.text, "this path built its own cues instead of laying them out"


def test_batch_zip_goes_through_the_layout_engine(
    fastapi_client, server_module, monkeypatch
):
    """The zip reuses the single-clip builder, so it inherits the seam — asserted
    rather than assumed, because 'reuses the builder' is how the copies started."""
    _seed(importlib.import_module("db"))
    monkeypatch.setattr(subtitle, "layout_cues", lambda *a, **k: [(1.0, 2.0, ["哨兵"])])

    r = fastapi_client.post("/api/export/batch", json={"ids": [1], "fmt": "srt"})

    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert "哨兵" in zf.read("a.srt").decode("utf-8")


def test_cli_and_http_agree_byte_for_byte(fastapi_client, server_module):
    """Two code paths, one output. They were free to drift and had."""
    _seed(importlib.import_module("db"), segments_json=json.dumps(
        [{"start": 0.0, "end": 12.0, "text": LONG}], ensure_ascii=False))
    export = importlib.reload(importlib.import_module("export"))

    http = fastapi_client.get("/api/media/1/export/srt").text
    cli = export.export_srt(1)

    assert http == cli


def test_cli_and_http_agree_when_there_are_no_segments(fastapi_client, server_module):
    """The fallback used to be two different guesses: the CLI made one cue for the
    whole clip, HTTP split the transcript's lines evenly."""
    _seed(importlib.import_module("db"), segments_json=None,
          transcript="第一行\n第二行\n第三行")
    export = importlib.reload(importlib.import_module("export"))

    assert fastapi_client.get("/api/media/1/export/srt").text == export.export_srt(1)


# ── what the layout engine actually buys the user ────────────────────────────

def test_a_long_segment_is_wrapped_instead_of_one_endless_line(
    fastapi_client, server_module
):
    _seed(importlib.import_module("db"), segments_json=json.dumps(
        [{"start": 0.0, "end": 12.0, "text": LONG}], ensure_ascii=False))

    srt = fastapi_client.get("/api/media/1/export/srt").text
    body_lines = [ln for ln in srt.split("\n") if ln and "-->" not in ln and not ln.isdigit()]

    assert len(body_lines) > 1
    assert all(subtitle.display_units(ln) <= 14.0 for ln in body_lines), body_lines


def test_the_punctuation_policy_reaches_the_downloaded_file(fastapi_client, server_module):
    """The product decision, pinned at the HTTP boundary: subtitles keep ，！？ and
    .txt keeps everything. One test for both halves, because it IS both halves."""
    _seed(importlib.import_module("db"), transcript=PUNCT, segments_json=json.dumps(
        [{"start": 0.0, "end": 12.0, "text": PUNCT}], ensure_ascii=False))

    srt = fastapi_client.get("/api/media/1/export/srt").text
    vtt = fastapi_client.get("/api/media/1/export/vtt").text
    txt = fastapi_client.get("/api/media/1/export/txt").text

    for cue in (srt, vtt):
        for gone in "。：「」、":
            assert gone not in cue
        assert "，" in cue and "？" in cue and "%" in cue
        assert "3.5" in cue and "12:30" in cue
    for kept in "。：「」、，？":
        assert kept in txt, "the stored transcript keeps its full punctuation"


# ── the things a hand-written emitter got wrong ──────────────────────────────

def test_transcript_text_cannot_forge_a_cue_boundary(fastapi_client, server_module):
    """`-->` and embedded newlines in spoken text must not open a fake cue.

    Three separate things stop this today — `_subtitle_text` neutralises the arrow,
    `wrap()` collapses whitespace, and the punctuation policy strips the dashes —
    so no single mutation makes this test red. It pins the OUTCOME, which is what a
    user's parser cares about; the redundancy is deliberate, since a caller passing
    `restrict_punct=False` would remove one of the three layers."""
    nasty = "他說 --> 然後\n\n99\n00:00:99,000 --> 00:01:00,000\n假字幕"
    _seed(importlib.import_module("db"), segments_json=json.dumps(
        [{"start": 0.0, "end": 6.0, "text": nasty}], ensure_ascii=False))

    srt = fastapi_client.get("/api/media/1/export/srt").text

    timing = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> ")
    assert sum(1 for ln in srt.split("\n") if timing.match(ln)) == 1, srt
    assert "\n99\n" not in srt, "the injected cue number must not start a cue"
    # The text itself survives as text — neutralised, on a body line. That is the
    # correct outcome: sanitising must not delete what someone actually said.
    assert "假字幕" in srt


def test_a_null_timestamp_does_not_500_on_a_trimmed_export(fastapi_client, server_module):
    """Legacy rows carry explicit nulls. `seg.get("start", 0)` returns None for
    those — the default only applies to a MISSING key — and the trim comparison
    then raises TypeError, i.e. a 500 on a real library."""
    _seed(importlib.import_module("db"), segments_json=json.dumps(
        [{"start": None, "end": None, "text": "沒有時間的一段"},
         {"start": 2.0, "end": 4.0, "text": "正常的一段"}], ensure_ascii=False))

    r = fastapi_client.get("/api/media/1/export/srt?in_s=1&out_s=5")

    assert r.status_code == 200, r.text
    assert "正常的一段" in r.text


def test_vtt_still_announces_itself_when_there_is_nothing_to_say(
    fastapi_client, server_module
):
    _seed(importlib.import_module("db"), segments_json=None, transcript="")

    r = fastapi_client.get("/api/media/1/export/vtt")

    assert r.status_code == 200
    assert r.text.startswith("WEBVTT")


def test_the_transcript_fallback_shares_the_clip_between_its_lines():
    """The behaviour the three hand-written copies each re-guessed. Tested directly:
    the CLI/HTTP parity tests above cannot see a change here, because both sides now
    call this same function."""
    export_builders = importlib.import_module("export_builders")

    segs = export_builders.transcript_fallback_segments("第一行\n第二行\n第三行", 9.0)

    assert [s["start"] for s in segs] == [0.0, 3.0, 6.0]
    assert segs[-1]["end"] == 9.0
    assert [s["text"] for s in segs] == ["第一行", "第二行", "第三行"]


def test_the_transcript_fallback_is_empty_for_an_empty_transcript():
    export_builders = importlib.import_module("export_builders")
    assert export_builders.transcript_fallback_segments("  \n\n ", 9.0) == []


# ── the timeline export: the third hand-written emitter ──────────────────────

def _seed_two(db):
    """Two 10-second clips, each with one caption."""
    for i, (name, text) in enumerate([("a.mp4", "甲段台詞"), ("b.mp4", "乙段台詞")], 1):
        db.upsert({
            "path": "/tmp/{0}".format(name), "filename": name, "ext": ".mp4",
            "duration_s": 10.0, "size_mb": 5.0, "width": 1920, "height": 1080,
            "fps": 30.0, "has_audio": 1, "transcript": text, "lang": "zh",
            "frame_tags": "", "thumbnail_path": "",
            "processed_at": "2026-05-0{0}T09:00:00".format(i),
            "segments_json": json.dumps(
                [{"start": 0.0, "end": 4.0, "text": text, "speaker_id": "SPEAKER_0{0}".format(i)}],
                ensure_ascii=False),
        })


def test_timeline_srt_goes_through_the_layout_engine(
    fastapi_client, server_module, monkeypatch
):
    _seed_two(importlib.import_module("db"))
    monkeypatch.setattr(subtitle, "layout_cues", lambda *a, **k: [(1.0, 2.0, ["哨兵"])])

    r = fastapi_client.get("/api/export/timeline/srt?ids=1,2")

    assert r.status_code == 200
    assert "哨兵" in r.text


def test_timeline_srt_keeps_every_other_segment_key(
    fastapi_client, server_module, monkeypatch
):
    """Sequencing rebases start/end. Rebuilding the dict from just those three
    fields would drop `speaker_id` today and a `translation` tomorrow."""
    _seed_two(importlib.import_module("db"))
    seen = {}
    real = subtitle.layout_cues

    def spy(segments, *a, **k):
        seen["segments"] = segments
        return real(segments, *a, **k)

    monkeypatch.setattr(subtitle, "layout_cues", spy)
    fastapi_client.get("/api/export/timeline/srt?ids=1,2")

    assert [s.get("speaker_id") for s in seen["segments"]] == ["SPEAKER_01", "SPEAKER_02"]


def test_timeline_srt_wraps_long_lines_too(fastapi_client, server_module):
    db = importlib.import_module("db")
    db.upsert({
        "path": "/tmp/long.mp4", "filename": "long.mp4", "ext": ".mp4",
        "duration_s": 12.0, "size_mb": 5.0, "width": 1920, "height": 1080,
        "fps": 30.0, "has_audio": 1, "transcript": LONG, "lang": "zh",
        "frame_tags": "", "thumbnail_path": "", "processed_at": "2026-05-01T09:00:00",
        "segments_json": json.dumps([{"start": 0.0, "end": 12.0, "text": LONG}],
                                    ensure_ascii=False),
    })

    srt = fastapi_client.get("/api/export/timeline/srt?ids=1").text
    body_lines = [ln for ln in srt.split("\n") if ln and "-->" not in ln and not ln.isdigit()]

    assert len(body_lines) > 1
    assert all(subtitle.display_units(ln) <= 14.0 for ln in body_lines), body_lines


def test_timeline_srt_applies_the_punctuation_policy(fastapi_client, server_module):
    db = importlib.import_module("db")
    db.upsert({
        "path": "/tmp/p.mp4", "filename": "p.mp4", "ext": ".mp4",
        "duration_s": 12.0, "size_mb": 5.0, "width": 1920, "height": 1080,
        "fps": 30.0, "has_audio": 1, "transcript": PUNCT, "lang": "zh",
        "frame_tags": "", "thumbnail_path": "", "processed_at": "2026-05-01T09:00:00",
        "segments_json": json.dumps([{"start": 0.0, "end": 12.0, "text": PUNCT}],
                                    ensure_ascii=False),
    })

    srt = fastapi_client.get("/api/export/timeline/srt?ids=1").text

    assert "。" not in srt and "「" not in srt
    assert "，" in srt and "？" in srt
