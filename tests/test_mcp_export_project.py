"""export_project: the whole library's transcripts + scenes, page by page.

Real tmp DB (not mocks): the point of the tool is the cursor/budget arithmetic
over actual rows, and the filter path through search_syntax's SQL.
"""
import json

import pytest

pytest.importorskip("mcp")

import db
import mcp_server as m


@pytest.fixture(autouse=True)
def _ready(monkeypatch):
    monkeypatch.setattr(m, "_DB_READY", True)


def _clip(name, lang="zh", transcript="", segments=None, rating=None, frames=()):
    with db.get_conn() as conn:
        mid = conn.execute(
            "INSERT INTO media (path, filename, lang, transcript, segments_json, rating, duration_s) "
            "VALUES (?,?,?,?,?,?,?)",
            ("media-in/" + name, name, lang, transcript,
             json.dumps(segments) if segments is not None else None, rating, 10.0),
        ).lastrowid
        for i, (ts, desc) in enumerate(frames):
            conn.execute(
                "INSERT INTO frames (media_id, frame_index, timestamp_s, description) VALUES (?,?,?,?)",
                (mid, i, ts, desc))
    return mid


def test_one_page_carries_segments_and_scenes(tmp_db):
    a = _clip("a.mp4", segments=[{"start": 0.0, "end": 2.5, "text": "大家好", "tokens": [1, 2]}],
              frames=[(0.0, "店門口"), (4.0, "吧台特寫")])
    out = m.export_project_impl()
    assert out["total_matching"] == 1 and out["next_cursor"] is None
    item = out["items"][0]
    assert item["id"] == a and item["path"] == "media-in/a.mp4"
    assert item["segments"] == [{"start": 0.0, "end": 2.5, "text": "大家好"}]  # decoder internals dropped
    assert [(s["start_s"], s["end_s"], s["description"]) for s in item["scenes"]] == [
        (0.0, 4.0, "店門口"), (4.0, 10.0, "吧台特寫")]


def test_clip_without_segment_timing_falls_back_to_flat_text(tmp_db):
    _clip("old.mp4", transcript="沒有時間碼的舊逐字稿")
    item = m.export_project_impl()["items"][0]
    assert "segments" not in item and item["transcript"] == "沒有時間碼的舊逐字稿"


def test_cursor_walks_every_clip_exactly_once(tmp_db):
    ids = [_clip("c{0}.mp4".format(i), transcript="x") for i in range(7)]
    seen, cursor = [], 0
    for _ in range(10):
        page = m.export_project_impl(cursor=cursor, limit=3)
        seen += [it["id"] for it in page["items"]]
        if page["next_cursor"] is None:
            break
        cursor = page["next_cursor"]
    assert seen == ids


def test_char_budget_ends_the_page_early_but_never_empties_it(tmp_db):
    long_text = "很長" * 1500
    a = _clip("a.mp4", transcript=long_text)
    b = _clip("b.mp4", transcript=long_text)
    page = m.export_project_impl(max_chars=4000)
    assert [it["id"] for it in page["items"]] == [a]   # b would pass the budget
    assert page["next_cursor"] == a and page["oversized"] is False
    tiny = m.export_project_impl(max_chars=1000)        # one clip alone is over budget
    assert [it["id"] for it in tiny["items"]] == [a] and tiny["oversized"] is True
    assert m.export_project_impl(cursor=a, max_chars=4000)["items"][0]["id"] == b


def test_filters_use_the_search_box_syntax(tmp_db):
    _clip("zh.mp4", lang="zh", transcript="中文")
    en = _clip("en.mp4", lang="en", transcript="english")
    out = m.export_project_impl(filters="lang:en")
    assert out["total_matching"] == 1 and [it["id"] for it in out["items"]] == [en]
    assert out["filters"] == [{"field": "lang", "value": "en"}]


def test_free_text_is_rejected_not_silently_dropped(tmp_db):
    _clip("a.mp4", transcript="x")
    with pytest.raises(ValueError, match="search_media"):
        m.export_project_impl(filters="咖啡 lang:zh")


def test_toggles_drop_either_half(tmp_db):
    _clip("a.mp4", segments=[{"start": 0, "end": 1, "text": "t"}], frames=[(0.0, "d")])
    item = m.export_project_impl(include_transcript=False, include_scenes=False)["items"][0]
    assert "segments" not in item and "transcript" not in item and "scenes" not in item


def test_tool_wrapper_returns_json_and_maps_bad_filters(tmp_db):
    _clip("a.mp4", transcript="x")
    assert json.loads(m.export_project())["returned"] == 1
    with pytest.raises(ValueError, match="export_project filter"):
        m.export_project(filters="rating:maybe")
