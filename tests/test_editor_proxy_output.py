"""Editing proxies: ProRes Proxy written to `<source dir>/Proxy/<stem>.mov`.

The Inspector proxy can't double as an editing proxy (wrong name for Resolve's
relink, no timecode, one audio track), so this is a second, opt-in output. It
writes into the user's media folder, which is why most of these tests are about
what it must NOT do: overwrite an editor's file, leave a half-written one, or
leave an empty `Proxy/` behind after a failure.
"""
import shutil
import subprocess

import pytest

import editor_proxy as ep


@pytest.fixture
def clip(tmp_path):
    src = tmp_path / "cam" / "A001.mov"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\x00" * 16)
    return src


@pytest.fixture
def fake_probe(monkeypatch):
    import ingest
    info = {"width": 3840, "height": 2160, "duration_s": 2.0, "start_tc": "10:20:30:12"}
    monkeypatch.setattr(ingest, "probe", lambda path: dict(info))
    return info


def _fake_ffmpeg(monkeypatch, rc=0, write=b"prores", side_effect=None):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if side_effect:
            side_effect(cmd)
        if write is not None:
            with open(cmd[-1], "wb") as f:
                f.write(write)
        return subprocess.CompletedProcess(cmd, rc, "", "boom" if rc else "")

    monkeypatch.setattr(ep.subprocess, "run", run)
    return calls


def test_cmd_is_prores_proxy_with_all_audio_and_source_tc():
    cmd = ep.build_cmd("/in.mov", "/out.mov", 1080, "01:00:00;00", hwaccel=False)
    joined = " ".join(cmd)
    assert "-c:v prores_ks -profile:v 0" in joined
    assert "-map 0:v:0 -map 0:a?" in joined          # every audio track, silent clips ok
    assert cmd[cmd.index("-timecode") + 1] == "01:00:00;00"
    assert "scale=-2:'min(ih,1080)'" in cmd           # never upscales
    assert cmd[-1] == "/out.mov"


def test_cmd_omits_timecode_when_source_has_none():
    assert "-timecode" not in ep.build_cmd("/in.mov", "/out.mov", 1080, None, hwaccel=False)


def test_target_is_proxy_folder_with_source_stem(clip):
    assert ep.target_for(str(clip)) == clip.parent / "Proxy" / "A001.mov"


@pytest.mark.parametrize("ext", [".mov", ".mp4", ".mxf"])
def test_never_overwrites_an_existing_editor_proxy(clip, fake_probe, monkeypatch, ext):
    theirs = clip.parent / "Proxy" / ("A001" + ext)
    theirs.parent.mkdir()
    theirs.write_bytes(b"editor's own")
    calls = _fake_ffmpeg(monkeypatch)

    res = ep.generate(str(clip), hwaccel=False)

    assert res == {"status": ep.EXISTS, "path": str(theirs)}
    assert theirs.read_bytes() == b"editor's own"
    assert calls == []                                # did not even encode


def test_created_lands_at_target_and_leaves_no_tmp(clip, fake_probe, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch)
    res = ep.generate(str(clip), hwaccel=False)
    target = clip.parent / "Proxy" / "A001.mov"
    assert res == {"status": ep.CREATED, "path": str(target)}
    assert target.read_bytes() == b"prores"
    assert sorted(p.name for p in target.parent.iterdir()) == ["A001.mov"]
    assert calls[0][calls[0].index("-timecode") + 1] == "10:20:30:12"


def test_failure_cleans_tmp_and_the_folder_it_created(clip, fake_probe, monkeypatch):
    _fake_ffmpeg(monkeypatch, rc=1)
    res = ep.generate(str(clip), hwaccel=False)
    assert res["status"] == ep.FAILED and "rc=1" in res["reason"]
    assert not (clip.parent / "Proxy").exists()


def test_failure_keeps_a_proxy_folder_that_was_already_there(clip, fake_probe, monkeypatch):
    folder = clip.parent / "Proxy"
    folder.mkdir()
    (folder / "OTHER.mov").write_bytes(b"x")
    _fake_ffmpeg(monkeypatch, rc=1)
    ep.generate(str(clip), hwaccel=False)
    assert sorted(p.name for p in folder.iterdir()) == ["OTHER.mov"]


def test_empty_output_is_a_failure_not_a_proxy(clip, fake_probe, monkeypatch):
    _fake_ffmpeg(monkeypatch, write=b"")
    assert ep.generate(str(clip), hwaccel=False)["status"] == ep.FAILED
    assert not (clip.parent / "Proxy").exists()


def test_editor_proxy_appearing_mid_encode_wins(clip, fake_probe, monkeypatch):
    theirs = clip.parent / "Proxy" / "A001.mp4"

    def editor_drops_one_in(cmd):
        theirs.write_bytes(b"editor's own")

    _fake_ffmpeg(monkeypatch, side_effect=editor_drops_one_in)
    res = ep.generate(str(clip), hwaccel=False)
    assert res == {"status": ep.EXISTS, "path": str(theirs)}
    assert sorted(p.name for p in theirs.parent.iterdir()) == ["A001.mp4"]


def test_hw_decode_failure_retries_in_software(clip, fake_probe, monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append("-hwaccel" in cmd)
        rc = 1 if "-hwaccel" in cmd else 0
        with open(cmd[-1], "wb") as f:
            f.write(b"" if rc else b"prores")
        return subprocess.CompletedProcess(cmd, rc, "", "")

    monkeypatch.setattr(ep.subprocess, "run", run)
    assert ep.generate(str(clip), hwaccel=True)["status"] == ep.CREATED
    assert seen == [True, False]


def test_no_video_stream_fails_without_touching_the_folder(clip, monkeypatch):
    import ingest
    monkeypatch.setattr(ingest, "probe", lambda path: None)
    calls = _fake_ffmpeg(monkeypatch)
    assert ep.generate(str(clip))["status"] == ep.FAILED
    assert calls == [] and not (clip.parent / "Proxy").exists()


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_real_ffmpeg_keeps_tc_and_every_audio_track(tmp_path, monkeypatch):
    import json
    import config
    monkeypatch.setattr(config, "FFMPEG_PATH", shutil.which("ffmpeg"))
    monkeypatch.setattr(config, "FFPROBE_PATH", shutil.which("ffprobe"))
    src = tmp_path / "cam" / "C003.mov"
    src.parent.mkdir()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
         "-f", "lavfi", "-i", "sine=f=440", "-f", "lavfi", "-i", "sine=f=880", "-t", "1",
         "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-c:a", "aac",
         "-timecode", "10:20:30:12", str(src)],
        check=True,
    )
    res = ep.generate(str(src), hwaccel=False)
    assert res["status"] == ep.CREATED
    out = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", res["path"]],
        capture_output=True, text=True, encoding="utf-8", check=True).stdout)["streams"]
    video = [s for s in out if s["codec_type"] == "video"]
    assert video[0]["codec_name"] == "prores" and video[0]["profile"] == "Proxy"
    assert video[0]["height"] == 360                                 # not upscaled
    assert sum(1 for s in out if s["codec_type"] == "audio") == 2
    assert any((s.get("tags") or {}).get("timecode") == "10:20:30:12" for s in out)


# ── API ──────────────────────────────────────────────────────────────────────

def test_status_for_unknown_media_is_404(fastapi_client):
    assert fastapi_client.get("/api/proxy/editor/999999").status_code == 404


def test_build_rejects_empty_ids(fastapi_client):
    r = fastapi_client.post("/api/proxy/editor", json={"ids": []},
                            headers={"Origin": "http://testserver"})
    assert r.status_code == 422


def test_build_is_single_flight(fastapi_client):
    import state
    assert state.editor_proxy_build.acquire()
    try:
        r = fastapi_client.post("/api/proxy/editor", json={"ids": [1]},
                                headers={"Origin": "http://testserver"})
        assert r.status_code == 409
    finally:
        state.editor_proxy_build.release()


def test_editor_guard_is_not_the_browser_proxy_guard():
    import routers.proxy as rp
    import state
    assert rp._editor_guard is state.editor_proxy_build
    assert rp._editor_guard is not rp._proxy_guard
