"""Switching the active transcript language must not drop edits made to the
active one.

`media.*` holds the ACTIVE transcript; the `transcripts` table archives each
language. The correction dictionary (corrections.apply) rewrites only media.*.
`activate` used to copy the archived row over media.* without archiving what was
active first — so zh (corrected) → en → zh came back as the raw, uncorrected
whisper text, silently, with ok: True.
"""
from fastapi.testclient import TestClient

import config as config_mod
import corrections
import db as db_mod


def _client():
    import server
    return TestClient(server.app, client=("127.0.0.1", 5555))


_H = {"origin": "http://localhost:8501"}


def _seed(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".arkiv").mkdir(parents=True, exist_ok=True)
    with db_mod.get_conn() as conn:
        mid = conn.execute(
            "INSERT INTO media (path, filename, transcript, lang) VALUES (?,?,?,?)",
            ("media-in/a.mp4", "a.mp4", "富田電源線", "zh"),
        ).lastrowid
    db_mod.upsert_transcript(mid, "zh", "富田電源線", None, None)
    db_mod.upsert_transcript(mid, "en", "Tomita power cable", None, None)
    return mid


def test_switching_away_and_back_keeps_dictionary_corrections(tmp_path, tmp_db, monkeypatch):
    mid = _seed(tmp_path, monkeypatch)
    corrections.apply([{"from": "富田", "to": "古河", "scope": "global", "post": True}])
    assert db_mod.get_record_by_id(mid)["transcript"] == "古河電源線"

    c = _client()
    assert c.post("/api/media/%d/transcript/activate" % mid, json={"lang": "en"}, headers=_H).json()["ok"]
    assert db_mod.get_record_by_id(mid)["transcript"] == "Tomita power cable"
    assert c.post("/api/media/%d/transcript/activate" % mid, json={"lang": "zh"}, headers=_H).json()["ok"]

    assert db_mod.get_record_by_id(mid)["transcript"] == "古河電源線"


def test_activate_still_switches_and_keeps_other_languages(tmp_path, tmp_db, monkeypatch):
    mid = _seed(tmp_path, monkeypatch)
    c = _client()
    r = c.post("/api/media/%d/transcript/activate" % mid, json={"lang": "en"}, headers=_H)
    assert r.status_code == 200 and r.json()["active_lang"] == "en"
    rec = db_mod.get_record_by_id(mid)
    assert (rec["transcript"], rec["lang"]) == ("Tomita power cable", "en")
    assert {t["lang"] for t in db_mod.get_transcripts(mid)} == {"zh", "en"}
    assert db_mod.get_transcript(mid, "zh")["transcript"] == "富田電源線"


def test_activate_unknown_language_is_404_and_changes_nothing(tmp_path, tmp_db, monkeypatch):
    mid = _seed(tmp_path, monkeypatch)
    r = _client().post("/api/media/%d/transcript/activate" % mid, json={"lang": "ja"}, headers=_H)
    assert r.status_code == 404
    assert db_mod.get_record_by_id(mid)["transcript"] == "富田電源線"
