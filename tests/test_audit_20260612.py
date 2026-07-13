"""Regression tests for the 2026-06-12 audit fix batch (blockers).

Each test pins a behaviour that was broken before the fixes so a future change
can't silently reintroduce it. See references/management/2026-06-12 audit report.
"""
import importlib
import subprocess
import types

import pytest


def _media_id(db, rec):
    with db.get_conn() as c:
        row = c.execute(
            "SELECT id FROM media WHERE path=? OR path=?",
            (rec["path"], db.to_relative(rec["path"])),
        ).fetchone()
    return row[0]


# ── H2: _to_wav raises (not silent empty) when ffmpeg audio extract fails ───────
def test_h2_to_wav_raises_on_ffmpeg_failure(monkeypatch):
    tr = importlib.import_module("transcribe")
    monkeypatch.setattr(
        tr.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=1, stderr=b"boom"),
    )
    # A failed extraction must raise, not return None (which masqueraded as
    # "no speech" and silently blanked the transcript).
    with pytest.raises(RuntimeError):
        tr._to_wav("/whatever.mp4")


# ── H3: ingest single-flight — a second concurrent ingest is rejected ───────────
def test_h3_ingest_slot_single_flight(server_module):
    assert server_module._acquire_ingest_slot() is True
    assert server_module._acquire_ingest_slot() is False, "2nd ingest must be rejected"
    server_module._release_ingest_slot()
    assert server_module._acquire_ingest_slot() is True
    server_module._release_ingest_slot()


# ── H1: retranscribe refuses to overwrite a good transcript with an empty result ─
def test_h1_retranscribe_refuses_empty_overwrite(
    fastapi_client, server_module, sample_record, monkeypatch
):
    import db
    rec = sample_record()
    rec["transcript"] = "原本就有的逐字稿"
    rec["lang"] = "zh"
    db.upsert(rec)
    mid = _media_id(db, rec)

    import transcribe as tr
    monkeypatch.setattr(tr, "transcribe", lambda *a, **k: ("", "", [], []))
    monkeypatch.setattr(server_module, "_resolve_media_path", lambda p: __file__)

    r = fastapi_client.post("/api/media/%d/retranscribe" % mid, json={"language": "zh"})
    assert r.status_code == 422
    with db.get_conn() as c:
        kept = c.execute("SELECT transcript FROM media WHERE id=?", (mid,)).fetchone()[0]
    assert kept == "原本就有的逐字稿", "empty retranscribe must not blank a good transcript"


# ── H4: reingest targets the exact media file, not the folder's first file ──────
def test_h4_reingest_targets_single_file(
    fastapi_client, server_module, sample_record, monkeypatch
):
    import db
    rec = sample_record()
    db.upsert(rec)
    mid = _media_id(db, rec)
    monkeypatch.setattr(server_module, "_resolve_media_path", lambda p: __file__)

    captured = {}

    def fake_run(cmd, *a, **k):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    # reingest now dispatches through proctree.run_tree (round-5 #2: tree-kill on
    # timeout) rather than a bare subprocess.run — stub the shared helper.
    import proctree
    monkeypatch.setattr(proctree, "run_tree", fake_run)
    r = fastapi_client.post("/api/media/%d/reingest" % mid)
    assert r.status_code == 200
    cmd = captured["cmd"]
    assert __file__ in cmd, "must reingest the exact media path"
    assert "--limit" not in cmd, "must not use --limit (would pick the folder's first file)"


# ── C1: retry-vision completes (no self-deadlock) and persists tags ─────────────
def test_c1_retry_vision_no_deadlock_persists_tags(
    fastapi_client, server_module, sample_record, monkeypatch
):
    import db
    rec = sample_record()
    db.upsert(rec)
    mid = _media_id(db, rec)
    db.upsert_frame(mid, 0, 0.0, thumbnail_path="t.jpg", description="")  # empty → retried

    import vision as vis
    monkeypatch.setattr(
        vis, "describe_frames",
        lambda paths, model=None: [{"description": "一隻貓", "tags": ["貓", "室內"], "focus_score": 80}],
    )
    monkeypatch.setattr(server_module, "_resolve_media_path", lambda p: p)

    r = fastapi_client.post("/api/media/%d/retry-vision" % mid)
    assert r.status_code == 200, r.text
    assert r.json()["patched"] == 1
    with db.get_conn() as c:
        desc = c.execute(
            "SELECT description FROM frames WHERE media_id=? AND frame_index=0", (mid,)
        ).fetchone()[0]
        tags = [row[0] for row in c.execute(
            "SELECT name FROM tags WHERE media_id=?", (mid,)
        ).fetchall()]
    assert desc == "一隻貓"
    assert "貓" in tags, "tags must persist (add_tag with _conn must not deadlock)"
