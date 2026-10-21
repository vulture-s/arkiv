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


def _duration(path):
    out = subprocess.run([config.FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return float(out.strip())


def test_short_window_is_reencoded_to_start_exactly_on_in(fastapi_client, clip, monkeypatch, tmp_path):
    from routers import dragout

    monkeypatch.setattr(dragout, "dragout_dir", lambda: tmp_path / "dragout")
    mid, _src = clip
    # the fixture's keyframes are 1 s apart (-g 10 @ 10 fps): 2.5 s would snap to 2.0 under stream copy
    r = fastapi_client.post(f"/api/media/{mid}/drag-source", json={"in_s": 2.5, "out_s": 4.0})
    body = r.json()
    assert r.status_code == 200 and body["kind"] == "subclip" and body["keyframe_cut"] is False
    out = body["path"]
    assert out.endswith("_exact_1_2.50-4.00.mp4") or "_exact_" in out
    assert abs(_duration(out) - 1.5) < 0.15, _duration(out)
    first = os.stat(out).st_ino
    again = fastapi_client.post(f"/api/media/{mid}/drag-source", json={"in_s": 2.5, "out_s": 4.0}).json()
    assert again["path"] == out and os.stat(out).st_ino == first, "re-drag must reuse, not re-cut"
    assert not [p for p in os.listdir(tmp_path / "dragout") if p.startswith(".part-")]


def test_long_window_uses_stream_copy_and_says_so(fastapi_client, clip, monkeypatch, tmp_path):
    from routers import dragout

    monkeypatch.setattr(dragout, "dragout_dir", lambda: tmp_path / "dragout")
    monkeypatch.setattr(dragout, "ACCURATE_MAX_S", 1.0)
    mid, _src = clip
    body = fastapi_client.post(f"/api/media/{mid}/drag-source", json={"in_s": 2.5, "out_s": 4.0}).json()
    assert body["keyframe_cut"] is True and "_kf_" in body["path"]
    assert _duration(body["path"]) > 1.6, "stream copy starts at the keyframe before IN"


def test_hardware_encoder_failure_falls_back_to_software(fastapi_client, clip, monkeypatch, tmp_path):
    from routers import dragout

    monkeypatch.setattr(dragout, "dragout_dir", lambda: tmp_path / "dragout")
    real = dragout._accurate_cmds

    def broken_hw_first(src, tmp, t_in, dur):
        cmds = real(src, tmp, t_in, dur)
        bogus = list(cmds[-1])
        bogus[bogus.index("libx264")] = "no_such_encoder"
        return [bogus] + [cmds[-1]]

    monkeypatch.setattr(dragout, "_accurate_cmds", broken_hw_first)
    mid, _src = clip
    body = fastapi_client.post(f"/api/media/{mid}/drag-source", json={"in_s": 1.0, "out_s": 2.0}).json()
    assert os.path.getsize(body["path"]) > 0


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
