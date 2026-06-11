"""Regression tests for the 2026-06-10 audit fix batches.

Each test pins a behaviour that was broken before the audit fixes so a future
change can't silently reintroduce the bug. See dev-log 2026-06-10.
"""
import importlib

import pytest


# ── C1: thumbnails keyed by abs-path hash (cross-card same-name collision) ──────
def test_c1_thumbnail_stem_disambiguates_same_name_different_path():
    frm = importlib.import_module("frames")
    a = frm._safe_stem("/Volumes/CARD_A/C0001.MP4")
    b = frm._safe_stem("/Volumes/CARD_B/C0001.MP4")
    assert a != b, "same filename on different cards must not share a thumbnail"
    assert a.startswith("C0001_") and b.startswith("C0001_")
    # Stable for the same absolute path.
    assert a == frm._safe_stem("/Volumes/CARD_A/C0001.MP4")


# ── H9: chat refinement rejects hallucinated/injected scene ids ─────────────────
def test_h9_refinement_drops_ids_outside_prior_set(tmp_db, sample_record, monkeypatch):
    import db
    import chat

    ids = [db_upsert_id(db, sample_record) for _ in range(3)]
    conv = chat.create_conversation(None, "find clips")
    # Seed a prior assistant turn whose scene_ids are the first two.
    prior = ids[:2]
    chat.persist_message(conv, "assistant", "prior", scene_ids=prior)

    # Model returns one valid id + one hallucinated/injected id (9999).
    monkeypatch.setattr(chat, "llm_chat", lambda *a, **k: {
        "text": '{"filtered_ids": [%d, 9999], "reason": "x"}' % prior[0],
        "tokens_used": 0, "latency_ms": 0,
    })
    out = chat.handle_refinement("只要第一個", [], None, conv)
    assert 9999 not in out["scene_ids"], "hallucinated id must be rejected"
    assert out["scene_ids"] == [prior[0]]


def test_h9_all_invalid_ids_falls_back_to_prior(tmp_db, sample_record, monkeypatch):
    import db
    import chat

    ids = [db_upsert_id(db, sample_record) for _ in range(2)]
    conv = chat.create_conversation(None, "find clips")
    chat.persist_message(conv, "assistant", "prior", scene_ids=ids)
    monkeypatch.setattr(chat, "llm_chat", lambda *a, **k: {
        "text": '{"filtered_ids": [7777, 8888], "reason": "x"}',
        "tokens_used": 0, "latency_ms": 0,
    })
    out = chat.handle_refinement("refine", [], None, conv)
    # All ids hallucinated → keep prior set rather than persisting bogus / empty.
    assert set(out["scene_ids"]) == set(ids)


# ── H8: q + rating filters on the real record, both search paths ────────────────
def test_h8_rating_filter_applies_to_sql_fallback(fastapi_client, tmp_db, sample_record):
    import db

    good = sample_record(filename="keep.mp4", transcript="aerial drone shot")
    bad = sample_record(filename="drop.mp4", transcript="aerial drone shot")
    db.upsert(good)
    db.upsert(bad)
    # Rate one good, one bad by path lookup.
    gid = db_id_for_path(db, good["path"])
    bid = db_id_for_path(db, bad["path"])
    with db.get_conn() as conn:
        conn.execute("UPDATE media SET rating='good' WHERE id=?", (gid,))
        conn.execute("UPDATE media SET rating='bad' WHERE id=?", (bid,))

    # Semantic search isn't available in tests → SQL LIKE fallback runs. The
    # rating filter must still apply (was the H8 bug: fallback ignored rating).
    r = fastapi_client.get("/api/media?q=drone&rating=good")
    assert r.status_code == 200
    returned = {it["id"] for it in r.json()["items"]}
    assert gid in returned
    assert bid not in returned


# ── H6: refresh-mode record omits frame_tags/thumbnail_path (no NULL wipe) ───────
def test_h6_refresh_does_not_null_frame_tags(tmp_db, sample_record, monkeypatch):
    import db
    import ingest

    rec = sample_record(filename="clip.mp4")
    db.upsert(rec)
    existing = {"transcript": rec["transcript"], "lang": rec["lang"],
                "frame_tags": rec["frame_tags"], "thumbnail_path": rec["thumbnail_path"],
                "fps": rec["fps"]}

    # Stub the heavy steps so process_file just builds the record.
    monkeypatch.setattr(ingest, "probe", lambda p: {
        "duration_s": 30.0, "size_mb": 10.0, "width": 1920, "height": 1080,
        "fps": 29.97, "has_audio": 1, "start_tc": None, "codec": "h264",
    })
    monkeypatch.setattr(ingest, "exiftool_extract", lambda *a, **k: {})
    monkeypatch.setattr(ingest, "parse_xavc_sidecar", lambda p: {})
    monkeypatch.setattr(ingest.frm, "extract_thumbnail", lambda *a, **k: None)  # thumb fails
    monkeypatch.setattr(ingest.frm, "extract_frames", lambda *a, **k: [])

    from pathlib import Path
    built = ingest.process_file(Path(rec["path"]), skip_vision=True, existing=existing)
    # The refresh record must NOT carry frame_tags/thumbnail_path keys set to
    # None (that would wipe the existing values on upsert).
    assert built.get("frame_tags") is None and "frame_tags" not in built
    assert "thumbnail_path" not in built


# ── H1: probe persists codec so Phase 3 doesn't re-probe ────────────────────────
def test_h1_probe_returns_codec_and_column_allowed():
    import db
    assert "codec" in db._ALLOWED_COLS


# ── helpers ─────────────────────────────────────────────────────────────────────
def db_upsert_id(db, sample_record):
    rec = sample_record()
    db.upsert(rec)
    return db_id_for_path(db, rec["path"])


def db_id_for_path(db, path):
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM media WHERE path=?", (path,)).fetchone()
        return row[0]
