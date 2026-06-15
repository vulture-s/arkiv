"""S1a brick 3 — per-stage WS progress protocol (ingest.py emit side).

ingest.py emits machine-readable JSON events (own line, __ARKIV__ sentinel) when
ARKIV_STAGE_EVENTS=1, so the line-buffered WS reader sees each stage in real time;
direct CLI runs keep the compact inline `>marker` markers untouched.
"""
import json

import ingest


def test_stage_inline_when_flag_off(capsys, monkeypatch):
    monkeypatch.setattr(ingest, "_STAGE_EVENTS", False)
    ingest._stage("probe", "probe")
    out = capsys.readouterr().out
    assert out == " >probe"                 # compact inline marker, no newline
    assert "__ARKIV__" not in out


def test_stage_structured_when_flag_on(capsys, monkeypatch):
    monkeypatch.setattr(ingest, "_STAGE_EVENTS", True)
    ingest._stage("whisper", "transcribe")
    out = capsys.readouterr().out
    assert out.startswith("__ARKIV__ ")
    assert out.endswith("\n")               # own flushed line, not inline
    ev = json.loads(out[len("__ARKIV__ "):])
    assert ev == {"t": "stage", "stage": "transcribe"}   # marker→stage mapping


def test_emit_progress_round_trips(capsys, monkeypatch):
    monkeypatch.setattr(ingest, "_STAGE_EVENTS", True)
    ingest._emit_progress({"t": "file", "index": 3, "total": 7, "file": "A 2.mov", "status": "start"})
    line = capsys.readouterr().out.strip()
    ev = json.loads(line[len("__ARKIV__ "):])      # the WS parses exactly this
    assert ev["t"] == "file" and ev["status"] == "start"
    assert ev["index"] == 3 and ev["total"] == 7
    assert ev["file"] == "A 2.mov"                  # spaces survive (json, not regex)


def test_emit_progress_noop_when_flag_off(capsys, monkeypatch):
    monkeypatch.setattr(ingest, "_STAGE_EVENTS", False)
    ingest._emit_progress({"t": "stage", "stage": "probe"})
    assert capsys.readouterr().out == ""            # silent on direct CLI


def test_phase1_done_event_shape(capsys, monkeypatch):
    # the event the WS turns into ok++ and a {type:file,status:done} broadcast
    monkeypatch.setattr(ingest, "_STAGE_EVENTS", True)
    ingest._emit_progress({"t": "file", "index": 2, "total": 5, "file": "B.mov", "status": "phase1_done"})
    ev = json.loads(capsys.readouterr().out.strip()[len("__ARKIV__ "):])
    assert ev["t"] == "file" and ev["status"] == "phase1_done" and ev["index"] == 2
