"""Phase 9.7 G6 — structured query builder (pure compile + API)."""
import importlib

import pytest

import query_builder as qb


# ---- pure compile_spec unit tests ----

def test_text_contains_and_eq():
    c = qb.compile_spec({"conditions": [
        {"field": "camera", "op": "contains", "value": "FX30"},
    ]})
    assert c["where"] == "camera_model LIKE ?"
    assert c["params"] == ["%FX30%"]

    c = qb.compile_spec({"conditions": [
        {"field": "camera", "op": "eq", "value": "ILME-FX30"},
    ]})
    assert c["where"] == "camera_model = ?"
    assert c["params"] == ["ILME-FX30"]


def test_rating_unrated_is_null():
    c = qb.compile_spec({"conditions": [
        {"field": "rating", "op": "eq", "value": "unrated"},
    ]})
    assert c["where"] == "rating IS NULL"
    assert c["params"] == []


def test_tag_subquery():
    c = qb.compile_spec({"conditions": [
        {"field": "tag", "op": "contains", "value": "貓"},
    ]})
    assert "SELECT media_id FROM tags WHERE name LIKE ?" in c["where"]
    assert c["params"] == ["%貓%"]


def test_numeric_range_both_bounds():
    c = qb.compile_spec({"conditions": [
        {"field": "duration", "op": "range", "value": [10, 120]},
    ]})
    assert c["where"] == "(duration_s >= ? AND duration_s <= ?)"
    assert c["params"] == [10, 120]


def test_numeric_range_open_upper():
    c = qb.compile_spec({"conditions": [
        {"field": "duration", "op": "range", "value": [10, None]},
    ]})
    assert c["where"] == "(duration_s >= ?)"
    assert c["params"] == [10]


def test_date_range_upper_bound_is_day_inclusive():
    # fable-audit round-5 #10: processed_at is a full timestamp, so the end-day bound
    # must compare against the next day exclusively (not <= the date-only string).
    c = qb.compile_spec({"conditions": [
        {"field": "date", "op": "range", "value": ["2026-05-01", "2026-05-03"]},
    ]})
    assert c["where"] == "(processed_at >= ? AND processed_at < date(?, '+1 day'))"
    assert c["params"] == ["2026-05-01", "2026-05-03"]


def test_media_type_bucket():
    c = qb.compile_spec({"conditions": [
        {"field": "media_type", "op": "eq", "value": "video"},
    ]})
    assert c["where"].startswith("LOWER(ext) IN (")
    assert ".mp4" in c["params"]


def test_match_all_vs_any_joiner():
    spec = {"match": "any", "conditions": [
        {"field": "camera", "op": "contains", "value": "FX30"},
        {"field": "lang", "op": "eq", "value": "zh"},
    ]}
    c = qb.compile_spec(spec)
    assert " OR " in c["where"]
    c2 = qb.compile_spec({**spec, "match": "all"})
    assert " AND " in c2["where"]


def test_semantic_term_is_separated_not_sql():
    c = qb.compile_spec({"conditions": [
        {"field": "semantic", "op": "contains", "value": "海邊日落"},
        {"field": "lang", "op": "eq", "value": "zh"},
    ]})
    assert c["semantic_terms"] == ["海邊日落"]
    assert c["where"] == "lang = ?"


def test_unknown_field_and_bad_op_rejected():
    with pytest.raises(qb.QueryError):
        qb.compile_spec({"conditions": [{"field": "nope", "op": "eq", "value": 1}]})
    with pytest.raises(qb.QueryError):
        qb.compile_spec({"conditions": [{"field": "duration", "op": "contains", "value": 1}]})


def test_empty_conditions_rejected():
    with pytest.raises(qb.QueryError):
        qb.compile_spec({"conditions": []})


# ---- API tests (SQL legs; semantic leg degrades cleanly without Ollama) ----

def _seed(db):
    db.upsert({
        "path": "/tmp/a.mp4", "filename": "a.mp4", "ext": ".mp4",
        "duration_s": 15.0, "size_mb": 5.0, "width": 1920, "height": 1080,
        "fps": 30.0, "has_audio": 1, "transcript": "海邊的咖啡廳", "lang": "zh",
        "frame_tags": "", "thumbnail_path": "/tmp/a.jpg",
        "processed_at": "2026-05-01T09:00:00", "rating": "good",
        "camera_model": "ILME-FX30",
    })
    db.upsert({
        "path": "/tmp/b.mov", "filename": "b.mov", "ext": ".mov",
        "duration_s": 200.0, "size_mb": 80.0, "width": 1920, "height": 1080,
        "fps": 24.0, "has_audio": 1, "transcript": "棚內訪談", "lang": "zh",
        "frame_tags": "", "thumbnail_path": "/tmp/b.jpg",
        "processed_at": "2026-05-02T09:00:00", "rating": "ng",
        "camera_model": "Canon R5",
    })
    db.upsert({
        "path": "/tmp/c.mp3", "filename": "c.mp3", "ext": ".mp3",
        "duration_s": 60.0, "size_mb": 2.0, "width": 0, "height": 0,
        "fps": 0.0, "has_audio": 1, "transcript": "純音訊", "lang": "zh",
        "frame_tags": "", "thumbnail_path": "",
        "processed_at": "2026-05-03T09:00:00",
    })


def test_query_camera_eq(fastapi_client, server_module):
    db = importlib.import_module("db")
    _seed(db)
    r = fastapi_client.post("/api/search/query", json={
        "conditions": [{"field": "camera", "op": "eq", "value": "ILME-FX30"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["structured"] is True
    assert {it["filename"] for it in body["items"]} == {"a.mp4"}


def test_query_rating_and_duration_range_all(fastapi_client, server_module):
    db = importlib.import_module("db")
    _seed(db)
    r = fastapi_client.post("/api/search/query", json={
        "match": "all",
        "conditions": [
            {"field": "rating", "op": "eq", "value": "good"},
            {"field": "duration", "op": "range", "value": [10, 60]},
        ],
    })
    assert r.status_code == 200
    assert {it["filename"] for it in r.json()["items"]} == {"a.mp4"}


def test_query_media_type_audio(fastapi_client, server_module):
    db = importlib.import_module("db")
    _seed(db)
    r = fastapi_client.post("/api/search/query", json={
        "conditions": [{"field": "media_type", "op": "eq", "value": "audio"}],
    })
    assert {it["filename"] for it in r.json()["items"]} == {"c.mp3"}


def test_query_match_any_union(fastapi_client, server_module):
    db = importlib.import_module("db")
    _seed(db)
    r = fastapi_client.post("/api/search/query", json={
        "match": "any",
        "conditions": [
            {"field": "camera", "op": "eq", "value": "ILME-FX30"},
            {"field": "rating", "op": "eq", "value": "ng"},
        ],
    })
    assert {it["filename"] for it in r.json()["items"]} == {"a.mp4", "b.mov"}


def test_query_tag_condition(fastapi_client, server_module):
    db = importlib.import_module("db")
    _seed(db)
    # tag the FX30 clip (id 1)
    db.add_tag(1, "海景")
    r = fastapi_client.post("/api/search/query", json={
        "conditions": [{"field": "tag", "op": "eq", "value": "海景"}],
    })
    assert {it["filename"] for it in r.json()["items"]} == {"a.mp4"}


def test_query_invalid_spec_is_422(fastapi_client):
    r = fastapi_client.post("/api/search/query", json={
        "conditions": [{"field": "bogus", "op": "eq", "value": 1}],
    })
    assert r.status_code == 422


def test_date_range_includes_end_day(fastapi_client, server_module):
    """fable-audit round-5 #10: the clip processed at 2026-05-03T09:00 must be
    returned when the range END is '2026-05-03' — pre-fix `<= '2026-05-03'` dropped
    every clip processed after midnight on the end day."""
    db = importlib.import_module("db")
    _seed(db)
    r = fastapi_client.post("/api/search/query", json={
        "conditions": [{"field": "date", "op": "range", "value": ["2026-05-01", "2026-05-03"]}],
    })
    assert r.status_code == 200, r.text
    assert {it["filename"] for it in r.json()["items"]} == {"a.mp4", "b.mov", "c.mp3"}


def test_date_range_single_day_not_empty(fastapi_client, server_module):
    """A single-day range [d, d] must return that day's clips, not nothing."""
    db = importlib.import_module("db")
    _seed(db)
    r = fastapi_client.post("/api/search/query", json={
        "conditions": [{"field": "date", "op": "range", "value": ["2026-05-03", "2026-05-03"]}],
    })
    assert {it["filename"] for it in r.json()["items"]} == {"c.mp3"}


def test_tag_eq_is_case_insensitive(fastapi_client, server_module):
    """fable-audit round-5 #11: add_tag stores lowercased, so an eq bind of a
    mixed-case tag must normalize to match (else 'Interview' finds nothing)."""
    db = importlib.import_module("db")
    _seed(db)
    db.add_tag(1, "Interview")  # stored as 'interview'
    r = fastapi_client.post("/api/search/query", json={
        "conditions": [{"field": "tag", "op": "eq", "value": "Interview"}],
    })
    assert {it["filename"] for it in r.json()["items"]} == {"a.mp4"}


def test_sort_map_entries_have_unique_tiebreaker():
    """fable-audit round-5 #12: every sort ends with an id tiebreaker so LIMIT/OFFSET
    pagination is a total order (no repeat/drop across pages)."""
    db = importlib.import_module("db")
    for key, val in db.SORT_MAP.items():
        tail = val.rstrip()
        assert tail.endswith("id DESC") or tail.endswith("id ASC"), (key, val)
