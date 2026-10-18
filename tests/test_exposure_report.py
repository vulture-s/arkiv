"""Brand / object exposure report (exposure_report.py) — core, CLI, REST, MCP.

All data is synthetic, seeded into the tmp_db fixture. Scene boundaries come from
scenes._build_scenes, so expected seconds below are "next keyframe start − this
keyframe start" (the last scene ends at media.duration_s).
"""
import json

import pytest

import db
import exposure_report as er
import zh_convert


def _clip(name, duration_s, frames, camera_model=None, shot_date=None, ext=".mp4"):
    """frames: [(timestamp_s, description, tags_str)]"""
    db.upsert({
        "path": "/tmp/expo/{0}".format(name), "filename": name, "ext": ext,
        "duration_s": duration_s, "size_mb": 1.0, "width": 1920, "height": 1080,
        "fps": 30.0, "has_audio": 1, "transcript": "", "lang": "zh",
        "frame_tags": "", "thumbnail_path": "",
        "processed_at": "2026-10-01T09:00:00",
        "camera_make": "Sony" if camera_model else None,
        "camera_model": camera_model,
    })
    with db.get_conn() as conn:
        mid = conn.execute("SELECT id FROM media WHERE filename=?", (name,)).fetchone()[0]
        if shot_date:
            conn.execute("UPDATE media SET shot_date=? WHERE id=?", (shot_date, mid))
    for i, (ts, desc, tags) in enumerate(frames):
        db.upsert_frame(media_id=mid, frame_index=i, timestamp_s=ts,
                        description=desc, tags=tags, content_type="B-Roll")
    return mid


@pytest.fixture
def library(tmp_db):
    ids = {}
    # a: scenes 0–4 (hit), 4–10 (miss), 10–12 (hit via tag) → 6 s, 2 scenes
    ids["a"] = _clip("a.mp4", 12.0, [
        (0.0, "桌上放著古河電源線", "電源線,桌面"),
        (4.0, "人物在客廳說話", "人物"),
        (10.0, "特寫插頭", "FURUTECH,插頭"),
    ], camera_model="FX3", shot_date="2026-10-03")
    # b: Simplified-stored description, last scene → ends at duration (20 s)
    ids["b"] = _clip("b.mp4", 20.0, [
        (0.0, "街景", "城市"),
        (5.0, "古河电源线的包装盒", ""),
    ], camera_model="A7S3", shot_date="2026-10-05")
    # c: analysed, no hit
    ids["c"] = _clip("c.mp4", 8.0, [(0.0, "海邊日落", "海,夕陽")],
                     camera_model="FX3", shot_date="2026-09-20")
    # d: frames exist but never analysed → unanalysed, not "absent"
    ids["d"] = _clip("d.mp4", 9.0, [(0.0, "", "")], shot_date="2026-10-04")
    # e: audio → out of scope entirely
    ids["e"] = _clip("e.wav", 60.0, [], ext=".wav")
    return ids


def test_totals_and_scene_cut(library):
    r = er.build_exposure_report(["古河", "furutech"])
    assert r["clips_in_scope"] == 4          # audio excluded
    assert r["clips_unanalysed"] == 1        # d
    assert r["clips_hit"] == 2
    assert r["scenes_hit"] == 3
    # a: 4 + 2 ; b: 20 - 5
    assert r["exposure_s"] == pytest.approx(21.0)
    by = {c["filename"]: c for c in r["clips"]}
    assert by["a.mp4"]["exposure_s"] == pytest.approx(6.0)
    assert [(s["start_s"], s["end_s"]) for s in by["a.mp4"]["scenes"]] == [(0.0, 4.0), (10.0, 12.0)]
    assert by["b.mp4"]["scenes"][0]["end_s"] == 20.0   # last scene ends at duration
    assert by["a.mp4"]["scenes"][1]["matched_terms"] == ["furutech"]


def test_by_term_counts_each_term(library):
    r = er.build_exposure_report(["古河", "FURUTECH"])
    assert r["by_term"]["古河"] == {"clips": 2, "scenes": 2, "exposure_s": pytest.approx(19.0)}
    assert r["by_term"]["FURUTECH"] == {"clips": 1, "scenes": 1, "exposure_s": pytest.approx(2.0)}


def test_case_and_width_insensitive(library):
    r = er.build_exposure_report(["ＦＵＲＵＴＥＣＨ"])  # full-width
    assert r["clips_hit"] == 1 and r["scenes_hit"] == 1


@pytest.mark.skipif(not zh_convert.opencc_available(), reason="needs opencc")
def test_traditional_term_matches_simplified_description(library):
    r = er.build_exposure_report(["電源線"])
    names = sorted(c["filename"] for c in r["clips"])
    assert names == ["a.mp4", "b.mp4"]
    assert r["method"]["script_fold"] == "opencc"


def test_filters_scope(library):
    r = er.build_exposure_report(["古河"], filters="camera:FX3")
    assert [c["filename"] for c in r["clips"]] == ["a.mp4"]
    r = er.build_exposure_report(["古河"], filters="shot:2026-10-05")
    assert [c["filename"] for c in r["clips"]] == ["b.mp4"]
    assert r["clips_in_scope"] == 1


def test_filters_who_scopes_to_annotated_clips(library):
    # A6: `who:` (manual person annotations) narrows the report like any filter.
    db.add_identity(library["b"], "小明", 5.0, 20.0)
    r = er.build_exposure_report(["古河"], filters="who:小明")
    assert [c["filename"] for c in r["clips"]] == ["b.mp4"]
    assert r["clips_in_scope"] == 1


def test_filters_reject_free_text_and_bad_values(library):
    with pytest.raises(er.ReportError):
        er.build_exposure_report(["古河"], filters="古河 camera:FX3")
    with pytest.raises(er.ReportError):
        er.build_exposure_report(["古河"], filters="rating:maybe")
    with pytest.raises(er.ReportError):
        er.build_exposure_report(["  "])
    with pytest.raises(er.ReportError):
        er.build_exposure_report(["古河"], group_by="lens")


def test_group_by_date_and_camera(library):
    r = er.build_exposure_report(["古河", "furutech"], group_by="camera")
    groups = {g["key"]: g for g in r["groups"]}
    assert groups["Sony FX3"]["exposure_s"] == pytest.approx(6.0)
    assert groups["Sony A7S3"]["clips"] == 1
    r = er.build_exposure_report(["古河"], group_by="date")
    assert [g["key"] for g in r["groups"]] == ["2026-10-03", "2026-10-05"]


def test_paging_keeps_totals(library):
    r = er.build_exposure_report(["古河"], limit=1, offset=1)
    assert r["clips_total"] == 2 and len(r["clips"]) == 1
    assert r["clips_hit"] == 2 and r["exposure_s"] == pytest.approx(19.0)


def test_no_absolute_path_leak(library, monkeypatch, tmp_path):
    r = er.build_exposure_report(["古河"])
    for c in r["clips"]:
        assert not c["path"].startswith("/"), c["path"]


def test_report_carries_method_disclaimer(library):
    r = er.build_exposure_report(["古河"])
    assert "非逐格偵測" in r["method"]["note"]
    md = er.render_markdown(r)
    assert "非逐格偵測" in md
    assert "a.mp4" in md and "0:00.0–0:04.0" in md


def test_legacy_json_list_tags(tmp_db):
    _clip("j.mp4", 10.0, [(0.0, "", json.dumps(["古河", "線材"], ensure_ascii=False))])
    r = er.build_exposure_report(["古河"])
    assert r["clips_hit"] == 1 and r["clips_unanalysed"] == 0


def test_cli_markdown_and_json(library, capsys, tmp_path):
    assert er.main(["古河", "--filters", "camera:FX3"]) == 0
    out = capsys.readouterr().out
    assert "# 曝光報告：古河" in out and "a.mp4" in out and "b.mp4" not in out
    dest = tmp_path / "r.json"
    assert er.main(["古河", "furutech", "--format", "json", "--group-by", "date",
                    "--output", str(dest)]) == 0
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["clips_hit"] == 2 and data["group_by"] == "date"
    assert er.main(["古河", "--filters", "free text"]) == 2


def test_rest_route(library, fastapi_client):
    r = fastapi_client.get("/api/report/exposure",
                           params=[("term", "古河"), ("term", "furutech"), ("group_by", "camera")])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["clips_hit"] == 2 and body["exposure_s"] == pytest.approx(21.0)
    assert fastapi_client.get("/api/report/exposure",
                              params={"term": "古河", "filters": "x y"}).status_code == 400
    assert fastapi_client.get("/api/report/exposure").status_code == 422


def test_mcp_tool(library):
    m = pytest.importorskip("mcp_server")
    out = json.loads(m.exposure_report(["古河"], filters="", group_by="date", limit=1))
    assert out["clips_hit"] == 2 and len(out["clips"]) == 1
    assert out["method"]["basis"] == "vision_description"
    with pytest.raises(ValueError):
        m.exposure_report(["古河"], filters="hello")
