"""Timecode correctness of the NLE exports (EDL / FCPXML / camera report).

Audit 2026-10-09 (N1). Four separate ways the exported timecode disagreed with
the camera's own timecode track:

1. A start TC label counts frames at the NOMINAL rate (23.976 → 24 per TC
   second). `_start_tc_seconds` divided the label into wall seconds and the
   exporter multiplied back by 23.976, so 01:00:00:00 came out as 00:59:56:10 —
   86 frames before the first frame of the clip.
2. Every 29.97 / 59.94 clip was exported as DROP FRAME, even when the camera's
   own TC is non-drop (`01:00:00:00`, colon) — 108 frames per hour off.
3. The single-clip FCPXML anchored the asset at 0s but started the clip at the
   camera TC, i.e. outside the asset's range (the timeline variant was fixed for
   exactly this; the single-clip one was not).
4. camera_report's DF frames→label conversion was wrong on almost every frame.

Plus: FCPXML `src` file URLs were not percent-encoded, so a space or `#` in a
path broke the media link.
"""
import importlib
import re
import urllib.parse
import xml.dom.minidom as minidom

import pytest


def _ref_df_label(frames, nominal):
    """Textbook SMPTE drop-frame frames→label, independent of the code under test."""
    d = 2 if nominal == 30 else 4
    per_10 = nominal * 600 - 9 * d
    per_1 = nominal * 60 - d
    tens, rem = divmod(frames, per_10)
    if rem > d:
        frames += 9 * d * tens + d * ((rem - d) // per_1)
    else:
        frames += 9 * d * tens
    ff = frames % nominal
    ss = (frames // nominal) % 60
    mm = (frames // (nominal * 60)) % 60
    hh = frames // (nominal * 3600)
    return "%02d:%02d:%02d;%02d" % (hh, mm, ss, ff)


def _sec(rational):
    m = re.match(r"(\d+)/(\d+)s", rational)
    return int(m.group(1)) / int(m.group(2)) if m else float(rational.rstrip("s"))


# ── 1. start TC is a frame count at the nominal rate ─────────────────────────

@pytest.mark.parametrize("fps,tc", [
    (23.976, "01:00:00:00"),
    (23.976, "10:00:00:00"),
    (23.976, "13:27:41:17"),
    (24.0, "01:00:00:00"),
    (25.0, "07:12:13:24"),
    (29.97, "01:00:00:00"),   # NDF 29.97
    (29.97, "01:00:00;00"),   # DF 29.97
    (29.97, "00:01:00;02"),   # first label after a DF skip
    (29.97, "05:37:12;19"),
    (59.94, "02:10:00;04"),
    (59.94, "02:10:00:04"),
    (50.0, "00:00:59:49"),
])
def test_start_tc_round_trips_to_the_same_label(fps, tc):
    eb = importlib.import_module("export_builders")
    rec = {"start_tc": tc}
    drop = eb._tc_is_drop(rec, fps)
    assert drop == (";" in tc)
    out = eb._edl_timecode(eb._start_tc_seconds(rec, fps), fps, drop)
    assert out == tc


def test_23976_clip_single_edl_source_in_is_camera_tc(fastapi_client, sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/a.mov", filename="a.mov", duration_s=10.0,
                            fps=23.976, start_tc="01:00:00:00"))
    body = fastapi_client.get("/api/media/1/export/edl").text
    line = next(ln for ln in body.splitlines() if ln.startswith("001  "))
    # source in == camera TC; source out == +10s at 24 nominal frames/s (240 frames)
    assert " 01:00:00:00 01:00:10:00 " in line, line


def test_23976_timeline_edl_and_fcpxml_use_camera_tc(fastapi_client, sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/a.mov", filename="a.mov", duration_s=10.0,
                            fps=23.976, start_tc="10:00:00:00"))
    edl = fastapi_client.get("/api/export/timeline/edl", params={"ids": "1"}).text
    line = next(ln for ln in edl.splitlines() if ln.startswith("001  "))
    assert " 10:00:00:00 10:00:10:00 " in line, line

    fcp = fastapi_client.get("/api/export/timeline/fcpxml", params={"ids": "1"}).text
    clip = minidom.parseString(fcp).getElementsByTagName("asset-clip")[0]
    # 10h of TC at 24 nominal frames/s = 864000 frames, each 1001/24000 s
    assert clip.getAttribute("start") == "{0}/24000s".format(864000 * 1001)


# ── 2. DF vs NDF follows the camera's own TC, not just the rate ──────────────

def test_2997_ndf_camera_tc_exports_non_drop(fastapi_client, sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/n.mp4", filename="n.mp4", duration_s=10.0,
                            fps=29.97, start_tc="01:00:00:00"))
    body = fastapi_client.get("/api/media/1/export/edl").text
    assert "FCM: NON-DROP FRAME" in body
    line = next(ln for ln in body.splitlines() if ln.startswith("001  "))
    assert " 01:00:00:00 " in line, line

    fcp = fastapi_client.get("/api/media/1/export/fcpxml").text
    assert 'tcFormat="NDF"' in fcp and 'tcFormat="DF"' not in fcp


def test_2997_df_camera_tc_still_exports_drop(fastapi_client, sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/d.mp4", filename="d.mp4", duration_s=10.0,
                            fps=29.97, start_tc="01:00:00;00"))
    body = fastapi_client.get("/api/media/1/export/edl").text
    assert "FCM: DROP FRAME" in body
    line = next(ln for ln in body.splitlines() if ln.startswith("001  "))
    assert " 01:00:00;00 01:00:10;00 " in line, line


def test_2997_without_camera_tc_keeps_drop_convention(fastapi_client, sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/x.mp4", filename="x.mp4", duration_s=10.0, fps=29.97))
    assert "FCM: DROP FRAME" in fastapi_client.get("/api/media/1/export/edl").text


# ── 3. single-clip FCPXML: clip start must sit inside the asset ──────────────

def test_single_fcpxml_clip_start_within_asset_range(fastapi_client, sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/tc.mp4", filename="tc.mp4", duration_s=30.0,
                            fps=30.0, start_tc="01:00:00:00"))
    body = fastapi_client.get("/api/media/1/export/fcpxml", params={"in_s": 10, "out_s": 20}).text
    dom = minidom.parseString(body)
    asset = dom.getElementsByTagName("asset")[0]
    clip = dom.getElementsByTagName("asset-clip")[0]
    a_start, a_dur = _sec(asset.getAttribute("start")), _sec(asset.getAttribute("duration"))
    c_start, c_dur = _sec(clip.getAttribute("start")), _sec(clip.getAttribute("duration"))
    assert a_start == 3600.0
    assert a_dur == 30.0
    assert c_start == 3610.0 and c_dur == 10.0
    assert a_start <= c_start and c_start + c_dur <= a_start + a_dur


# ── 4. FCPXML src must be a real (percent-encoded) file URL ──────────────────

@pytest.mark.parametrize("endpoint", ["/api/media/1/export/fcpxml", "/api/export/timeline/fcpxml?ids=1"])
def test_fcpxml_src_is_percent_encoded(fastapi_client, sample_record, endpoint):
    db = importlib.import_module("db")
    raw = "/Volumes/My Passport/day 1/take #2 100%.mov"
    db.upsert(sample_record(path=raw, filename="take #2 100%.mov", duration_s=5.0, fps=25.0))
    body = fastapi_client.get(endpoint).text
    src = minidom.parseString(body).getElementsByTagName("asset")[0].getAttribute("src")
    parsed = urllib.parse.urlparse(src)
    assert parsed.scheme == "file"
    assert parsed.fragment == ""
    assert " " not in src
    assert urllib.parse.unquote(parsed.path) == raw


def test_fcpxml_src_keeps_windows_drive_letter():
    rex = importlib.import_module("routers.export")
    url = rex._file_url("C:\\Users\\me\\My Clips\\a b.mov")
    assert url == "file:///C:/Users/me/My%20Clips/a%20b.mov"


# ── 5. camera report DF labels ────────────────────────────────────────────────

def test_camera_report_df_label_matches_reference_every_frame():
    cr = importlib.import_module("camera_report")
    for frames in list(range(0, 20000)) + [107892, 107892 + 17982, 2589407]:
        assert cr._frames_to_timecode(frames, 29.97, True) == _ref_df_label(frames, 30), frames
    for frames in (0, 3, 4, 3596, 3600, 35964, 215784):
        assert cr._frames_to_timecode(frames, 59.94, True) == _ref_df_label(frames, 60), frames


@pytest.mark.parametrize("start,expected", [
    ("01:00:00;00", "01:00:10;00"),  # DF camera TC → DF TC out
    ("01:00:00:00", "01:00:10:00"),  # NDF camera TC → NDF TC out
])
def test_camera_report_tc_out_follows_camera_tc_by_default(start, expected):
    cr = importlib.import_module("camera_report")
    assert cr._format_timecode_out(start, 300 / (30000 / 1001), 29.97, "auto") == expected
    assert cr.build_parser().parse_args(["--date", "2026-01-01"]).tc_format == "auto"


def test_camera_report_explicit_tc_format_still_honoured():
    cr = importlib.import_module("camera_report")
    # 01:00:00;00 is frame 107892; +300 frames = 108192 → NDF label 01:00:06:12
    assert cr._format_timecode_out("01:00:00;00", 300 / (30000 / 1001), 29.97, "ndf") == "01:00:06:12"
    assert cr._format_timecode_out("01:00:00:00", 300 / (30000 / 1001), 29.97, "df") == _ref_df_label(108300, 30)
