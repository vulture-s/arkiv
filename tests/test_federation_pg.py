"""Federated search must stay semantic under the pgvector backend.

In pg mode an external project has no ``.arkiv/chroma_db`` to open — its vectors
live in the shared store, tagged by ``project_name``. Federation opened that
directory anyway, got nothing, and fell through to ``_sql_like_search``: every
federated query silently degraded from vector search to a keyword LIKE, with no
error and no marker in the response.
"""

import importlib
import sqlite3
import types
from pathlib import Path

import pytest


def _make_project(tmp_path, name, with_chroma=False, media_count=50):
    """A project on disk. ``with_chroma=False`` is the pg-mode shape: a real
    database, no chroma directory anywhere."""
    root = tmp_path / name
    db_dir = root / ".arkiv"
    db_dir.mkdir(parents=True, exist_ok=True)
    if with_chroma:
        (db_dir / "chroma_db").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_dir / "project.db"))
    conn.execute(
        "CREATE TABLE media (id INTEGER PRIMARY KEY, path TEXT, filename TEXT, "
        "duration_s REAL, rating TEXT, lang TEXT, ext TEXT, transcript TEXT)"
    )
    conn.execute(
        "CREATE TABLE tags (id INTEGER PRIMARY KEY, media_id INTEGER, name TEXT, "
        "source TEXT DEFAULT 'manual')"
    )
    for idx in range(1, media_count + 1):
        conn.execute(
            "INSERT INTO media (id, path, filename, duration_s, rating, lang, ext, transcript) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (idx, "clips/%d.mp4" % idx, "%s_%d.mp4" % (name, idx), float(idx),
             "good", "en", ".mp4", "project %s row %d query token" % (name, idx)),
        )
    conn.commit()
    conn.close()
    return root


def _fake_vectordb(recorder):
    """A stand-in for the shared pg collection that records its scope filter."""
    def _query_collection(col, query_embeddings, n_results, project_scope=None):
        recorder.append({"scope": project_scope, "n": n_results})
        name = (project_scope or ["?"])[0]
        return {
            "documents": [["%s semantic hit" % name, "%s second hit" % name]],
            "metadatas": [[
                {"media_id": "7", "filename": "%s_7.mp4" % name, "path": "clips/7.mp4",
                 "duration_s": 7.0, "lang": "en", "chunk_type": "transcript", "chunk_idx": 0},
                {"media_id": "8", "filename": "%s_8.mp4" % name, "path": "clips/8.mp4",
                 "duration_s": 8.0, "lang": "en", "chunk_type": "transcript", "chunk_idx": 1},
            ]],
            "distances": [[0.05, 0.30]],
        }

    return types.SimpleNamespace(
        get_collection=lambda: object(),
        _query_collection=_query_collection,
        embed=lambda q: [0.42],
    )


def _exploding_chromadb():
    """Any attempt to open a chroma directory in pg mode is a bug."""
    def _boom(path):
        raise AssertionError("pg mode must not open a chroma dir: %s" % path)
    return types.SimpleNamespace(PersistentClient=_boom)


@pytest.fixture
def fed():
    return importlib.import_module("federation")


def test_pg_mode_queries_the_shared_store_scoped_to_the_project(fed, tmp_path, monkeypatch):
    project = importlib.import_module("projects").ProjectMeta(
        name="alpha", path=_make_project(tmp_path, "alpha"))
    calls = []
    monkeypatch.setattr(fed.config, "VECTOR_BACKEND", "pg")
    monkeypatch.setitem(__import__("sys").modules, "vectordb", _fake_vectordb(calls))

    hits = fed._query_vectors(project, [0.42], limit=5)

    assert calls[0]["scope"] == ["alpha"]          # scoped, not the whole store
    assert calls[0]["n"] >= 5
    assert [h["media_id"] for h in hits] == ["7", "8"]
    assert hits[0]["excerpt"] == "alpha semantic hit"
    assert hits[0]["score"] == pytest.approx(0.95)


def test_pg_mode_never_opens_a_chroma_directory(fed, tmp_path, monkeypatch):
    project = importlib.import_module("projects").ProjectMeta(
        name="alpha", path=_make_project(tmp_path, "alpha"))
    monkeypatch.setattr(fed.config, "VECTOR_BACKEND", "pg")
    monkeypatch.setattr(fed, "chromadb", _exploding_chromadb())
    monkeypatch.setitem(__import__("sys").modules, "vectordb", _fake_vectordb([]))

    hits = fed._query_vectors(project, [0.42], limit=3)
    assert hits  # would have raised AssertionError inside _exploding_chromadb


def test_chroma_mode_is_unchanged(fed, tmp_path, monkeypatch):
    """The default backend must keep its existing path — this is additive."""
    project = importlib.import_module("projects").ProjectMeta(
        name="alpha", path=_make_project(tmp_path, "alpha", with_chroma=True))
    seen = {}

    class _Col:
        def query(self, query_embeddings, n_results, include):
            seen["include"] = include
            return {"documents": [["chroma hit"]],
                    "metadatas": [[{"media_id": "1", "filename": "a.mp4", "path": "clips/1.mp4"}]],
                    "distances": [[0.1]]}

    class _Client:
        def __init__(self, path):
            seen["path"] = path

        def get_collection(self, name):
            return _Col()

    monkeypatch.setattr(fed.config, "VECTOR_BACKEND", "chroma")
    monkeypatch.setattr(fed, "chromadb", types.SimpleNamespace(PersistentClient=_Client))

    hits = fed._query_vectors(project, [0.42], limit=3)

    assert "chroma_db" in seen["path"]
    assert hits[0]["excerpt"] == "chroma hit"


def test_pg_mode_end_to_end_stays_semantic_without_any_chroma_dir(fed, tmp_path, monkeypatch):
    """The defect in one assertion: no chroma dirs anywhere, and the federated
    result is still vector hits rather than a keyword-LIKE degradation."""
    project_mod = importlib.import_module("projects")
    projects = [
        project_mod.ProjectMeta(name=name, path=_make_project(tmp_path, name))
        for name in ("alpha", "beta")
    ]
    for p in projects:
        assert not (p.path / ".arkiv" / "chroma_db").exists()

    calls = []
    monkeypatch.setattr(fed.config, "VECTOR_BACKEND", "pg")
    monkeypatch.setattr(fed.config, "discover_projects", lambda: projects)
    monkeypatch.setattr(fed, "embed_query", lambda q: [0.42])
    monkeypatch.setattr(fed, "chromadb", _exploding_chromadb())
    monkeypatch.setitem(__import__("sys").modules, "vectordb", _fake_vectordb(calls))
    fed._neg_clear()

    payload = fed.search_all_projects("query token", limit=6, per_project_limit=2, timeout=5.0)

    assert payload["projects_failed"] == 0
    assert {c["scope"][0] for c in calls} == {"alpha", "beta"}
    excerpts = {item["excerpt"] for item in payload["items"]}
    assert "alpha semantic hit" in excerpts
    assert "beta semantic hit" in excerpts
    # A SQL-LIKE degradation would have produced transcript rows instead.
    assert not any("row" in e and "query token" in e for e in excerpts)


def test_hits_from_raw_tolerates_empty_and_null_metadata(fed):
    assert fed._hits_from_raw({}, 5) == []
    raw = {"documents": [["d"]], "metadatas": [[None]], "distances": [[0.2]]}
    hits = fed._hits_from_raw(raw, 5)
    assert hits[0]["media_id"] == "None"
    assert hits[0]["score"] == pytest.approx(0.8)
