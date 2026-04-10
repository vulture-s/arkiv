import importlib


def _insert_media(sample_record):
    db = importlib.import_module("db")
    db.upsert(
        sample_record(
            path="/tmp/interview-zh.mp4",
            filename="interview-zh.mp4",
            duration_s=90,
            transcript="第一行中文逐字稿\n第二行中文逐字稿",
            lang="zh",
        )
    )
    db.upsert(
        sample_record(
            path="/tmp/podcast-en.mp3",
            filename="podcast-en.mp3",
            ext=".mp3",
            duration_s=45,
            transcript="English transcript line one\nline two",
            lang="en",
        )
    )
    db.add_tag(1, "訪談")
    return db


def test_list_media_supports_empty_paginated_and_filtered_results(fastapi_client, sample_record):
    response = fastapi_client.get("/api/media")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "search": False}

    _insert_media(sample_record)

    response = fastapi_client.get("/api/media", params={"limit": 1, "offset": 1})
    data = response.json()
    assert response.status_code == 200
    assert data["total"] == 2
    assert len(data["items"]) == 1
    assert data["items"][0]["path"] == "/tmp/interview-zh.mp4"

    response = fastapi_client.get("/api/media", params={"lang": "zh"})
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["tags"] == [{"id": 1, "name": "訪談", "source": "manual"}]


def test_media_detail_returns_200_and_404(fastapi_client, sample_record):
    _insert_media(sample_record)
    response = fastapi_client.get("/api/media/1")
    assert response.status_code == 200
    data = response.json()
    assert data["path"] == "/tmp/interview-zh.mp4"
    assert data["frame_tags_parsed"][0]["keywords"] == "人物 訪談 場景1"
    assert data["tags"][0]["name"] == "訪談"
    assert data["frames"] == []

    missing = fastapi_client.get("/api/media/999")
    assert missing.status_code == 404


def test_rating_update_set_clear_and_missing_record(fastapi_client, sample_record):
    db = _insert_media(sample_record)

    response = fastapi_client.patch(
        "/api/media/1/rating",
        json={"rating": "review", "note": "需要再聽一次"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "rating": "review",
        "note": "需要再聽一次",
    }
    assert db.get_record_by_id(1)["rating"] == "review"

    response = fastapi_client.patch(
        "/api/media/1/rating",
        json={"rating": None, "note": None},
    )
    assert response.status_code == 200
    assert db.get_record_by_id(1)["rating"] is None

    missing = fastapi_client.patch("/api/media/999/rating", json={"rating": "good"})
    assert missing.status_code == 404


def test_tag_stats_and_tag_catalog_endpoints(fastapi_client, sample_record):
    _insert_media(sample_record)

    create = fastapi_client.post(
        "/api/media/1/tags",
        json={"name": "精選", "source": "manual"},
    )
    assert create.status_code == 200
    assert [tag["name"] for tag in create.json()["tags"]] == ["精選", "訪談"]

    listing = fastapi_client.get("/api/media/1/tags")
    assert listing.status_code == 200
    assert [tag["name"] for tag in listing.json()] == ["精選", "訪談"]

    delete = fastapi_client.delete("/api/media/1/tags/訪談")
    assert delete.status_code == 200
    assert [tag["name"] for tag in delete.json()["tags"]] == ["精選"]

    stats = fastapi_client.get("/api/stats")
    assert stats.status_code == 200
    stats_data = stats.json()
    assert stats_data["total"] == 2
    assert stats_data["langs"] == {"en": 1, "zh": 1}
    assert stats_data["top_tags"][0]["name"] == "精選"

    tags = fastapi_client.get("/api/tags")
    assert tags.status_code == 200
    assert tags.json()[0]["name"] == "精選"


def test_export_endpoints_cover_supported_formats_and_current_json_gap(fastapi_client, sample_record):
    _insert_media(sample_record)

    srt = fastapi_client.get("/api/media/1/export/srt")
    assert srt.status_code == 200
    assert "第一行中文逐字稿" in srt.text
    assert srt.headers["content-disposition"].endswith("interview-zh.srt\"")

    vtt = fastapi_client.get("/api/media/1/export/vtt")
    assert vtt.status_code == 200
    assert vtt.text.startswith("WEBVTT")

    txt = fastapi_client.get("/api/media/1/export/txt")
    assert txt.status_code == 200
    assert "第二行中文逐字稿" in txt.text

    json_resp = fastapi_client.get("/api/media/1/export/json")
    assert json_resp.status_code == 400
    assert "json" in json_resp.json()["detail"]
