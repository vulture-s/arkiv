"""Tests for the pgvector backend (pgvector_backend.PgCollection).

Three layers, from cheapest to most real:
  1. Pure helpers (_vec_literal / _parse_vec) — vector text round-trip.
  2. Fake-connection tests — construct PgCollection against an in-memory fake
     psycopg, asserting the SQL it emits and that query()/get() return the exact
     Chroma-collection shapes vectordb.py downstream code depends on. No DB, no
     psycopg install required, so these run everywhere.
  3. Live round-trip — only when ARKIV_PG_DSN points at a real vchord pg;
     skipped otherwise. Proves upsert → count → query → delete end to end.
"""
import importlib
import os
import sys
import types

import pytest


# ── layer 1: pure helpers ──────────────────────────────────────────────────
def test_vec_literal_and_parse_roundtrip():
    pgb = importlib.import_module("pgvector_backend")
    vec = [0.0, -1.0166817903518677, 3.5, 2e-8]
    lit = pgb._vec_literal(vec)
    assert lit.startswith("[") and lit.endswith("]")
    back = pgb._parse_vec(lit)
    assert len(back) == len(vec)
    for a, b in zip(back, vec):
        assert a == pytest.approx(b)


# ── layer 2: fake-connection wiring ─────────────────────────────────────────
class _Rec:
    """Shared recorder: captures executed (sql, params) and doles out canned
    fetch results in call order."""
    def __init__(self):
        self.execs = []
        self.fetchone_vals = []
        self.fetchall_vals = []


class _Cur:
    def __init__(self, rec):
        self.rec = rec

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.rec.execs.append((sql, params))

    def executemany(self, sql, rows):
        self.rec.execs.append((sql, list(rows)))

    def fetchone(self):
        return self.rec.fetchone_vals.pop(0)

    def fetchall(self):
        return self.rec.fetchall_vals.pop(0)

    def commit(self):
        pass


class _Conn:
    def __init__(self, rec):
        self.rec = rec

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _Cur(self.rec)

    def commit(self):
        pass


def _fake_psycopg(rec):
    mod = types.ModuleType("psycopg")
    mod.connect = lambda dsn: _Conn(rec)
    return mod


@pytest.fixture
def pg_col(monkeypatch):
    """A PgCollection wired to a fresh recorder + fake psycopg. Returns
    (collection, recorder). __init__ runs the idempotent DDL, so exec[0] is the
    schema statement."""
    rec = _Rec()
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(rec))
    pgb = importlib.import_module("pgvector_backend")
    col = pgb.PgCollection(
        dsn="postgresql://rag@localhost:5433/rag",
        dim=1024, model="bge-m3", collection="media_assets",
    )
    return col, rec


def test_init_stamps_metadata_and_runs_ddl(pg_col):
    col, rec = pg_col
    # stamp mirrors Chroma so vectordb._assert_collection_compatible fast-paths
    assert col.metadata["embed_model"] == "bge-m3"
    assert col.metadata["embed_dim"] == 1024
    assert col.metadata["hnsw:space"] == "cosine"
    assert col.table == "emb_1024"
    ddl_sql = rec.execs[0][0]
    assert "CREATE TABLE IF NOT EXISTS emb_1024" in ddl_sql
    assert "UNIQUE INDEX" in ddl_sql and "(collection, doc_id)" in ddl_sql


def test_count_scopes_by_collection(pg_col):
    col, rec = pg_col
    rec.fetchone_vals.append((7,))
    assert col.count() == 7
    sql, params = rec.execs[-1]
    assert "count(*)" in sql and "collection = %s" in sql
    assert params == ("media_assets",)


def test_upsert_uses_on_conflict_and_payload(pg_col):
    col, rec = pg_col
    col.upsert(
        ids=["12_t0"],
        embeddings=[[0.1, 0.2, 0.3]],
        documents=["hello"],
        metadatas=[{"media_id": "12", "project_name": "demo", "filename": "a.mp4"}],
    )
    sql, rows = rec.execs[-1]
    assert "INSERT INTO emb_1024" in sql
    assert "ON CONFLICT (collection, doc_id) DO UPDATE" in sql
    assert len(rows) == 1
    coll, ns, doc_id, content, payload_json, emb_lit = rows[0]
    assert coll == "media_assets"
    assert ns == "demo"           # namespace derived from project_name
    assert doc_id == "12_t0"
    assert content == "hello"
    assert '"media_id": "12"' in payload_json
    assert emb_lit == "[0.1,0.2,0.3]"


def test_delete_scopes_by_media_id(pg_col):
    col, rec = pg_col
    col.delete(where={"media_id": "12"})
    sql, params = rec.execs[-1]
    assert "DELETE FROM emb_1024" in sql
    assert "payload->>'media_id' = %s" in sql
    assert params == ("media_assets", "12")


def test_delete_without_media_id_is_noop(pg_col):
    col, rec = pg_col
    before = len(rec.execs)
    col.delete(where={})
    assert len(rec.execs) == before  # emitted no SQL


def test_query_returns_chroma_nested_shape(pg_col):
    col, rec = pg_col
    # two candidate rows: (content, payload dict, distance)
    rec.fetchall_vals.append([
        ("doc one", {"media_id": "1", "filename": "a.mp4"}, 0.10),
        ("doc two", {"media_id": "2", "filename": "b.mp4"}, 0.42),
    ])
    raw = col.query([[0.1, 0.2, 0.3]], n_results=5)
    # Chroma wraps each field one level deep (per-query list)
    assert raw["documents"] == [["doc one", "doc two"]]
    assert raw["metadatas"][0][0]["media_id"] == "1"
    assert raw["distances"] == [[0.10, 0.42]]
    sql, params = rec.execs[-1]
    assert "embedding <=> %s::vector" in sql
    assert "ORDER BY embedding <=> %s::vector" in sql
    assert params[-1] == 5  # LIMIT n_results


def test_query_applies_project_scope(pg_col):
    col, rec = pg_col
    rec.fetchall_vals.append([])
    col.query([[0.1]], n_results=3, where={"project_name": {"$in": ["p1", "p2"]}})
    sql, params = rec.execs[-1]
    assert "payload->>'project_name' = ANY(%s)" in sql
    assert ["p1", "p2"] in params


def test_get_returns_ids_and_embeddings(pg_col):
    col, rec = pg_col
    rec.fetchall_vals.append([("1_t0", "[0.5,0.6]")])
    out = col.get(where={"media_id": "1"}, include=["embeddings"], limit=1)
    assert out["ids"] == ["1_t0"]
    assert out["embeddings"] == [[0.5, 0.6]]
    sql, params = rec.execs[-1]
    assert "payload->>'media_id' = %s" in sql
    assert "LIMIT 1" in sql


# ── layer 3: live round-trip (skipped without a real pg) ────────────────────
_DSN = os.getenv("ARKIV_PG_DSN")


@pytest.mark.skipif(not _DSN, reason="ARKIV_PG_DSN not set — no live pgvector store")
def test_live_roundtrip():
    pytest.importorskip("psycopg")
    pgb = importlib.import_module("pgvector_backend")
    # isolate on a throwaway collection so we never touch real media_assets rows
    col = pgb.PgCollection(_DSN, dim=1024, model="bge-m3",
                           collection="__pytest_roundtrip__")
    col.reset()
    vec = [0.0] * 1024
    vec[0] = 1.0
    col.upsert(
        ids=["99_t0"],
        embeddings=[vec],
        documents=["round trip doc"],
        metadatas=[{"media_id": "99", "filename": "rt.mp4", "project_name": "pytest"}],
    )
    try:
        assert col.count() == 1
        raw = col.query([vec], n_results=1)
        assert raw["documents"][0][0] == "round trip doc"
        assert raw["distances"][0][0] == pytest.approx(0.0, abs=1e-4)  # self match
        got = col.get(where={"media_id": "99"}, include=["embeddings"], limit=1)
        assert got["embeddings"][0][0] == pytest.approx(1.0)
        col.delete(where={"media_id": "99"})
        assert col.count() == 0
    finally:
        col.reset()
