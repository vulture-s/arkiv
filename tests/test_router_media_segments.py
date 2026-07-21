"""A-seg: GET /api/media/{id}/segments — the lightweight sentence-level cut surface
for downstream edit agents (smart-edit). Returns the projected [{start,end,text}]
array only; never words, never the full record."""
import importlib

from starlette.testclient import TestClient


def test_segments_endpoint_returns_projected_array_without_words(fastapi_client):
    db = importlib.import_module("db")
    with db.get_conn() as c:
        c.execute(
            "INSERT INTO media (path, filename, ext, transcript, segments_json, words_json) "
            "VALUES (?,?,?,?,?,?)",
            ("m.mp4", "m.mp4", ".mp4", "hi there",
             '[{"start":0,"end":1,"text":"hi"},{"start":1,"end":2,"text":"there"}]',
             '[{"word":"hi","start":0,"end":1,"score":0.9}]'),
        )
    r = fastapi_client.get("/api/media/1/segments")
    assert r.status_code == 200, r.text
    # projected to exactly {start,end,text} — no words leaked in
    assert r.json() == [
        {"start": 0, "end": 1, "text": "hi"},
        {"start": 1, "end": 2, "text": "there"},
    ]


def test_segments_endpoint_missing_media_404(fastapi_client):
    r = fastapi_client.get("/api/media/99999/segments")
    assert r.status_code == 404


def test_segments_endpoint_empty_when_no_transcript(fastapi_client):
    db = importlib.import_module("db")
    with db.get_conn() as c:
        c.execute(
            "INSERT INTO media (path, filename, ext) VALUES (?,?,?)",
            ("silent.mp4", "silent.mp4", ".mp4"),
        )
    r = fastapi_client.get("/api/media/1/segments")
    assert r.status_code == 200
    assert r.json() == []


def test_segments_endpoint_tolerates_corrupt_segments_json(fastapi_client):
    """A corrupt column degrades to [] rather than 500ing."""
    db = importlib.import_module("db")
    with db.get_conn() as c:
        c.execute(
            "INSERT INTO media (path, filename, ext, segments_json) VALUES (?,?,?,?)",
            ("bad.mp4", "bad.mp4", ".mp4", "{not valid json"),
        )
    r = fastapi_client.get("/api/media/1/segments")
    assert r.status_code == 200
    assert r.json() == []


def test_segments_endpoint_requires_auth(server_module):
    with TestClient(server_module.app) as c:
        assert c.get("/api/media/1/segments").status_code == 401
