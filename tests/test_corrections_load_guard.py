"""Audit 2026-10-09 K1: an unreadable correction dictionary must not look empty.

GET /api/corrections answered {"rules": []} for a corrupt corrections.json (a
hand edit with one stray comma), the Settings UI rendered that as "no rules",
and the next save PUT the on-screen list over the whole file — no backup. The
dictionary is hours of curated work, so: GET reports `load_error`, and every
save keeps the previous file as corrections.json.bak.
"""
import json

import config
import corrections


def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".arkiv").mkdir(exist_ok=True)
    return corrections.corrections_path()


def test_get_reports_corrupt_dictionary(tmp_path, monkeypatch, fastapi_client):
    path = _env(tmp_path, monkeypatch)
    path.write_text('{"version": 1, "rules": [{"from": "a", "to": "b"},]}', encoding="utf-8")
    body = fastapi_client.get("/api/corrections").json()
    assert body["rules"] == []
    assert body.get("load_error"), body


def test_get_clean_or_missing_has_no_load_error(tmp_path, monkeypatch, fastapi_client):
    path = _env(tmp_path, monkeypatch)
    assert fastapi_client.get("/api/corrections").json().get("load_error") is None
    corrections.save_rules([{"from": "a", "to": "b"}])
    body = fastapi_client.get("/api/corrections").json()
    assert body.get("load_error") is None
    assert len(body["rules"]) == 1
    assert path.exists()


def test_save_keeps_previous_file_as_bak(tmp_path, monkeypatch, fastapi_client):
    path = _env(tmp_path, monkeypatch)
    original = '{"version": 1, "rules": [{"from": "臺", "to": "台"}, {"from": "x", "to": "y"},]}'
    path.write_text(original, encoding="utf-8")
    r = fastapi_client.put("/api/corrections", json={"rules": [{"from": "new", "to": "NEW"}]})
    assert r.status_code == 200
    assert json.loads(path.read_text(encoding="utf-8"))["rules"][0]["from"] == "new"
    bak = path.with_name(path.name + ".bak")
    assert bak.exists(), "previous dictionary must survive a save"
    assert bak.read_text(encoding="utf-8") == original
