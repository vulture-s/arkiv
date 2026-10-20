"""A7 camera motion: synthetic controls, scene alignment, storage, and the scenes
contract.

Controls are built with ffmpeg from ONE still (a mandelbrot frame — rich,
non-periodic texture) so every clip has identical content and differs only in
what the virtual camera does: a fixed crop (static), a crop sliding right/left
(pan), a zoompan push-in / pull-out, and a crop jumping randomly (handheld
shake). A detector that reads content instead of motion cannot pass all six.
"""
import importlib
import json
import shutil
import subprocess

import pytest

import camera_motion as cm

_HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")

# virtual-camera filter per control; 6 s at 25 fps, 640x360 out of a 1600x600 still
_CONTROLS = {
    "static": "crop=640:360:400:120",
    # crop window slides RIGHT = camera pans right = content moves left
    "pan_right": "crop=640:360:x='100+t*80':y=120",
    "pan_left": "crop=640:360:x='700-t*80':y=120",
    "zoom_in": "zoompan=z='1+0.006*on':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=640x360:fps=25",
    "zoom_out": "zoompan=z='1.6-0.006*on':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=640x360:fps=25",
    "shake": "crop=640:360:x='400+60*(random(1)-0.5)':y='120+20*(random(2)-0.5)'",
    # static for 3 s, then pans right — two scenes must read differently
    "static_then_pan": "crop=640:360:x='if(lt(t,3),400,400+(t-3)*80)':y=120",
}


def _ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y"] + list(args), check=True)


@pytest.fixture(scope="module")
def controls(tmp_path_factory):
    if not _HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    d = tmp_path_factory.mktemp("motion_controls")
    still = d / "still.png"
    _ff("-f", "lavfi", "-i", "mandelbrot=s=1600x600:start_scale=1.5",
        "-frames:v", "1", str(still))
    out = {}
    for name, vf in _CONTROLS.items():
        p = d / (name + ".mp4")
        _ff("-loop", "1", "-i", str(still), "-t", "6", "-r", "25", "-vf", vf,
            "-pix_fmt", "yuv420p", str(p))
        out[name] = str(p)
    return out


def _whole(path):
    st = cm.steps(path)
    m = cm.summarize([d for _, d, _ in st], [z for _, _, z in st])
    return m, cm.classify(m)


# ── synthetic controls: direction and kind ────────────────────────────────────

@needs_ffmpeg
def test_static_reads_no_motion(controls):
    m, c = _whole(controls["static"])
    assert m["pan"] < 0.5 and m["zoom"] < 0.5 and m["jitter"] < 0.5
    assert c == {"pan_dir": None, "zoom_dir": None}


@needs_ffmpeg
@pytest.mark.parametrize("name,direction,sign", [("pan_left", "left", 1), ("pan_right", "right", -1)])
def test_pan_direction(controls, name, direction, sign):
    m, c = _whole(controls[name])
    # 80 px/s in a 640-px frame = 20 px/s in the 160-px projection space
    assert sign * m["net"] == pytest.approx(20.0, abs=1.5)
    assert m["jitter"] < 1.0
    assert c["pan_dir"] == direction
    assert c["zoom_dir"] is None


@needs_ffmpeg
@pytest.mark.parametrize("name,direction", [("zoom_in", "in"), ("zoom_out", "out")])
def test_zoom_direction_and_no_false_pan(controls, name, direction):
    """The experiment's shift-then-scale search read a pure push-in as a 2.36 px/s
    pan (over PAN_MIN_NET). The joint search must keep a pure zoom off the pan axis."""
    m, c = _whole(controls[name])
    assert c["zoom_dir"] == direction
    assert abs(m["net"]) < 1.0
    assert c["pan_dir"] is None


@needs_ffmpeg
def test_shake_is_not_a_pan(controls):
    """Travels a lot (pan high) but keeps reversing (jitter high): shake, not pan."""
    m, c = _whole(controls["shake"])
    assert m["pan"] > cm.PAN_MIN_NET
    assert m["jitter"] > cm.PAN_MAX_JITTER
    assert c["pan_dir"] is None


@needs_ffmpeg
def test_per_scene_split_follows_scene_bounds(controls):
    frames = [{"timestamp_s": 0.0}, {"timestamp_s": 3.0}]
    first, second = cm.measure_scenes(controls["static_then_pan"], frames, 6.0)
    # the step straddling the cut can bleed a few px in; the label must not flip
    assert cm.classify(first)["pan_dir"] is None and abs(first["net"]) < cm.PAN_MIN_NET
    assert cm.classify(second)["pan_dir"] == "right"


# ── pure units ────────────────────────────────────────────────────────────────

def test_too_short_window_is_unmeasured_not_static():
    assert cm.summarize([0], [0.0]) is None
    assert cm.per_scene([(0.0, 5, 0.0)], [(0.0, 0.4)]) == [None]


def test_scene_bounds_match_scenes_leaf():
    frames = [{"timestamp_s": 0.0}, {"timestamp_s": 5.0}, {"timestamp_s": 12.0}]
    assert cm.scene_bounds(frames, 30.0) == [(0.0, 5.0), (5.0, 12.0), (12.0, 30.0)]


def test_from_db_shape_and_garbage():
    raw = cm.to_db({"pan": 8.1, "net": 8.0, "jitter": 1.7, "zoom": 0.2,
                    "znet": 0.1, "zjitter": 0.3, "steps": 10})
    out = cm.from_db(raw)
    assert list(out) == ["pan", "net", "jitter", "zoom", "znet", "zjitter", "pan_dir", "zoom_dir"]
    assert out["pan_dir"] == "left" and out["zoom_dir"] is None
    assert cm.from_db(None) is None
    assert cm.from_db("not json") is None
    assert cm.from_db(json.dumps({"pan": 1})) is None


def test_classify_thresholds_from_field_calibration():
    # field pair the thresholds were set on: clean pan vs. bigger-but-shaky
    assert cm.classify({"net": 8.14, "jitter": 1.77})["pan_dir"] == "left"
    assert cm.classify({"net": 11.65, "jitter": 11.12})["pan_dir"] is None
    # whip pan: large fake znet with untrustworthy zjitter is NOT a zoom
    assert cm.classify({"znet": -6.62, "zjitter": 15.83})["zoom_dir"] is None
    assert cm.classify({"znet": 4.0, "zjitter": 1.15})["zoom_dir"] == "in"


# ── storage + scenes surfaces ─────────────────────────────────────────────────

_MOTION = {"pan": 20.0, "net": -20.0, "jitter": 0.0, "zoom": 0.0, "znet": 0.0,
           "zjitter": 0.0, "steps": 9}


def _seed(db, sample_record):
    db.upsert(sample_record(path="/tmp/motion.mp4", duration_s=30.0))
    db.upsert_frame(media_id=1, frame_index=0, timestamp_s=0.0,
                    thumbnail_path="thumbnails/m_f0.jpg", description="a")
    db.upsert_frame(media_id=1, frame_index=1, timestamp_s=10.0, description="b")
    db.upsert_frame(media_id=1, frame_index=2, timestamp_s=20.0,
                    thumbnail_path="thumbnails/m_f2.jpg", description="c")
    db.set_frame_camera_motion(1, 0, cm.to_db(_MOTION))
    db.set_frame_camera_motion(1, 1, cm.to_db(_MOTION))
    # frame 2: not measured


def test_http_scenes_append_camera_motion_last_and_only_when_measured(fastapi_client, sample_record):
    db = importlib.import_module("db")
    _seed(db, sample_record)
    data = fastapi_client.get("/api/media/1/scenes").json()
    s0, s1, s2 = data["scenes"]
    assert list(s0)[-2:] == ["keyframe_url", "camera_motion"]
    assert s0["camera_motion"]["pan_dir"] == "right"
    # no thumbnail: camera_motion is still last, keyframe_url still absent
    assert list(s1)[-1] == "camera_motion" and "keyframe_url" not in s1
    # unmeasured: key absent (not null) — "not measured" is not "static"
    assert "camera_motion" not in s2 and list(s2)[-1] == "keyframe_url"


def test_mcp_scenes_carry_the_same_camera_motion(tmp_db, sample_record):
    db = importlib.import_module("db")
    _seed(db, sample_record)
    import scenes
    body = scenes._scenes_for_mcp(1, db.get_record_by_id(1), db.get_frames(1))
    s0 = body["scenes"][0]
    assert list(s0)[-2:] == ["keyframe_path", "camera_motion"]
    assert s0["camera_motion"] == cm.from_db(cm.to_db(_MOTION))
    assert "camera_motion" not in body["scenes"][2]


def test_vision_reupsert_does_not_clobber_motion(tmp_db, sample_record):
    db = importlib.import_module("db")
    _seed(db, sample_record)
    db.upsert_frame(media_id=1, frame_index=0, timestamp_s=0.0,
                    thumbnail_path="thumbnails/m_f0.jpg", description="re-described")
    f0 = db.get_frames(1)[0]
    assert f0["description"] == "re-described"
    assert f0["camera_motion"] == cm.to_db(_MOTION)


# ── backfill ──────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_refresh_motion_backfills_and_resumes_by_version(tmp_db, sample_record, controls):
    db = importlib.import_module("db")
    import ingest
    path = controls["static_then_pan"]
    db.upsert(sample_record(path=path, filename="static_then_pan.mp4", duration_s=6.0))
    mid = 1  # fresh tmp db
    db.upsert_frame(media_id=mid, frame_index=0, timestamp_s=0.0, description="x")
    db.upsert_frame(media_id=mid, frame_index=1, timestamp_s=3.0, description="y")

    class _A:
        limit = 0

    assert ingest._run_refresh_motion(_A()) == 1
    f0, f1 = db.get_frames(mid)
    assert cm.from_db(f0["camera_motion"])["pan_dir"] is None
    assert cm.from_db(f1["camera_motion"])["pan_dir"] == "right"
    assert f0["description"] == "x"  # vision untouched
    # resumable: a second run finds nothing to do
    assert ingest._run_refresh_motion(_A()) == 0
    # an older measurement version is re-measured, not skipped
    db.set_frame_camera_motion(mid, 0, json.dumps(dict(_MOTION, v=0)))
    assert ingest._run_refresh_motion(_A()) == 1
