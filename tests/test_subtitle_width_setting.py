"""Caption line width lives in one place now.

14 CJK units (the Netflix zh-Hant spec) was hard-coded in four: `subtitle.wrap`'s
default, the CLI's `--max-cjk`, and each renderer's signature. Four copies of a
number an operator might reasonably want to change — and three chances for the CLI
and the app to disagree about how wide a line is.

Deliberately NOT a query parameter: batch export calls the single-clip builder
internally, so a per-request width would have to be threaded through every caller —
the exact partial wiring this wave exists to undo.
"""
from __future__ import annotations

import importlib
import json

import pytest

import config
import subtitle


def _seed(db, text):
    db.upsert({
        "path": "/tmp/w.mp4", "filename": "w.mp4", "ext": ".mp4",
        "duration_s": 12.0, "size_mb": 5.0, "width": 1920, "height": 1080,
        "fps": 30.0, "has_audio": 1, "transcript": text, "lang": "zh",
        "frame_tags": "", "thumbnail_path": "", "processed_at": "2026-05-01T09:00:00",
        "segments_json": json.dumps([{"start": 0.0, "end": 12.0, "text": text}],
                                    ensure_ascii=False),
    })


LONG = "這是一段很長的旁白，長到一行字幕根本放不下，所以引擎會把它拆成好幾行才對。"


def _body_lines(srt):
    return [ln for ln in srt.split("\n") if ln and "-->" not in ln and not ln.isdigit()]


def test_default_equals_the_engine_default(tmp_db):
    """Adding the setting must change nothing until someone moves it."""
    settings = importlib.reload(importlib.import_module("settings"))
    assert settings.subtitle_max_cjk() == config.SUBTITLE_MAX_CJK == 14


def test_the_setting_changes_what_the_http_export_produces(fastapi_client, server_module):
    _seed(importlib.import_module("db"), LONG)

    wide = _body_lines(fastapi_client.get("/api/media/1/export/srt").text)
    r = fastapi_client.put("/api/settings", json={"scope": "global",
                                                 "values": {"export.subtitle_max_cjk": 8}})
    assert r.status_code == 200, r.text
    narrow = _body_lines(fastapi_client.get("/api/media/1/export/srt").text)

    assert all(subtitle.display_units(ln) <= 8.0 for ln in narrow), narrow
    assert len(narrow) > len(wide), "a narrower line must produce more of them"


def test_the_setting_reaches_the_timeline_export_too(fastapi_client, server_module):
    """One seam, so this comes for free — asserted because 'for free' is how the
    three hand-written emitters justified themselves too."""
    _seed(importlib.import_module("db"), LONG)
    fastapi_client.put("/api/settings", json={"scope": "global",
                                              "values": {"export.subtitle_max_cjk": 8}})

    lines = _body_lines(fastapi_client.get("/api/export/timeline/srt?ids=1").text)

    assert lines and all(subtitle.display_units(ln) <= 8.0 for ln in lines)


def test_the_cli_reads_the_same_setting(fastapi_client, server_module):
    _seed(importlib.import_module("db"), LONG)
    fastapi_client.put("/api/settings", json={"scope": "global",
                                              "values": {"export.subtitle_max_cjk": 8}})
    export = importlib.reload(importlib.import_module("export"))

    assert _body_lines(export.export_srt(1)) == _body_lines(
        fastapi_client.get("/api/media/1/export/srt").text)


def test_an_explicit_width_still_wins(fastapi_client, server_module):
    """`--max-cjk 20` must not be silently overridden by the stored setting."""
    _seed(importlib.import_module("db"), LONG)
    fastapi_client.put("/api/settings", json={"scope": "global",
                                              "values": {"export.subtitle_max_cjk": 8}})
    export = importlib.reload(importlib.import_module("export"))

    lines = _body_lines(export.export_srt(1, max_units=20.0))

    assert any(subtitle.display_units(ln) > 8.0 for ln in lines)


@pytest.mark.parametrize("bad", [4, 100, "wide"])
def test_an_unusable_width_is_rejected_not_stored(fastapi_client, server_module, bad):
    """A 2-unit line would make every cue a column of single characters, and the
    schema is the only thing standing between an operator and that."""
    r = fastapi_client.put("/api/settings", json={"scope": "global",
                                                 "values": {"export.subtitle_max_cjk": bad}})
    assert r.status_code == 422, r.text


def test_the_settings_ui_offers_the_control():
    """A setting the UI never shows is an API-only knob. settings.py's own rule is
    that a control must have a downstream effect; the converse matters as well."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "routes"
           / "SettingsLive.svelte").read_text(encoding="utf-8")
    assert "export.subtitle_max_cjk" in src
