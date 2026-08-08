"""Regression tests for the "向量索引靜默過期" bug.

An already-indexed media whose vision description / transcript changed AFTER it was
embedded used to be skipped forever (embed.py only checked "media_id present in
Chroma OR in force_ids"). vision-only — the sole writer of descriptions — never
passed force_ids, so the whole G-corpus index froze on 07-22 while descriptions
kept being written through 07-29, and semantic search silently missed them.

These tests pin the fix: freshness is decided by CONTENT (media.embed_hash vs the
current build_doc_text hash), and a legacy row with no hash is reported as
"unverified" and re-embedded (never rendered up-to-date). Chroma/Ollama are mocked
so the freshness DECISION is tested without a live embedding backend.
"""
import importlib
import json

import pytest

import embed
import vectordb as vdb


@pytest.fixture
def dbmod(tmp_db):
    """tmp_db points config.DB_PATH at a temp file; init the schema (adds the new
    embed_hash/embedded_at columns) and hand back the db module."""
    d = importlib.import_module("db")
    d.init_db()
    return d


def _add_media(d, mid, frame_tags_list, *, transcript=None, embed_hash=None):
    """Insert a media row with a known frame_tags blob and (optionally) a stored
    embed_hash, simulating "was embedded with this content"."""
    ft = json.dumps(frame_tags_list, ensure_ascii=False)
    with d.get_conn() as c:
        c.execute(
            "INSERT INTO media (id, path, filename, ext, transcript, frame_tags, embed_hash) "
            "VALUES (?,?,?,?,?,?,?)",
            (mid, f"media/{mid}.mp4", f"{mid}.mp4", ".mp4", transcript, ft, embed_hash),
        )


def _hash_of(mid, frame_tags_list, transcript=None):
    rec = {"filename": f"{mid}.mp4", "transcript": transcript,
           "frame_tags": json.dumps(frame_tags_list, ensure_ascii=False)}
    return vdb.content_hash(rec)


@pytest.fixture
def mock_backend(monkeypatch):
    """Mock the Chroma/Ollama boundary; record which media ids get (re)embedded.
    `indexed` is the set of media_ids Chroma already holds (as strings)."""
    state = {"processed": [], "indexed": set()}

    monkeypatch.setattr(embed.vdb, "get_collection", lambda reset=False: object())
    monkeypatch.setattr(embed, "get_indexed_media_ids", lambda col: set(state["indexed"]))
    monkeypatch.setattr(embed.vdb, "delete_media", lambda col, mid: None)

    def fake_upsert(col, rec):
        state["processed"].append(rec["id"])   # no Ollama call — just record
        state["indexed"].add(str(rec["id"]))
        return 1
    monkeypatch.setattr(embed.vdb, "upsert_record", fake_upsert)
    return state


# ── AC-5: description change on an already-indexed row is detected + re-embedded ──
def test_content_change_detected_as_stale(dbmod, mock_backend):
    old = [{"description": "a man holding a cable", "tags": ["cable"]}]
    _add_media(dbmod, 1, old, embed_hash=_hash_of(1, old))
    mock_backend["indexed"] = {"1"}                     # already in Chroma, hash matches

    # vision rewrites the frame description (what vision-only does)
    new = [{"description": "a Furutech FP-209 spade, macro", "tags": ["spade", "furutech"]}]
    with dbmod.get_conn() as c:
        c.execute("UPDATE media SET frame_tags=? WHERE id=1",
                  (json.dumps(new, ensure_ascii=False),))

    res = embed.run_embed()
    assert mock_backend["processed"] == [1]             # re-embedded exactly the changed row
    assert res["stale"] == 1 and res["stale_detected"] == 1
    # and the stamp is refreshed so it won't re-embed next time
    with dbmod.get_conn() as c:
        assert c.execute("SELECT embed_hash FROM media WHERE id=1").fetchone()[0] == _hash_of(1, new)


# ── AC-3: unchanged content is content-verified and skipped (not a full re-embed) ──
def test_unchanged_content_skipped(dbmod, mock_backend):
    ft = [{"description": "a drone shot over a valley", "tags": ["drone"]}]
    _add_media(dbmod, 1, ft, embed_hash=_hash_of(1, ft))
    mock_backend["indexed"] = {"1"}

    res = embed.run_embed()
    assert mock_backend["processed"] == []              # nothing re-embedded
    assert res["stale"] == 0 and res["new"] == 0 and res["stale_detected"] == 0


# ── ③: legacy row with no embed_hash is "unverified" and re-embedded, not up-to-date ──
def test_legacy_unverified_reembedded(dbmod, mock_backend):
    ft = [{"description": "kitchen b-roll", "tags": ["kitchen"]}]
    _add_media(dbmod, 1, ft, embed_hash=None)           # legacy: indexed but never hashed
    mock_backend["indexed"] = {"1"}

    res = embed.run_embed()
    assert mock_backend["processed"] == [1]
    assert res["unverified"] == 1 and res["stale_detected"] == 1
    # after re-embed it becomes verified → next run skips it
    res2 = embed.run_embed()
    assert mock_backend["processed"] == [1]             # unchanged from the first run
    assert res2["unverified"] == 0 and res2["stale"] == 0


# ── never-indexed row is "new" (baseline: existence check still works) ──
def test_new_media_embedded(dbmod, mock_backend):
    ft = [{"description": "sunset timelapse", "tags": ["sunset"]}]
    _add_media(dbmod, 1, ft, embed_hash=None)
    mock_backend["indexed"] = set()                     # not in Chroma yet

    res = embed.run_embed()
    assert mock_backend["processed"] == [1]
    assert res["new"] == 1 and res["stale"] == 0 and res["unverified"] == 0


# ── force_ids (── --refresh path) still forces a re-embed of an unchanged, indexed row ──
def test_force_ids_still_honored(dbmod, mock_backend):
    ft = [{"description": "unchanged", "tags": ["x"]}]
    _add_media(dbmod, 1, ft, embed_hash=_hash_of(1, ft))
    mock_backend["indexed"] = {"1"}

    res = embed.run_embed(force_ids={1})
    assert mock_backend["processed"] == [1]
    assert res["forced"] == 1
