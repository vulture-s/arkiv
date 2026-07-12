"""Regression tests for the fable-audit 2026-07-12 concurrency-guard fixes.

Pins that whole-library retranscribe now shares the H3 single-flight ingest slot
(#11) so a concurrent /api/ingest can't load a second whisper → double-whisper OOM.
(copy_bin's slot sharing #2/#5 is covered in test_bin_copy.py.)
"""
import importlib


def _seed_audio_row(sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/ghost-audio.mp3", filename="ghost-audio.mp3",
                            ext=".mp3", has_audio=1))


def test_retranscribe_all_409_when_ingest_slot_busy(fastapi_client, sample_record):
    import server
    _seed_audio_row(sample_record)
    assert server._acquire_ingest_slot() is True  # a concurrent ingest holds the slot
    try:
        resp = fastapi_client.post("/api/retranscribe-all", json={"backup": False})
        assert resp.status_code == 409
    finally:
        server._release_ingest_slot()


def test_retranscribe_all_releases_slot_after_run(fastapi_client, sample_record):
    import server
    _seed_audio_row(sample_record)
    # slot free → the batch is queued; TestClient runs the background task
    # synchronously, and its finally must release the shared slot.
    resp = fastapi_client.post("/api/retranscribe-all", json={"backup": False})
    assert resp.status_code == 200
    assert resp.json()["queued"] == 1
    # the slot was released — a subsequent ingest can acquire it
    assert server._acquire_ingest_slot() is True
    server._release_ingest_slot()


def test_retranscribe_all_no_audio_returns_early_without_taking_slot(fastapi_client):
    # empty library (no has_audio rows) → returns before touching the slot
    import server
    resp = fastapi_client.post("/api/retranscribe-all", json={"backup": False})
    assert resp.status_code == 200
    assert resp.json()["queued"] == 0
    # slot untouched / free
    assert server._acquire_ingest_slot() is True
    server._release_ingest_slot()
