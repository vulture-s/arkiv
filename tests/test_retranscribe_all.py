"""Phase 9.6d — project-wide batch retranscribe (2a).

Whisper is stubbed (conftest + per-test monkeypatch) so these exercise the batch
loop, the single-clip guard (never blank a good transcript), the shared backup /
revert, and the single-flight + queue API — not the model itself (real-audio
A/B is deferred to a GPU box).
"""
import importlib

import pytest


def _seed_audio(tmp_path, transcript=None):
    """Insert one has_audio media backed by a real (tiny) file so exists() passes."""
    db = importlib.import_module("db")
    f = tmp_path / "clip{0}.wav".format(_seed_audio.n)
    _seed_audio.n += 1
    f.write_bytes(b"\x00")
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO media (path, filename, has_audio, transcript) VALUES (?,?,1,?)",
            (str(f), f.name, transcript),
        )
        return cur.lastrowid, str(f)
_seed_audio.n = 0


@pytest.fixture
def srv(server_module, tmp_path, monkeypatch):
    """server module with active project rooted at tmp_path (for backups)."""
    config = importlib.import_module("config")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    return server_module


def test_batch_updates_all_and_backup_reverts(srv, tmp_path, monkeypatch):
    db = importlib.import_module("db")
    transcribe = importlib.import_module("transcribe")
    corrections = importlib.import_module("corrections")
    id1, p1 = _seed_audio(tmp_path, "舊逐字稿一")
    id2, p2 = _seed_audio(tmp_path, "舊逐字稿二")
    monkeypatch.setattr(
        transcribe, "transcribe",
        lambda path, language=None: ("新的逐字稿", "zh", [{"start": 0, "end": 1, "text": "新的逐字稿"}], []),
    )

    srv._run_retranscribe_all([(id1, p1), (id2, p2)], None, True)

    assert srv._retranscribe_progress["done"] == 2
    assert srv._retranscribe_progress["failed"] == 0
    assert srv._retranscribe_progress["backup"]
    with db.get_conn() as conn:
        got = [r["transcript"] for r in conn.execute("SELECT transcript FROM media ORDER BY id")]
    assert got == ["新的逐字稿", "新的逐字稿"]

    # the snapshot is a normal correction-backup → the shared revert restores it
    corrections.revert()
    with db.get_conn() as conn:
        back = [r["transcript"] for r in conn.execute("SELECT transcript FROM media ORDER BY id")]
    assert back == ["舊逐字稿一", "舊逐字稿二"]


def test_batch_guard_never_blanks_good_transcript(srv, tmp_path, monkeypatch):
    db = importlib.import_module("db")
    transcribe = importlib.import_module("transcribe")
    id1, p1 = _seed_audio(tmp_path, "務必保留這段")
    # an empty decode on a clip that already has text = transient failure, not intent
    monkeypatch.setattr(transcribe, "transcribe", lambda path, language=None: ("", "zh", [], []))

    srv._run_retranscribe_all([(id1, p1)], None, False)

    assert srv._retranscribe_progress["failed"] == 1
    assert srv._retranscribe_progress["done"] == 1
    with db.get_conn() as conn:
        kept = conn.execute("SELECT transcript FROM media WHERE id=?", (id1,)).fetchone()["transcript"]
    assert kept == "務必保留這段"  # untouched


def test_batch_missing_file_counts_failed(srv, tmp_path, monkeypatch):
    db = importlib.import_module("db")
    transcribe = importlib.import_module("transcribe")
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO media (path, filename, has_audio, transcript) VALUES (?,?,1,?)",
            ("/no/such/file.wav", "ghost.wav", "原文"),
        )
        gid = cur.lastrowid
    monkeypatch.setattr(transcribe, "transcribe", lambda path, language=None: ("x", "zh", [], []))
    srv._run_retranscribe_all([(gid, "/no/such/file.wav")], None, False)
    assert srv._retranscribe_progress["failed"] == 1
    with db.get_conn() as conn:
        assert conn.execute("SELECT transcript FROM media WHERE id=?", (gid,)).fetchone()["transcript"] == "原文"


def test_api_queue_and_status(fastapi_client, server_module, tmp_path, monkeypatch):
    db = importlib.import_module("db")
    config = importlib.import_module("config")
    transcribe = importlib.import_module("transcribe")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    _seed_audio(tmp_path, "甲")
    monkeypatch.setattr(transcribe, "transcribe", lambda path, language=None: ("乙", "zh", [], []))
    r = fastapi_client.post("/api/retranscribe-all", json={"backup": False})
    assert r.status_code == 200
    assert r.json()["queued"] == 1
    status = fastapi_client.get("/api/retranscribe-all/status").json()
    assert status["total"] == 1


def test_api_no_audio_queues_zero(fastapi_client, server_module, tmp_path, monkeypatch):
    config = importlib.import_module("config")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    r = fastapi_client.post("/api/retranscribe-all", json={})
    assert r.json()["queued"] == 0


def test_api_refuses_concurrent_run(fastapi_client, server_module, tmp_path, monkeypatch):
    config = importlib.import_module("config")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    _seed_audio(tmp_path, "丙")
    server_module._retranscribe_active = True  # pretend a batch is mid-flight
    try:
        r = fastapi_client.post("/api/retranscribe-all", json={})
        assert r.status_code == 409
    finally:
        server_module._retranscribe_active = False
