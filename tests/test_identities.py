"""A6: manual person annotations (identities) — DB, API, scenes `people`, `who:`.

Human-entered names only; nothing here computes or stores face data. These rows
are the future validation set for any automatic recognition, so the write rules
(normalisation, merge-not-stack) are pinned as carefully as the read shape.
"""
import importlib
import json
import sqlite3
import unicodedata

import pytest
from starlette.testclient import TestClient


def _db():
    return importlib.import_module("db")


def _mid(db, path):
    with db.get_conn() as c:
        return c.execute("SELECT id FROM media WHERE path = ?", (path,)).fetchone()[0]


def _clip(db, sample_record, path="/tmp/ident.mp4", duration_s=30.0, frames=(0.0, 10.0, 20.0)):
    db.upsert(sample_record(path=path, duration_s=duration_s))
    mid = _mid(db, path)
    for i, ts in enumerate(frames):
        db.upsert_frame(media_id=mid, frame_index=i, timestamp_s=ts,
                        description="seg {0}".format(i))
    return mid


def _spans(rows):
    return sorted((r["name"], r["start_s"], r["end_s"]) for r in rows)


# ── DB ───────────────────────────────────────────────────────────────────────

def test_identities_table_cascades_with_media(tmp_db, sample_record):
    db = _db()
    mid = _clip(db, sample_record)
    db.add_identity(mid, "小明")
    conn = sqlite3.connect(str(tmp_db))
    try:
        fks = conn.execute('PRAGMA foreign_key_list("identities")').fetchall()
    finally:
        conn.close()
    assert any(fk[2] == "media" and fk[6].upper() == "CASCADE" for fk in fks)
    db.delete_media(mid)
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM identities").fetchone()[0] == 0


def test_name_is_nfc_and_stripped():
    db = _db()
    decomposed = unicodedata.normalize("NFD", "  José\t Li \n")
    assert db.normalize_identity_name(decomposed) == "José Li"
    assert db.normalize_identity_name(decomposed) == unicodedata.normalize("NFC", "José Li")
    for bad in ("", "   ", "\n\t", "x" * 101):
        with pytest.raises(ValueError):
            db.normalize_identity_name(bad)


def test_overlapping_and_touching_ranges_merge(tmp_db, sample_record):
    db = _db()
    mid = _clip(db, sample_record)
    db.add_identity(mid, "小明", 2.0, 5.0)
    db.add_identity(mid, "小明", 10.0, 12.0)
    assert _spans(db.get_identities(mid)) == [("小明", 2.0, 5.0), ("小明", 10.0, 12.0)]
    # touches the first (5.0) and overlaps the second → one union covering both
    row = db.add_identity(mid, "小明", 5.0, 11.0)
    assert (row["start_s"], row["end_s"]) == (2.0, 12.0)
    assert _spans(db.get_identities(mid)) == [("小明", 2.0, 12.0)]
    # a different person is never merged into it
    db.add_identity(mid, "阿華", 3.0, 4.0)
    assert _spans(db.get_identities(mid)) == [("小明", 2.0, 12.0), ("阿華", 3.0, 4.0)]


def test_merge_chains_through_a_widened_union(tmp_db, sample_record):
    db = _db()
    mid = _clip(db, sample_record)
    db.add_identity(mid, "A", 20.0, 25.0)   # scanned first, misses [8, 18]…
    db.add_identity(mid, "A", 1.0, 3.0)
    row = db.add_identity(mid, "A", 2.0, 21.0)  # …but the union reaches both
    assert (row["start_s"], row["end_s"]) == (1.0, 25.0)
    assert len(db.get_identities(mid)) == 1


def test_whole_clip_absorbs_ranges_and_dedups(tmp_db, sample_record):
    db = _db()
    mid = _clip(db, sample_record)
    db.add_identity(mid, "小明", 2.0, 5.0)
    whole = db.add_identity(mid, "小明")
    assert _spans(db.get_identities(mid)) == [("小明", None, None)]
    # a range (or another whole) under an existing whole-clip row is a no-op
    assert db.add_identity(mid, "小明", 7.0, 9.0)["id"] == whole["id"]
    assert db.add_identity(mid, "  小明 ")["id"] == whole["id"]
    assert len(db.get_identities(mid)) == 1


def test_names_counts_clips_not_rows(tmp_db, sample_record):
    db = _db()
    a = _clip(db, sample_record, path="/tmp/a.mp4")
    b = _clip(db, sample_record, path="/tmp/b.mp4")
    db.add_identity(a, "小明", 1.0, 2.0)
    db.add_identity(a, "小明", 5.0, 6.0)
    db.add_identity(b, "小明")
    db.add_identity(b, "阿華")
    assert db.get_identity_names() == [{"name": "小明", "count": 2}, {"name": "阿華", "count": 1}]


# ── scenes `people` ──────────────────────────────────────────────────────────

def test_scene_people_overlap_rule():
    import scenes
    rows = [{"name": "W", "start_s": None, "end_s": None},
            {"name": "R", "start_s": 5.0, "end_s": 10.0}]
    assert scenes.scene_people(0.0, 5.0, rows) == ["W"]          # ends at 5: no leak
    assert scenes.scene_people(4.9, 6.0, rows) == ["R", "W"]
    assert scenes.scene_people(10.0, 20.0, rows) == ["W"]        # starts at 10: no leak
    assert scenes.scene_people(7.0, 7.0, rows) == ["R", "W"]     # zero-length scene
    assert scenes.scene_people(0.0, 5.0, []) == []


def test_http_scenes_people_last_and_only_on_annotated_clips(fastapi_client, sample_record):
    db = _db()
    mid = _clip(db, sample_record)
    other = _clip(db, sample_record, path="/tmp/other.mp4")
    before = fastapi_client.get("/api/media/{0}/scenes".format(other)).content
    db.add_identity(mid, "小明")
    db.add_identity(mid, "阿華", 12.0, 15.0)
    # annotating clip A leaves clip B's body byte-identical (no `people` key)
    assert fastapi_client.get("/api/media/{0}/scenes".format(other)).content == before
    assert b"people" not in before

    data = fastapi_client.get("/api/media/{0}/scenes".format(mid)).json()
    for sc in data["scenes"]:
        assert list(sc)[-1] == "people"
    assert [sc["people"] for sc in data["scenes"]] == [["小明"], ["小明", "阿華"], ["小明"]]


def test_mcp_get_scenes_and_export_project_carry_people(tmp_db, sample_record):
    pytest.importorskip("mcp")
    mcp_server = importlib.import_module("mcp_server")
    db = _db()
    mid = _clip(db, sample_record)
    plain = _clip(db, sample_record, path="/tmp/plain.mp4")
    db.add_identity(mid, "阿華", 0.0, 3.0)
    body = mcp_server.get_scenes_impl(mid)
    assert [sc["people"] for sc in body["scenes"]] == [["阿華"], [], []]
    assert list(body["scenes"][0])[-1] == "people"
    assert all("people" not in sc for sc in mcp_server.get_scenes_impl(plain)["scenes"])

    page = mcp_server.export_project_impl(include_transcript=False)
    by_id = {it["id"]: it for it in page["items"]}
    assert [sc["people"] for sc in by_id[mid]["scenes"]] == [["阿華"], [], []]
    assert all("people" not in sc for sc in by_id[plain]["scenes"])
    # `who:` works as an export filter too
    only = mcp_server.export_project_impl(filters="who:阿華", include_transcript=False)
    assert [it["id"] for it in only["items"]] == [mid]


# ── `who:` search ────────────────────────────────────────────────────────────

def test_who_filter_substring_case_insensitive_and_literal_wildcards(tmp_db, sample_record):
    db = _db()
    import search_syntax
    a = _clip(db, sample_record, path="/tmp/a.mp4")
    b = _clip(db, sample_record, path="/tmp/b.mp4")
    c = _clip(db, sample_record, path="/tmp/c.mp4")
    db.add_identity(a, "Alice Chen")
    db.add_identity(b, "陳小明", 1.0, 2.0)
    db.add_identity(c, "a_b")

    def ids(q):
        text, conds = search_syntax.parse(q)
        assert text == ""
        with db.get_conn() as conn:
            return search_syntax.matching_ids(conn, conds)

    assert ids("who:alice") == {a}
    assert ids("who:ALICE") == {a}
    assert ids("who:小明") == {b}
    assert ids('who:"Alice Chen"') == {a}
    assert ids("who:a_b") == {c}          # `_` is literal, not "any char"
    assert ids("who:%") == set()
    assert ids("who:nobody") == set()


def test_who_filter_through_search_box(fastapi_client, sample_record):
    db = _db()
    a = _clip(db, sample_record, path="/tmp/a.mp4")
    _clip(db, sample_record, path="/tmp/b.mp4")
    db.add_identity(a, "小明")
    r = fastapi_client.get("/api/media", params={"q": "who:小明"})
    assert r.status_code == 200
    body = r.json()
    items = body["items"] if isinstance(body, dict) else body
    assert [it["id"] for it in items] == [a]


# ── HTTP API ─────────────────────────────────────────────────────────────────

def test_api_crud_roundtrip(fastapi_client, sample_record):
    db = _db()
    mid = _clip(db, sample_record)
    base = "/api/media/{0}/identities".format(mid)
    assert fastapi_client.get(base).json() == {"media_id": mid, "identities": []}

    r = fastapi_client.post(base, json={"name": " 小明 "})
    assert r.status_code == 200, r.text
    assert r.json()["identity"]["name"] == "小明"
    assert r.json()["identity"]["start_s"] is None

    r = fastapi_client.post(base, json={"name": "阿華", "start_s": 3.5, "end_s": 8})
    assert r.status_code == 200, r.text
    ident = r.json()["identity"]
    assert (ident["start_s"], ident["end_s"], ident["source"]) == (3.5, 8.0, "manual")
    rows = fastapi_client.get(base).json()["identities"]
    assert [x["name"] for x in rows] == ["小明", "阿華"]   # whole-clip first

    names = fastapi_client.get("/api/identities/names").json()
    assert {n["name"] for n in names} == {"小明", "阿華"}

    r = fastapi_client.delete("{0}/{1}".format(base, ident["id"]))
    assert r.status_code == 200
    assert [x["name"] for x in r.json()["identities"]] == ["小明"]
    assert fastapi_client.delete("{0}/{1}".format(base, ident["id"])).status_code == 404


def test_api_out_past_end_clamps(fastapi_client, sample_record):
    db = _db()
    mid = _clip(db, sample_record, duration_s=30.0)
    r = fastapi_client.post("/api/media/{0}/identities".format(mid),
                            json={"name": "X", "start_s": 25, "end_s": 30.04})
    assert r.status_code == 200
    assert r.json()["identity"]["end_s"] == 30.0


@pytest.mark.parametrize("body", [
    {"name": ""},
    {"name": "   "},
    {"name": "x", "start_s": 1.0},                 # half a range
    {"name": "x", "end_s": 1.0},
    {"name": "x", "start_s": 5.0, "end_s": 5.0},   # empty range
    {"name": "x", "start_s": 6.0, "end_s": 5.0},
    {"name": "x", "start_s": -1.0, "end_s": 5.0},
    {"name": "x", "start_s": 31.0, "end_s": 40.0},  # starts past the clip's end
])
def test_api_rejects_bad_bodies(fastapi_client, sample_record, body):
    db = _db()
    mid = _clip(db, sample_record, duration_s=30.0)
    r = fastapi_client.post("/api/media/{0}/identities".format(mid), json=body)
    assert r.status_code == 422, r.text
    assert db.get_identities(mid) == []


def test_api_unknown_media_and_cross_clip_delete(fastapi_client, sample_record):
    db = _db()
    a = _clip(db, sample_record, path="/tmp/a.mp4")
    b = _clip(db, sample_record, path="/tmp/b.mp4")
    missing = max(a, b) + 100
    assert fastapi_client.get("/api/media/{0}/identities".format(missing)).status_code == 404
    assert fastapi_client.post("/api/media/{0}/identities".format(missing),
                               json={"name": "x"}).status_code == 404
    ident = db.add_identity(a, "小明")
    # an id belonging to clip A cannot be deleted through clip B's URL
    r = fastapi_client.delete("/api/media/{0}/identities/{1}".format(b, ident["id"]))
    assert r.status_code == 404
    assert len(db.get_identities(a)) == 1


def test_api_writes_need_videos_write_scope(server_module, sample_record):
    import admin
    db = _db()
    mid = _clip(db, sample_record)
    read = admin.create_token(name="ro", scopes=["videos_read"])["raw_token"]
    write = admin.create_token(name="rw", scopes=["videos_write"])["raw_token"]
    base = "/api/media/{0}/identities".format(mid)
    with TestClient(server_module.app) as c:
        assert c.get(base).status_code == 401
        assert c.get("/api/identities/names").status_code == 401
        assert c.post(base, json={"name": "x"}).status_code == 401
        assert c.delete(base + "/1").status_code == 401
        h_ro = {"Authorization": "Bearer " + read}
        h_rw = {"Authorization": "Bearer " + write}
        assert c.get(base, headers=h_ro).status_code == 200
        assert c.get("/api/identities/names", headers=h_ro).status_code == 200
        assert c.post(base, json={"name": "x"}, headers=h_ro).status_code == 403
        assert c.delete(base + "/1", headers=h_ro).status_code == 403
        r = c.post(base, json={"name": "x"}, headers=h_rw)
        assert r.status_code == 200
        iid = r.json()["identity"]["id"]
        assert c.delete("{0}/{1}".format(base, iid), headers=h_rw).status_code == 200


def test_api_write_is_same_site_guarded(fastapi_client, sample_record):
    db = _db()
    mid = _clip(db, sample_record)
    r = fastapi_client.post("/api/media/{0}/identities".format(mid), json={"name": "x"},
                            headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert db.get_identities(mid) == []


def test_write_is_audited(fastapi_client, sample_record, tmp_path, monkeypatch):
    config = importlib.import_module("config")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    db = _db()
    mid = _clip(db, sample_record)
    fastapi_client.post("/api/media/{0}/identities".format(mid), json={"name": "小明"})
    lines = (tmp_path / ".arkiv" / "audit" / "edits.jsonl").read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    assert last["action"] == "add_identity" and last["after"] == [["小明", None, None]]
