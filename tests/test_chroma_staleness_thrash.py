"""The staleness check must not treat its own cache drop as somebody else's write.

#421 added an mtime check so a write from another process (embed.py, MCP, an
ingest subprocess) invalidates this process's HNSW index. It fed itself:

    check sees a newer mtime  →  drops the System cache
    → next PersistentClient(path) has to rebuild from disk
    → that rebuild WRITES to chroma.sqlite3, moving the mtime
    → next call sees a newer file, drops again, forever

Measured on the real chromadb before the fix: six `get_collection()` calls with
no external write at all produced SIX drops, each reloading the whole index.
After: one. An external write is still caught.

The tests below do not need real chromadb (conftest stubs it). They reproduce
the loop directly: a client whose construction touches the file, which is exactly
what the rebuild does.
"""
import importlib

import pytest

vectordb = importlib.import_module("vectordb")


@pytest.fixture
def chroma_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(vectordb, "CHROMA_PATH", tmp_path)
    monkeypatch.setattr(vectordb, "_CHROMA_DB_MTIME", 0.0)
    db = tmp_path / "chroma.sqlite3"
    db.write_text("seed")
    return db


def _count_drops(monkeypatch, succeeds=True):
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return succeeds

    monkeypatch.setattr(vectordb, "_drop_chroma_system_cache", fake)
    return calls


def _touch(db, offset):
    import os
    st = db.stat()
    os.utime(db, (st.st_atime, st.st_mtime + offset))


# ── the loop itself ──────────────────────────────────────────────────────────
def test_recording_after_the_rebuild_absorbs_our_own_write(chroma_dir, monkeypatch):
    """🔴 The regression. Simulate one full round: check, then the rebuild
    writes, then we record. The next check must see nothing new."""
    calls = _count_drops(monkeypatch)

    vectordb._check_chroma_staleness()      # first sight of the file
    assert calls["n"] == 1
    _touch(chroma_dir, 5)                   # the rebuild's own write
    vectordb._record_chroma_mtime()

    vectordb._check_chroma_staleness()      # a second, idle call
    assert calls["n"] == 1, "our own rebuild write must not read as staleness"


def test_many_idle_rounds_drop_once(chroma_dir, monkeypatch):
    calls = _count_drops(monkeypatch)
    for i in range(6):
        vectordb._check_chroma_staleness()
        _touch(chroma_dir, 5 * (i + 1))     # every rebuild moves the file
        vectordb._record_chroma_mtime()
    assert calls["n"] == 1, "six idle calls dropped {0} times".format(calls["n"])


def test_a_real_external_write_is_still_caught(chroma_dir, monkeypatch):
    calls = _count_drops(monkeypatch)
    vectordb._check_chroma_staleness()
    _touch(chroma_dir, 5)
    vectordb._record_chroma_mtime()

    _touch(chroma_dir, 100)                 # another process writes
    vectordb._check_chroma_staleness()
    assert calls["n"] == 2


# ── the pieces on their own ──────────────────────────────────────────────────
def test_record_is_a_noop_when_the_file_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(vectordb, "CHROMA_PATH", tmp_path)
    monkeypatch.setattr(vectordb, "_CHROMA_DB_MTIME", 7.0)
    vectordb._record_chroma_mtime()
    assert vectordb._CHROMA_DB_MTIME == 7.0, "a missing file must not reset the mark"


def test_a_failed_drop_is_still_retried(chroma_dir, monkeypatch):
    """The ordering #421 argued for, which the thrash made unreachable in
    production: a drop that fails must not consume the mtime."""
    calls = _count_drops(monkeypatch, succeeds=False)
    vectordb._check_chroma_staleness()
    assert vectordb._CHROMA_DB_MTIME == 0.0
    vectordb._check_chroma_staleness()
    assert calls["n"] == 2


def test_get_collection_records_the_mark_inside_the_lock(monkeypatch):
    """The re-read has to happen while the lock is still held, or a concurrent
    caller can slip a drop in between the rebuild and the record."""
    import inspect

    src = inspect.getsource(vectordb.get_collection)
    body = src.split("with _CHROMA_LOCK:")[1]
    record_line = next(i for i, l in enumerate(body.splitlines())
                       if "_record_chroma_mtime()" in l)
    dedented = [l for l in body.splitlines()[:record_line]
                if l.strip() and not l.strip().startswith("#")]
    assert dedented, "expected the client build to precede the record"
    assert "_assert_collection_compatible" not in "\n".join(
        body.splitlines()[:record_line]), "record must come before leaving the lock"
