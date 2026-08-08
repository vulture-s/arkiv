"""hash_verified_at writer (audit 2026-07-30): the column was declared (db.py:254) and
allow-listed (_ALLOWED_COLS) but had NO writer anywhere in the codebase → NULL for all
1506 rows. Ingest now stamps it inline via the record upsert; db.set_hash_verified is the
targeted writer for a one-off integrity backfill / future re-verify pass."""
import importlib
from datetime import datetime, timezone

db = importlib.import_module("db")


def test_set_hash_verified_writes_timestamp(tmp_db):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO media (id, path, filename, ext, file_hash, hash_algo) "
            "VALUES (1, '/tmp/c1.mp4', 'c1.mp4', '.mp4', 'abc', 'xxh3')"
        )
    assert db.get_record_by_id(1)["hash_verified_at"] is None   # unwritten to start
    ts = datetime.now(timezone.utc).isoformat()
    db.set_hash_verified(1, ts)
    assert db.get_record_by_id(1)["hash_verified_at"] == ts


def test_upsert_persists_hash_verified_at(tmp_db):
    # the ingest write-path sets record["hash_verified_at"]; since it's in _ALLOWED_COLS
    # the upsert must persist it (previously nothing ever set it).
    ts = datetime.now(timezone.utc).isoformat()
    db.upsert({"path": "/tmp/c2.mp4", "filename": "c2.mp4", "ext": ".mp4",
               "file_hash": "def", "hash_algo": "xxh3", "hash_verified_at": ts})
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT hash_verified_at FROM media WHERE path=?", ("/tmp/c2.mp4",)
        ).fetchone()
    assert row[0] == ts
