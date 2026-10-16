"""B13 (high-fps): EDL export flags rates outside the CMX3600 set.

CMX3600 only knows 23.976 / 24 / 25 / 29.97 / 30. A 50 / 59.94 / 119.88 clip
still exports (Resolve conforms it when the timeline matches), but the EDL must
carry a comment telling the editor to fall back to FCPXML if conform drifts.
Non-blocking by design: a 422 would break the working 59.94 path that
test_export_timecode_correctness covers.
"""
import importlib

import pytest


@pytest.mark.parametrize("fps", [23.976, 23.98, 24, 25, 29.97, 30, "30", None, 0, "x"])
def test_spec_rates_and_garbage_give_no_warning(fps):
    import export_builders as eb
    assert eb._edl_high_fps_warning(fps) is None


@pytest.mark.parametrize("fps", [50, 59.94, 60, 119.88])
def test_off_spec_rates_warn_and_point_to_fcpxml(fps):
    import export_builders as eb
    warn = eb._edl_high_fps_warning(fps)
    assert warn and warn.startswith("* ")  # EDL comment line → parsers ignore it
    assert "%g" % fps in warn and "FCPXML" in warn
    assert "\n" not in warn


def _ids(db, *paths):
    # Look ids up by path: a fresh project may already hold the bundled sample
    # clips, so "the first upsert is id 1" only holds by test-order luck.
    by_path = {r["path"]: r["id"] for r in db.get_all_records()}
    return [by_path[p] for p in paths]


def _warn_lines(body):
    return [ln for ln in body.splitlines() if "outside the CMX3600" in ln]


def test_single_clip_edl_carries_warning_only_for_high_fps(fastapi_client, sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/h.mov", filename="h.mov", duration_s=10.0,
                            fps=59.94, start_tc="01:00:00:00"))
    db.upsert(sample_record(path="/tmp/n.mov", filename="n.mov", duration_s=10.0,
                            fps=25.0, start_tc="01:00:00:00"))
    hi_id, lo_id = _ids(db, "/tmp/h.mov", "/tmp/n.mov")
    hi = fastapi_client.get("/api/media/%d/export/edl" % hi_id)
    assert hi.status_code == 200  # warn, don't block
    assert len(_warn_lines(hi.text)) == 1
    # the comment sits in the header, before the first event
    lines = hi.text.splitlines()
    assert lines.index(_warn_lines(hi.text)[0]) < next(
        i for i, ln in enumerate(lines) if ln.startswith("001  "))
    assert _warn_lines(fastapi_client.get("/api/media/%d/export/edl" % lo_id).text) == []


def test_timeline_edl_warns_once_per_off_spec_rate(fastapi_client, sample_record):
    db = importlib.import_module("db")
    for i, fps in enumerate((59.94, 59.94, 25.0, 119.88)):
        db.upsert(sample_record(path="/tmp/t%d.mov" % i, filename="t%d.mov" % i,
                                duration_s=5.0, fps=fps, start_tc="01:00:00:00"))
    ids = _ids(db, *("/tmp/t%d.mov" % i for i in range(4)))
    body = fastapi_client.get("/api/export/timeline/edl",
                              params={"ids": ",".join(map(str, ids))}).text
    warns = _warn_lines(body)
    assert len(warns) == 2, warns
    assert "59.94" in warns[0] and "119.88" in warns[1]
    assert sum(1 for ln in body.splitlines() if ln[:3].isdigit()) == 4
    # all-spec timeline stays warning-free
    assert _warn_lines(fastapi_client.get("/api/export/timeline/edl",
                                          params={"ids": str(ids[2])}).text) == []
