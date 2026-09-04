"""Cross-process HNSW staleness (#408).

Why this file does not patch `SharedSystemClient` directly
----------------------------------------------------------
`tests/conftest.py` installs a stub `chromadb` module (PersistentClient only, no
`chromadb.api` submodule). Any test that does

    from chromadb.api.shared_system_client import SharedSystemClient
    SharedSystemClient.clear_system_cache = <spy>

raises ImportError, and if that import sits inside a `try/except` the spy is
never installed and every assertion guarded by it is skipped. Such a test is
green without having exercised the thing it names. (Measured: the assertion
never ran, and separately the real `clear_system_cache` is a *staticmethod*
taking no arguments, so a `lambda self:` spy would `TypeError` even with the
real module present — two mistakes that mask each other.)

So the unit tests below patch `vectordb._drop_chroma_system_cache`, a seam on
our own module, and the end-to-end behaviour is covered by a subprocess test
that runs against the real chromadb.
"""
import importlib
import subprocess
import sys

import pytest

vectordb = importlib.import_module("vectordb")
config = importlib.import_module("config")


@pytest.fixture
def chroma_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(vectordb, "CHROMA_PATH", tmp_path)
    monkeypatch.setattr(vectordb, "_CHROMA_DB_MTIME", 0.0)
    return tmp_path


def _spy(monkeypatch, succeeds=True):
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return succeeds

    monkeypatch.setattr(vectordb, "_drop_chroma_system_cache", fake)
    return calls


# ── the seam itself ──────────────────────────────────────────────────────────
def test_drop_returns_false_when_api_missing(monkeypatch):
    """Under the conftest stub there is no chromadb.api — the helper must report
    that, not raise. This is the condition every other test here relies on."""
    assert vectordb._drop_chroma_system_cache() is False


# ── mtime tracking ───────────────────────────────────────────────────────────
def test_no_db_file_is_a_noop(chroma_dir, monkeypatch):
    calls = _spy(monkeypatch)
    vectordb._check_chroma_staleness()
    assert calls["n"] == 0
    assert vectordb._CHROMA_DB_MTIME == 0.0


def test_first_sight_of_db_drops_cache_and_records_mtime(chroma_dir, monkeypatch):
    (chroma_dir / "chroma.sqlite3").write_text("v1")
    calls = _spy(monkeypatch)
    vectordb._check_chroma_staleness()
    assert calls["n"] == 1
    assert vectordb._CHROMA_DB_MTIME > 0


def test_unchanged_mtime_does_not_drop_again(chroma_dir, monkeypatch):
    (chroma_dir / "chroma.sqlite3").write_text("v1")
    calls = _spy(monkeypatch)
    vectordb._check_chroma_staleness()
    assert calls["n"] == 1
    vectordb._check_chroma_staleness()
    vectordb._check_chroma_staleness()
    assert calls["n"] == 1, "an unchanged DB must not keep clearing the cache"


def test_external_write_drops_cache_again(chroma_dir, monkeypatch):
    db = chroma_dir / "chroma.sqlite3"
    db.write_text("v1")
    calls = _spy(monkeypatch)
    vectordb._check_chroma_staleness()
    first = vectordb._CHROMA_DB_MTIME

    import os
    os.utime(db, (first + 10, first + 10))  # simulate the other process's write

    vectordb._check_chroma_staleness()
    assert calls["n"] == 2
    assert vectordb._CHROMA_DB_MTIME != first


# ── the ordering that pixb/arkiv#18 had backwards ────────────────────────────
def test_failed_drop_does_not_consume_the_mtime(chroma_dir, monkeypatch):
    """🔴 The regression this file exists for.

    Advancing the mtime before the drop succeeds means one silent failure marks
    that write as handled forever: the next call compares equal, skips, and the
    index stays stale until the process restarts. The retry must survive.
    """
    (chroma_dir / "chroma.sqlite3").write_text("v1")
    calls = _spy(monkeypatch, succeeds=False)

    vectordb._check_chroma_staleness()
    assert calls["n"] == 1
    assert vectordb._CHROMA_DB_MTIME == 0.0, "a failed drop must not record the mtime"

    vectordb._check_chroma_staleness()
    assert calls["n"] == 2, "the same staleness must be retried, not swallowed"


def test_recovers_once_the_drop_starts_working(chroma_dir, monkeypatch):
    (chroma_dir / "chroma.sqlite3").write_text("v1")
    _spy(monkeypatch, succeeds=False)
    vectordb._check_chroma_staleness()
    assert vectordb._CHROMA_DB_MTIME == 0.0

    calls = _spy(monkeypatch, succeeds=True)
    vectordb._check_chroma_staleness()
    assert calls["n"] == 1
    assert vectordb._CHROMA_DB_MTIME > 0


# ── get_collection wires it in ───────────────────────────────────────────────
def test_get_collection_checks_staleness(monkeypatch):
    seen = {"n": 0}
    monkeypatch.setattr(vectordb, "_check_chroma_staleness",
                        lambda: seen.__setitem__("n", seen["n"] + 1))
    monkeypatch.setattr(vectordb, "_assert_collection_compatible", lambda col: None)
    monkeypatch.setattr(vectordb, "VECTOR_BACKEND", "chroma")
    vectordb.get_collection()
    assert seen["n"] == 1


# ── end-to-end against the real chromadb ─────────────────────────────────────
def _real_chromadb_available() -> bool:
    r = subprocess.run(
        [sys.executable, "-c", "import chromadb.api.shared_system_client"],
        capture_output=True,
    )
    return r.returncode == 0


_needs_real_chromadb = pytest.mark.skipif(
    not _real_chromadb_available(), reason="real chromadb not importable"
)

# Runs in a clean interpreter so the conftest stub is out of the way. Writes from
# a grandchild process, then asks OUR get_collection() whether search can see it.
_E2E = r'''
import os, sys, subprocess, pathlib
os.environ["ANONYMIZED_TELEMETRY"] = "False"
chroma = sys.argv[1]
os.environ["ARKIV_CHROMA_PATH"] = chroma
os.environ["ARKIV_PROJECT_ROOT"] = str(pathlib.Path(chroma).parent)
sys.path.insert(0, sys.argv[2])

import vectordb
D = vectordb.EMBED_DIM
def emb(s): return [float((s * 3 + i) % 11) / 11.0 for i in range(D)]

col = vectordb.get_collection()
col.add(ids=["parent1"], embeddings=[emb(1)], documents=["parent"])
col.query(query_embeddings=[emb(1)], n_results=10)   # force the HNSW index in

child = (
    'import os\n'
    'os.environ["ANONYMIZED_TELEMETRY"] = "False"\n'
    'import chromadb\n'
    'c = chromadb.PersistentClient(path=r"%s")\n'
    'col = c.get_or_create_collection("%s")\n'
    'col.add(ids=["c1","c2","c3"], embeddings=[%s,%s,%s], documents=["a","b","c"])\n'
) % (chroma, vectordb.COLLECTION_NAME, emb(5), emb(6), emb(7))
subprocess.run([sys.executable, "-c", child], capture_output=True, check=True)

res = vectordb.get_collection().query(query_embeddings=[emb(5)], n_results=10)
print("HITS", len(res["ids"][0]))
'''


@_needs_real_chromadb
def test_search_sees_another_process_write(tmp_path):
    """Without the fix this returns 1 of 4: count() is right while query() is not."""
    chroma = tmp_path / "chroma"
    chroma.mkdir()
    r = subprocess.run(
        [sys.executable, "-c", _E2E, str(chroma), str(config.BASE_DIR)],
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    hits = [l for l in r.stdout.splitlines() if l.startswith("HITS")]
    assert hits, r.stdout[-2000:] + r.stderr[-2000:]
    assert hits[0] == "HITS 4", "search missed rows written by another process"
