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


def _insert_media_with_segments(sample_record):
    import json as _json
    db = importlib.import_module("db")
    segments = [
        {"start": 0.0, "end": 10.0, "text": "前段台詞"},
        {"start": 10.0, "end": 20.0, "text": "中段台詞"},
        {"start": 20.0, "end": 30.0, "text": "後段台詞"},
    ]
    db.upsert(
        sample_record(
            path="/tmp/trim-zh.mp4",
            filename="trim-zh.mp4",
            duration_s=30.0,
            fps=30.0,
            start_tc="01:00:00:00",
            segments_json=_json.dumps(segments, ensure_ascii=False),
        )
    )
    return db


def test_srt_export_ignores_trim_when_range_covers_full_clip(fastapi_client, sample_record):
    _insert_media_with_segments(sample_record)

    baseline = fastapi_client.get("/api/media/1/export/srt").text
    widened = fastapi_client.get(
        "/api/media/1/export/srt", params={"in_s": 0, "out_s": 30}
    ).text
    assert baseline == widened, "full-range trim must be a no-op"
    assert "前段台詞" in baseline and "後段台詞" in baseline


def test_srt_export_filters_and_rebases_to_trim_window(fastapi_client, sample_record):
    _insert_media_with_segments(sample_record)

    resp = fastapi_client.get(
        "/api/media/1/export/srt", params={"in_s": 10, "out_s": 20}
    )
    assert resp.status_code == 200
    body = resp.text
    # Only the middle segment survives
    assert "中段台詞" in body
    assert "前段台詞" not in body
    assert "後段台詞" not in body
    # And it's rebased to start at 00:00:00,000
    assert "00:00:00,000 --> 00:00:10,000" in body


def test_txt_export_with_trim_but_no_segments_returns_empty(fastapi_client, sample_record):
    # record inserted without segments_json
    _insert_media(sample_record)
    resp = fastapi_client.get(
        "/api/media/1/export/txt", params={"in_s": 10, "out_s": 20}
    )
    assert resp.status_code == 200
    assert resp.text == ""


def test_edl_export_shifts_source_tc_by_trim_in(fastapi_client, sample_record):
    _insert_media_with_segments(sample_record)

    resp = fastapi_client.get(
        "/api/media/1/export/edl", params={"in_s": 10, "out_s": 20}
    )
    assert resp.status_code == 200
    body = resp.text
    # Record TC still begins at 01:00:00:00
    assert "01:00:00:00" in body
    # Source TC begins at 01:00:10:00 (camera TC 01:00:00:00 + trim_in 10s at 30fps)
    assert "01:00:10:00" in body
    # And ends 10s later (trim duration, not full 30s clip)
    assert "01:00:20:00" in body
    # Full-clip source end (01:00:30:00) must not appear
    assert "01:00:30:00" not in body


def test_fcpxml_export_keeps_asset_full_but_clip_uses_trim(fastapi_client, sample_record):
    _insert_media_with_segments(sample_record)

    resp = fastapi_client.get(
        "/api/media/1/export/fcpxml", params={"in_s": 10, "out_s": 20}
    )
    assert resp.status_code == 200
    body = resp.text
    # asset duration = full 30s * 30fps = 900 frames; num=1, den=30
    assert 'duration="900/30s"' in body  # asset
    # sequence + asset-clip duration = 10s * 30fps = 300 frames
    assert 'duration="300/30s"' in body
    # asset-clip start = (camera TC 3600s + trim_in 10s) * 30fps = 108300 frames
    assert 'start="108300/30s"' in body


def test_export_to_file_accepts_trim_params(tmp_path, fastapi_client, sample_record):
    _insert_media_with_segments(sample_record)
    dest = tmp_path / "trim.srt"
    resp = fastapi_client.post(
        "/api/media/1/export-to",
        json={"fmt": "srt", "dest": str(dest), "in_s": 10, "out_s": 20},
    )
    assert resp.status_code == 200
    content = dest.read_text(encoding="utf-8")
    assert "中段台詞" in content
    assert "前段台詞" not in content


def test_export_metadata_csv_returns_davinci_compatible_columns(fastapi_client, sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(
        path="/tmp/clip-a.mp4",
        filename="clip-a.mp4",
        transcript="這是 A 段訪談的開頭",
        frame_tags="室內訪談\n人物特寫",
        content_type="interview",
        atmosphere="warm",
        energy="calm",
        edit_position="b-roll",
    ))
    db.upsert(sample_record(
        path="/tmp/clip-b.mp4",
        filename="clip-b.mp4",
        transcript="",
        frame_tags="",
        content_type=None,
        atmosphere=None,
        energy=None,
    ))
    db.add_tag(1, "people")
    db.add_tag(1, "warm-tone")

    resp = fastapi_client.get("/api/export/metadata-csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "arkiv_davinci_metadata.csv" in resp.headers["content-disposition"]

    body = resp.content.decode("utf-8")
    lines = body.splitlines()
    # Header + 2 rows
    assert len(lines) == 3
    assert lines[0] == "File Name,Description,Keywords,Comments,Scene"

    # Row 1: filename match-key, vision frame_tags first line in Description,
    # tags + content_type joined by '; ' in Keywords, atmosphere/energy in Comments.
    import csv as _csv
    from io import StringIO
    rows = list(_csv.reader(StringIO(body)))
    assert rows[1][0] == "clip-a.mp4"
    assert rows[1][1] == "室內訪談"
    assert "people" in rows[1][2] and "warm-tone" in rows[1][2] and "interview" in rows[1][2]
    assert rows[1][2].count(";") == 2  # 3 keywords: 2 tags + content_type (dedup-safe)
    assert "atmosphere:warm" in rows[1][3] and "energy:calm" in rows[1][3]
    assert "edit:b-roll" in rows[1][3]
    # Scene flattens \n to space
    assert "\n" not in rows[1][4]
    assert "室內訪談" in rows[1][4] and "人物特寫" in rows[1][4]

    # Row 2: empty metadata produces empty cells but row still emitted.
    assert rows[2][0] == "clip-b.mp4"
    assert rows[2][1] == ""
    assert rows[2][2] == ""
    assert rows[2][3] == ""


def test_proxy_filename_is_scoped_by_source_path():
    # Regression: a proxies/ directory copied from another installation
    # must not be served as the local user's content. Filenames carry a
    # hash of the absolute source path so cross-installation collisions
    # are impossible.
    config = importlib.import_module("config")
    same_id_hevin = config.proxy_path_for(1, "/Users/hevin/clip.mov")
    same_id_pen = config.proxy_path_for(1, "/Users/pen/clip.mov")
    assert same_id_hevin != same_id_pen
    assert same_id_hevin.name.startswith("1_")
    assert same_id_pen.name.startswith("1_")
    assert same_id_hevin == config.proxy_path_for(1, "/Users/hevin/clip.mov")


def test_stream_ignores_legacy_proxy_from_another_install(
    fastapi_client, sample_record, tmp_path, monkeypatch
):
    # Simulates the Pen bug: the repo shipped with a proxies/ directory
    # from another machine. A file named "{media_id}.mp4" exists but was
    # never generated from the local user's source. The stream endpoint
    # must NOT return it.
    config = importlib.import_module("config")
    db = importlib.import_module("db")

    proxies_dir = tmp_path / "proxies"
    proxies_dir.mkdir()
    legacy_contamination = proxies_dir / "1.mp4"
    legacy_contamination.write_bytes(b"another-users-content-DO-NOT-SERVE")
    monkeypatch.setattr(config, "PROXIES_DIR", proxies_dir)

    db.upsert(sample_record(path="/tmp/local_user_own_file.mp4"))

    # Source file doesn't exist on disk; fall-through yields 404. Key
    # assertion: we did NOT serve the contamination file.
    resp = fastapi_client.get("/api/stream/1")
    assert resp.status_code == 404
    assert b"DO-NOT-SERVE" not in resp.content
    assert legacy_contamination.read_bytes() == b"another-users-content-DO-NOT-SERVE"
