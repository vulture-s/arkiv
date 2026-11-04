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
import os
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

# Windows CreateProcess caps lpCommandLine at 32767 chars (POSIX ARG_MAX is ~1MB).
_WIN_CMDLINE_MAX = 32767

# Writes three rows from a separate process. It builds its own vectors and takes
# path / collection / dim from argv: the earlier version inlined three 1024-float
# reprs, a ~58K-char command line that died in subprocess._execute_child with
# WinError 206 on Windows before running a line, while staying green on POSIX.
_GRANDCHILD = (
    'import os, sys\n'
    'os.environ["ANONYMIZED_TELEMETRY"] = "False"\n'
    'import chromadb\n'
    'path, name, D = sys.argv[1], sys.argv[2], int(sys.argv[3])\n'
    'def emb(s): return [float((s * 3 + i) % 11) / 11.0 for i in range(D)]\n'
    'c = chromadb.PersistentClient(path=path)\n'
    'col = c.get_or_create_collection(name)\n'
    'col.add(ids=["c1","c2","c3"], embeddings=[emb(5),emb(6),emb(7)], documents=["a","b","c"])\n'
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

child = sys.argv[3]   # _GRANDCHILD, passed in rather than inlined (see there)
argv = [sys.executable, "-c", child, chroma, vectordb.COLLECTION_NAME, str(D)]
# Hold every platform to the Windows limit, so a regression is red on Mac too.
n = len(subprocess.list2cmdline(argv))
if n >= 32767:
    print("CMDLINE_TOO_LONG", n)
    sys.exit(3)
g = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")
if g.returncode != 0:
    sys.stderr.write(g.stderr[-2000:])
    sys.exit(4)

res = vectordb.get_collection().query(query_embeddings=[emb(5)], n_results=10)
print("HITS", len(res["ids"][0]))
'''


@_needs_real_chromadb
def test_search_sees_another_process_write(tmp_path):
    """Without the fix this returns 1 of 4: count() is right while query() is not."""
    chroma = tmp_path / "chroma"
    chroma.mkdir()
    r = subprocess.run(
        [sys.executable, "-c", _E2E, str(chroma), str(config.BASE_DIR), _GRANDCHILD],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
        # Parent decodes utf-8; the child writes the locale encoding by default.
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    assert "CMDLINE_TOO_LONG" not in r.stdout, (
        "grandchild command line exceeds Windows' %d-char limit: " % _WIN_CMDLINE_MAX + r.stdout[-200:])
    assert r.returncode == 0, r.stderr[-2000:]
    hits = [l for l in r.stdout.splitlines() if l.startswith("HITS")]
    assert hits, r.stdout[-2000:] + r.stderr[-2000:]
    assert hits[0] == "HITS 4", "search missed rows written by another process"


def test_e2e_command_lines_fit_the_windows_limit(tmp_path):
    """Not gated on real chromadb: no CI job installs it, so the e2e test above is
    skipped everywhere in CI and its own length check never runs there. This one
    pins the constraint that made it red on Windows, on every platform."""
    deep = str(tmp_path / ("d" * 200) / "chroma")
    for argv in (
        [sys.executable, "-c", _E2E, deep, str(config.BASE_DIR), _GRANDCHILD],
        [sys.executable, "-c", _GRANDCHILD, deep, vectordb.COLLECTION_NAME,
         str(config.EMBED_DIM)],
    ):
        n = len(subprocess.list2cmdline(argv))
        assert n < _WIN_CMDLINE_MAX, "%d chars: Windows CreateProcess would refuse it" % n
