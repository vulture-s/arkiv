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


# ── #12: the single-clip retranscribe never took the slot at all ─────────────
# The batch paths all take it; this one didn't. So the way to get two whisper
# decodes running side by side on a 16 GB box was: start a batch, then click one
# clip's "retranscribe" — which is not an exotic sequence, it's what you do when
# you notice one clip came out wrong while the batch is still going.

def _seed_playable(sample_record, tmp_path):
    """A row whose media file actually exists — the route 400s before reaching the
    slot otherwise, which would make these tests pass for the wrong reason."""
    import importlib
    db = importlib.import_module("db")
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"\x00")
    db.upsert(sample_record(path=str(media), filename="clip.mp4", ext=".mp4", has_audio=1))
    return 1


def test_single_clip_retranscribe_409s_while_the_slot_is_held(
    fastapi_client, sample_record, tmp_path
):
    import server
    mid = _seed_playable(sample_record, tmp_path)
    assert server._acquire_ingest_slot() is True  # a batch is running
    try:
        r = fastapi_client.post("/api/media/%d/retranscribe" % mid, json={"language": "zh"})
        assert r.status_code == 409, r.text
    finally:
        server._release_ingest_slot()


def test_single_clip_retranscribe_releases_the_slot_even_when_it_fails(
    fastapi_client, sample_record, tmp_path, monkeypatch
):
    """A leaked slot is worse than no slot: every later ingest 409s until restart.
    The failure path is the one that leaks, so that is the one tested."""
    import server
    import transcribe

    mid = _seed_playable(sample_record, tmp_path)
    monkeypatch.setattr(transcribe, "transcribe",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg died")))

    r = fastapi_client.post("/api/media/%d/retranscribe" % mid, json={"language": "zh"})

    assert r.status_code == 500
    assert server._acquire_ingest_slot() is True, "the slot leaked on the failure path"
    server._release_ingest_slot()


def test_single_clip_retranscribe_releases_the_slot_on_success(
    fastapi_client, sample_record, tmp_path, monkeypatch
):
    import server
    import transcribe

    mid = _seed_playable(sample_record, tmp_path)
    monkeypatch.setattr(transcribe, "transcribe",
                        lambda *a, **k: ("新的逐字稿", "zh",
                                         [{"start": 0.0, "end": 1.0, "text": "新的逐字稿"}], []))

    r = fastapi_client.post("/api/media/%d/retranscribe" % mid, json={"language": "zh"})

    assert r.status_code == 200, r.text
    assert server._acquire_ingest_slot() is True
    server._release_ingest_slot()
