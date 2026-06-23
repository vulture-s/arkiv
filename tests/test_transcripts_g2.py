"""Phase 9.7 G2 — per-language transcript archive.

A clip used to hold one transcript; retranscribing in another language destroyed
the previous one. Now every language is archived in `transcripts`, the active
one mirrors media.*, and you can switch which language is active. Whisper is
stubbed (the archive/activate logic is what's under test, not the model).
"""
import importlib
import json
from pathlib import Path

import pytest


def _seed(transcript="原始中文逐字稿", lang="zh"):
    # retranscribe checks the media file exists → back it with a real temp file.
    p = Path("/tmp/arkiv-g2-clip.mp4")
    p.write_bytes(b"\x00")
    db = importlib.import_module("db")
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO media (path, filename, has_audio, transcript, lang, segments_json) VALUES (?,?,1,?,?,?)",
            (str(p), "c.mp4", transcript, lang,
             json.dumps([{"start": 0, "end": 1, "text": transcript}], ensure_ascii=False)),
        )
        return cur.lastrowid


# ── db layer ─────────────────────────────────────────────────────────────────

def test_archive_keeps_each_language(tmp_db):
    db = importlib.import_module("db")
    mid = _seed()
    db.upsert_transcript(mid, "en", "english text", None, None)
    db.upsert_transcript(mid, "ja", "日本語テキスト", None, None)
    langs = {r["lang"] for r in db.get_transcripts(mid)}
    assert langs == {"en", "ja"}
    # same lang upsert overwrites only that row
    db.upsert_transcript(mid, "en", "english v2", None, None)
    rows = {r["lang"]: r["transcript"] for r in db.get_transcripts(mid)}
    assert rows["en"] == "english v2"
    assert rows["ja"] == "日本語テキスト"


def test_cascade_delete(tmp_db):
    db = importlib.import_module("db")
    mid = _seed()
    db.upsert_transcript(mid, "en", "x", None, None)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM media WHERE id=?", (mid,))
    assert db.get_transcripts(mid) == []


# ── API: retranscribe archives, GET backfills, activate switches ─────────────

@pytest.fixture
def langclient(fastapi_client, server_module, monkeypatch):
    transcribe = importlib.import_module("transcribe")
    # transcribe returns text that encodes the requested language
    monkeypatch.setattr(
        transcribe, "transcribe",
        lambda path, language=None: (f"[{language}] spoken", language or "zh",
                                     [{"start": 0, "end": 1, "text": f"[{language}] spoken"}], []),
    )
    return fastapi_client


def test_retranscribe_archives_each_language(langclient):
    mid = _seed()
    langclient.post(f"/api/media/{mid}/retranscribe", json={"language": "en"})
    langclient.post(f"/api/media/{mid}/retranscribe", json={"language": "ja"})
    data = langclient.get(f"/api/media/{mid}/transcripts").json()
    langs = {t["lang"] for t in data["transcripts"]}
    # zh (seeded, lazily backfilled) + en + ja all preserved
    assert {"zh", "en", "ja"} <= langs
    assert data["active_lang"] == "ja"  # last retranscribe is active
    active = [t for t in data["transcripts"] if t["active"]]
    assert len(active) == 1 and active[0]["lang"] == "ja"


def test_get_lazy_backfills_active(langclient):
    mid = _seed(transcript="只有中文", lang="zh")
    # no transcripts rows yet — GET should backfill the active language
    data = langclient.get(f"/api/media/{mid}/transcripts").json()
    assert data["active_lang"] == "zh"
    zh = [t for t in data["transcripts"] if t["lang"] == "zh"]
    assert len(zh) == 1 and zh[0]["transcript"] == "只有中文" and zh[0]["active"] is True


def test_activate_switches_active_transcript(langclient):
    db = importlib.import_module("db")
    mid = _seed(transcript="中文版", lang="zh")
    langclient.post(f"/api/media/{mid}/retranscribe", json={"language": "en"})  # active→en
    assert db.get_record_by_id(mid)["lang"] == "en"
    # switch back to zh
    r = langclient.post(f"/api/media/{mid}/transcript/activate", json={"lang": "zh"})
    assert r.status_code == 200
    rec = db.get_record_by_id(mid)
    assert rec["lang"] == "zh"
    assert rec["transcript"] == "中文版"
    # en still archived + switchable
    data = langclient.get(f"/api/media/{mid}/transcripts").json()
    assert {"zh", "en"} <= {t["lang"] for t in data["transcripts"]}


def test_activate_unknown_language_404(langclient):
    mid = _seed()
    r = langclient.post(f"/api/media/{mid}/transcript/activate", json={"lang": "fr"})
    assert r.status_code == 404
