"""12.6 drag-out: the backend hands the desktop shell a real path to drag."""
import os
import shutil
import subprocess
import time

import pytest

import config
import db

pytestmark = pytest.mark.skipif(shutil.which(config.FFMPEG_PATH) is None, reason="needs ffmpeg")


@pytest.fixture
def clip(tmp_path, sample_record, fastapi_client):
    src = tmp_path / "clip.mp4"
    subprocess.run([config.FFMPEG_PATH, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=160x90:rate=10:duration=6", "-g", "10", "-pix_fmt", "yuv420p", str(src)],
                   check=True)
    rec = sample_record(path=str(src), filename="clip.mp4", duration_s=6.0)
    db.upsert(rec)
    with db.get_conn() as conn:
        mid = conn.execute("SELECT id FROM media WHERE path=?", (str(src),)).fetchone()[0]
    return mid, src


def test_no_trim_drags_the_original(fastapi_client, clip):
    mid, src = clip
    r = fastapi_client.post(f"/api/media/{mid}/drag-source", json={})
    assert r.status_code == 200
    assert r.json() == {"path": str(src), "kind": "original", "keyframe_cut": False}


def test_trim_cuts_a_subclip_once_and_reuses_it(fastapi_client, clip, monkeypatch, tmp_path):
    from routers import dragout

    monkeypatch.setattr(dragout, "dragout_dir", lambda: tmp_path / "dragout")
    mid, _src = clip
    r = fastapi_client.post(f"/api/media/{mid}/drag-source", json={"in_s": 2, "out_s": 4})
    body = r.json()
    assert r.status_code == 200 and body["kind"] == "subclip" and body["keyframe_cut"] is True
    out = body["path"]
    assert os.path.getsize(out) > 0 and out.endswith("_2.00-4.00.mp4")
    first = os.stat(out).st_ino
    again = fastapi_client.post(f"/api/media/{mid}/drag-source", json={"in_s": 2, "out_s": 4}).json()
    assert again["path"] == out and os.stat(out).st_ino == first, "re-drag must reuse, not re-cut"
    assert not [p for p in os.listdir(tmp_path / "dragout") if p.startswith(".part-")]


def test_missing_original_is_a_clear_404(fastapi_client, clip):
    mid, src = clip
    src.unlink()
    r = fastapi_client.post(f"/api/media/{mid}/drag-source", json={})
    assert r.status_code == 404 and "NAS" in r.json()["detail"]


def test_sweep_removes_only_old_files(tmp_path):
    from routers import dragout

    old, new = tmp_path / "old.mp4", tmp_path / "new.mp4"
    old.write_bytes(b"x"), new.write_bytes(b"x")
    past = time.time() - (dragout.KEEP_DAYS + 1) * 86400
    os.utime(old, (past, past))
    assert dragout._sweep(tmp_path) == 1
    assert not old.exists() and new.exists()
