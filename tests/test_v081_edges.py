"""Regression tests for the 3 Codex-flagged v0.8.1 edges.

The 2026-06-12 Codex (GPT-5) read-only review deferred three edges to v0.8.1
(H5 abs/rel merge integrity, scene_ids dangling reference, M24 codec backfill
semantics). Verification (plan 2026-06-12-arkiv-v0.8.1-stabilization) found the
primary fixes already merged in the v0.8.0 sprint; these tests pin the edge
*behaviour* so "symbol exists" can't drift into "edge silently broken".
"""
import importlib
from pathlib import Path

import pytest


# ── H5: migrate_to_relative merges an abs/rel duplicate pair, moving the only
#       two FK children of media (frames + tags) to the survivor with no orphans.
#       The duplicate exists because upsert's ON CONFLICT(path) never fires
#       across abs↔rel forms; migrate is where the two rows finally reconcile.
def test_h5_migrate_merges_abs_rel_duplicate_no_orphans(tmp_db):
    db = importlib.import_module("db")
    config = importlib.import_module("config")

    rel = "h5merge/clip.mp4"
    abspath = str(Path(config.PROJECT_ROOT) / rel)  # to_relative(abspath) == rel

    with db.get_conn() as c:
        # survivor already holds the relative form
        c.execute("INSERT INTO media (path, filename, ext) VALUES (?,?,?)",
                  (rel, "clip.mp4", ".mp4"))
        survivor_id = c.execute("SELECT id FROM media WHERE path=?", (rel,)).fetchone()["id"]
        # legacy duplicate holds the absolute form
        c.execute("INSERT INTO media (path, filename, ext) VALUES (?,?,?)",
                  (abspath, "clip.mp4", ".mp4"))
        legacy_id = c.execute("SELECT id FROM media WHERE path=?", (abspath,)).fetchone()["id"]
    # children attached to the legacy (abs) row — these must survive the merge
    db.upsert_frame(legacy_id, 0, 0.0, thumbnail_path="legacy.jpg", description="legacy frame")
    db.add_tag(legacy_id, "legacy-tag", source="auto")

    db.migrate_to_relative()

    with db.get_conn() as c:
        # exactly one row remains, in relative form, and it's the survivor
        rows = c.execute("SELECT id, path FROM media WHERE path IN (?,?)",
                         (rel, abspath)).fetchall()
        assert len(rows) == 1
        assert rows[0]["path"] == rel
        assert rows[0]["id"] == survivor_id
        assert c.execute("SELECT 1 FROM media WHERE id=?", (legacy_id,)).fetchone() is None
        # children moved to survivor, none left orphaned on the deleted id
        assert c.execute("SELECT COUNT(*) AS n FROM frames WHERE media_id=?", (legacy_id,)).fetchone()["n"] == 0
        assert c.execute("SELECT COUNT(*) AS n FROM tags WHERE media_id=?", (legacy_id,)).fetchone()["n"] == 0
        assert c.execute("SELECT COUNT(*) AS n FROM frames WHERE media_id=?", (survivor_id,)).fetchone()["n"] == 1
        assert c.execute("SELECT name FROM tags WHERE media_id=?", (survivor_id,)).fetchone()["name"] == "legacy-tag"
        # FK integrity: no dangling child rows anywhere
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


# ── H5 collision branch: when the survivor ALREADY has the same frame_index,
#    UPDATE OR IGNORE keeps the survivor's copy and the duplicate's colliding
#    child is dropped (not duplicated, not orphaned).
def test_h5_merge_frame_collision_keeps_survivor_copy(tmp_db):
    db = importlib.import_module("db")
    config = importlib.import_module("config")

    rel = "h5collide/clip.mp4"
    abspath = str(Path(config.PROJECT_ROOT) / rel)
    with db.get_conn() as c:
        c.execute("INSERT INTO media (path, filename, ext) VALUES (?,?,?)", (rel, "clip.mp4", ".mp4"))
        survivor_id = c.execute("SELECT id FROM media WHERE path=?", (rel,)).fetchone()["id"]
        c.execute("INSERT INTO media (path, filename, ext) VALUES (?,?,?)", (abspath, "clip.mp4", ".mp4"))
        legacy_id = c.execute("SELECT id FROM media WHERE path=?", (abspath,)).fetchone()["id"]
    db.upsert_frame(survivor_id, 0, 0.0, description="survivor frame")
    db.upsert_frame(legacy_id, 0, 0.0, description="legacy frame")  # same frame_index → collides

    db.migrate_to_relative()

    with db.get_conn() as c:
        frames = c.execute("SELECT media_id, description FROM frames WHERE media_id=?", (survivor_id,)).fetchall()
        assert len(frames) == 1
        assert frames[0]["description"] == "survivor frame"  # survivor's copy won
        assert c.execute("SELECT COUNT(*) AS n FROM frames WHERE media_id=?", (legacy_id,)).fetchone()["n"] == 0
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


# ── H5 + round-5 #7: the merge must NOT cascade-delete the loser's per-language
#    transcript archive. A language present only on the abs (loser) row must be
#    re-parented onto the survivor, not destroyed by the media DELETE's FK cascade.
def test_h5_merge_preserves_loser_only_transcript_language(tmp_db):
    db = importlib.import_module("db")
    config = importlib.import_module("config")

    rel = "h5tr/clip.mp4"
    abspath = str(Path(config.PROJECT_ROOT) / rel)
    with db.get_conn() as c:
        c.execute("INSERT INTO media (path, filename, ext) VALUES (?,?,?)", (rel, "clip.mp4", ".mp4"))
        survivor_id = c.execute("SELECT id FROM media WHERE path=?", (rel,)).fetchone()["id"]
        c.execute("INSERT INTO media (path, filename, ext) VALUES (?,?,?)", (abspath, "clip.mp4", ".mp4"))
        legacy_id = c.execute("SELECT id FROM media WHERE path=?", (abspath,)).fetchone()["id"]
    # survivor has a zh transcript; the legacy (abs) row is the ONLY holder of en
    db.upsert_transcript(survivor_id, "zh", "中文逐字稿", None, None)
    db.upsert_transcript(legacy_id, "en", "english transcript", None, None)

    db.migrate_to_relative()

    with db.get_conn() as c:
        langs = {r["lang"]: r["transcript"]
                 for r in c.execute("SELECT lang, transcript FROM transcripts WHERE media_id=?",
                                    (survivor_id,)).fetchall()}
        # BOTH languages survive on the survivor — the loser-only 'en' was rescued,
        # not cascade-deleted
        assert langs == {"zh": "中文逐字稿", "en": "english transcript"}
        # nothing left dangling on the deleted loser id
        assert c.execute("SELECT COUNT(*) AS n FROM transcripts WHERE media_id=?", (legacy_id,)).fetchone()["n"] == 0
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


# ── scene_ids: chat_messages.scene_ids_json is a TEXT JSON blob, NOT a FK, so
#    an H5 merge that deletes a cited media id leaves a dangling reference. The
#    refinement resolution (`SELECT ... WHERE id IN (prior_ids)`) must drop the
#    dead id silently rather than resurrect or crash. This pins that graceful
#    degradation — the reason scene_ids needs no migration.
def test_scene_ids_dangling_reference_drops_gracefully(tmp_db):
    db = importlib.import_module("db")
    with db.get_conn() as c:
        for p in ("a.mp4", "b.mp4", "c.mp4"):
            c.execute("INSERT INTO media (path, filename, ext) VALUES (?,?,?)", (p, p, ".mp4"))
        ids = [r["id"] for r in c.execute("SELECT id FROM media ORDER BY id").fetchall()]
    prior_ids = list(ids)            # what a prior chat answer cited
    dead = prior_ids[1]
    with db.get_conn() as c:
        c.execute("DELETE FROM media WHERE id=?", (dead,))   # simulate H5 merge deleting it
        placeholders = ",".join("?" * len(prior_ids))
        resolved = [r["id"] for r in c.execute(
            "SELECT id FROM media WHERE id IN ({0})".format(placeholders), prior_ids).fetchall()]
    assert dead not in resolved              # dangling id silently dropped
    assert set(resolved) == set(prior_ids) - {dead}   # survivors still resolve


# ── M24: /api/stream must use the codec persisted at ingest, only probing when
#    the column is NULL (legacy rows), and the probe-failure path must keep the
#    UNKNOWN fall-through (never crash playback).
@pytest.fixture
def _stream_media(tmp_db, tmp_path):
    """A media row pointing at a real temp .mp4 (so the endpoint reaches codec
    resolution). Returns (db, media_id, make) where make(codec) sets the row."""
    db = importlib.import_module("db")
    f = tmp_path / "stream.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypmp42")  # bytes irrelevant; only existence + ext matter
    rec = {"path": str(f), "filename": "stream.mp4", "ext": ".mp4", "duration_s": 5.0}
    db.upsert(rec)
    with db.get_conn() as c:
        mid = c.execute("SELECT id FROM media WHERE path=?", (str(f),)).fetchone()["id"]
    return db, mid


def test_m24_stored_codec_skips_probe(fastapi_client, server_module, _stream_media, monkeypatch):
    db, mid = _stream_media
    proxy_codec = next(iter(server_module.codec.PROXY_CODECS))  # e.g. "hevc"
    with db.get_conn() as c:
        c.execute("UPDATE media SET codec=? WHERE id=?", (proxy_codec, mid))

    calls = {"n": 0}
    monkeypatch.setattr(server_module.codec, "probe_codec",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), "hevc")[1])

    r = fastapi_client.get("/api/stream/{0}".format(mid))
    assert r.status_code == 409          # browser-incompatible codec → need proxy
    assert r.json().get("need_proxy") is True
    assert calls["n"] == 0               # stored codec used; NO re-probe on playback


def test_m24_null_codec_probes_and_backfills(fastapi_client, server_module, _stream_media, monkeypatch):
    db, mid = _stream_media  # upsert left codec NULL
    proxy_codec = next(iter(server_module.codec.PROXY_CODECS))
    calls = {"n": 0}
    monkeypatch.setattr(server_module.codec, "probe_codec",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), proxy_codec)[1])

    r = fastapi_client.get("/api/stream/{0}".format(mid))
    assert r.status_code == 409
    assert calls["n"] == 1               # NULL → probed once
    with db.get_conn() as c:
        assert c.execute("SELECT codec FROM media WHERE id=?", (mid,)).fetchone()["codec"] == proxy_codec  # backfilled


def test_m24_probe_failure_falls_through_not_crash(fastapi_client, server_module, _stream_media, monkeypatch):
    db, mid = _stream_media  # codec NULL
    monkeypatch.setattr(server_module.codec, "probe_codec", lambda *a, **k: None)  # probe fails
    r = fastapi_client.get("/api/stream/{0}".format(mid))
    # None → UNKNOWN fall-through: serves the file (200/206), never 409, never 500
    assert r.status_code in (200, 206)
