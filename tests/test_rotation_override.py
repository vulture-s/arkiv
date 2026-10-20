"""Container rotation metadata can be wrong, and honouring it corrupts every
downstream artifact.

A camera writes the rotation flag from an orientation sensor. On a gimbal, or
pointed steeply up or down, that sensor misreads and the file ends up carrying
a 90/180° flag over footage that is already framed landscape. Honouring it
swaps the stored width/height, rotates the thumbnail, rotates the frames vision
reads, and rotates the proxy — and the vision captions come back reading
perfectly plausible for a sideways image, so nothing in the data says anything
is wrong.

Observed on a real shoot: 6 of 200 clips, 4 of them stored as 2160x3840.
"""
import importlib
import json
import config
import ingest
import frames as frm


def _reload(monkeypatch, value):
    monkeypatch.setenv("ARKIV_IGNORE_ROTATION", value)
    importlib.reload(config)
    importlib.reload(ingest)
    importlib.reload(frm)
    return config


def _stub_ffprobe(monkeypatch, mod, rotation):
    """probe() shells out to ffprobe; stub the subprocess, not a helper."""
    class _R:
        returncode = 0
        stdout = ""
        stderr = ""
    def fake_run(cmd, **kw):
        r = _R(); r.stdout = json.dumps(_probe_payload(rotation)); return r
    monkeypatch.setattr(mod.subprocess, "run", fake_run)


def _probe_payload(rotation):
    return {
        "streams": [{
            "codec_type": "video", "width": 3840, "height": 2160,
            "r_frame_rate": "60000/1001",
            "side_data_list": [{"rotation": rotation}],
        }],
        "format": {"duration": "10.0", "size": "1000000"},
    }


def test_rotation_is_honoured_by_default(monkeypatch, tmp_path):
    """Genuinely vertical footage exists and its flag is correct — the default
    must keep trusting it."""
    cfg = _reload(monkeypatch, "0")
    assert cfg.IGNORE_ROTATION is False
    _stub_ffprobe(monkeypatch, ingest, -90)
    f = tmp_path / "a.mp4"; f.write_bytes(b"x")
    got = ingest.probe(str(f))
    assert (got["width"], got["height"]) == (2160, 3840)


def test_rotation_can_be_ignored(monkeypatch, tmp_path):
    cfg = _reload(monkeypatch, "1")
    assert cfg.IGNORE_ROTATION is True
    _stub_ffprobe(monkeypatch, ingest, -90)
    f = tmp_path / "a.mp4"; f.write_bytes(b"x")
    got = ingest.probe(str(f))
    assert (got["width"], got["height"]) == (3840, 2160), \
        "ignoring rotation must leave the stored geometry alone"


def test_frame_extraction_passes_noautorotate_when_ignoring(monkeypatch, tmp_path):
    """-noautorotate is a demuxer option: after -i it is silently ignored and
    the frame still comes out rotated. It has to precede the input."""
    _reload(monkeypatch, "1")
    seen = {}
    def _cap(cmd, out_path=None, timeout=60):
        seen["cmd"] = cmd
        return False        # 讓 _extract_frame_to 走失敗路徑，不去 os.replace
    monkeypatch.setattr(frm, "_run_ffmpeg", _cap)
    src = tmp_path / "a.mp4"; src.write_bytes(b"x")
    frm._extract_frame_to(str(src), 1.0, tmp_path / "o.jpg")
    cmd = seen.get("cmd", [])
    assert "-noautorotate" in cmd, cmd
    assert cmd.index("-noautorotate") < cmd.index("-i"), \
        "-noautorotate after -i is silently ignored"


def test_frame_extraction_omits_noautorotate_by_default(monkeypatch, tmp_path):
    _reload(monkeypatch, "0")
    seen = {}
    def _cap(cmd, out_path=None, timeout=60):
        seen["cmd"] = cmd
        return False        # 讓 _extract_frame_to 走失敗路徑，不去 os.replace
    monkeypatch.setattr(frm, "_run_ffmpeg", _cap)
    src = tmp_path / "a.mp4"; src.write_bytes(b"x")
    frm._extract_frame_to(str(src), 1.0, tmp_path / "o.jpg")
    assert "-noautorotate" not in seen.get("cmd", []), seen.get("cmd")
