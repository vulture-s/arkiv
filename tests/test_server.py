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


def test_export_metadata_csv_defuses_formula_injection(fastapi_client, sample_record):
    """Codex audit: filename / tags 開頭 =/+/-/@/TAB/CR 在 Excel 會當公式執行。"""
    db = importlib.import_module("db")
    db.upsert(sample_record(
        path="/tmp/=cmd_injection.mp4",
        filename="=cmd|'/c calc'!A1",  # canonical Excel injection payload
        transcript="-IMPORTXML(\"foo\")",
        frame_tags="@SUM(A1:A10)",
        content_type=None,
    ))
    db.add_tag(1, "+regular-tag")

    resp = fastapi_client.get("/api/export/metadata-csv")
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    import csv as _csv
    from io import StringIO
    rows = list(_csv.reader(StringIO(body)))
    # Each cell that started with =/+/-/@ must be prefixed by a single quote.
    assert rows[1][0].startswith("'=")  # filename
    assert rows[1][1].startswith("'@")  # description (frame_tags first line)
    assert rows[1][2].startswith("'+")  # keywords (only +regular-tag)
    # Sanity: no row cell still leads with bare =/+/-/@.
    for cell in rows[1]:
        assert not cell.startswith(("=", "+", "-", "@", "\t", "\r"))


def test_export_metadata_csv_dedups_content_type_case_insensitive(fastapi_client, sample_record):
    """Codex audit: vision 寫 'B-Roll'，tags 強制小寫 'b-roll'，naive dedup 會雙吐。"""
    db = importlib.import_module("db")
    db.upsert(sample_record(
        path="/tmp/clip-x.mp4",
        filename="clip-x.mp4",
        transcript="",
        frame_tags="",
        content_type="B-Roll",
    ))
    db.add_tag(1, "b-roll")  # db.py forces .lower()
    db.add_tag(1, "people")

    resp = fastapi_client.get("/api/export/metadata-csv")
    body = resp.content.decode("utf-8")
    import csv as _csv
    from io import StringIO
    rows = list(_csv.reader(StringIO(body)))
    keywords = rows[1][2]
    # b-roll appears once, not twice (case-insensitive dedup)
    assert keywords.lower().count("b-roll") == 1
    assert "people" in keywords


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


def test_export_metadata_csv_parses_vision_json_into_columns(fastapi_client, sample_record):
    """Audit Batch E F1 critical fix: frame_tags 是 JSON list，必須解出
    description / tags / content_type / atmosphere / energy / edit_position，不能
    把整段 JSON 當 plain text 塞到 Description 欄。"""
    import json as _json
    db = importlib.import_module("db")
    vision_payload = _json.dumps([
        {
            "description": "戶外體育課，孩童跳繩。",
            "tags": ["兒童", "戶外", "運動"],
            "content_type": "A-Roll",
            "atmosphere": "活潑",
            "energy": "高",
            "edit_position": "中段-互動",
        },
        {
            "description": "近景特寫。",
            "tags": ["特寫"],
        },
    ], ensure_ascii=False)
    db.upsert(sample_record(
        path="/tmp/clipA.mp4",
        filename="clipA.mp4",
        frame_tags=vision_payload,
        # Media-level columns NULL — exporter 必須回退到從 frame_tags 抽
        content_type=None, atmosphere=None, energy=None, edit_position=None,
    ))

    resp = fastapi_client.get("/api/export/metadata-csv")
    body = resp.content.decode("utf-8")
    import csv as _csv
    from io import StringIO
    rows = list(_csv.reader(StringIO(body)))
    # Header + 1 data row
    assert len(rows) == 2
    fname, desc, kw, comments, scene = rows[1]
    assert fname == "clipA.mp4"
    # Description = first frame description, NOT raw JSON
    assert desc == "戶外體育課，孩童跳繩。"
    assert "{" not in desc, "raw JSON must not leak into Description"
    # Keywords: vision tags + content_type, dedup case-insensitive
    assert "兒童" in kw and "戶外" in kw and "運動" in kw and "特寫" in kw
    assert "A-Roll" in kw
    # Comments: pulled from JSON since media-level cols are NULL
    assert "atmosphere:活潑" in comments
    assert "energy:高" in comments
    assert "edit:中段-互動" in comments
    # Scene: all frame descriptions joined by ' | '
    assert "戶外體育課，孩童跳繩。" in scene and "近景特寫。" in scene


def test_export_metadata_csv_legacy_plain_text_frame_tags_still_works(fastapi_client, sample_record):
    """Old DB rows with non-JSON frame_tags shouldn't blow up."""
    db = importlib.import_module("db")
    db.upsert(sample_record(
        path="/tmp/legacy.mp4",
        filename="legacy.mp4",
        frame_tags="這是舊 schema 寫進去的純文字\n第二行也應該被忽略",
        content_type=None, atmosphere=None, energy=None, edit_position=None,
    ))
    resp = fastapi_client.get("/api/export/metadata-csv")
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    import csv as _csv
    from io import StringIO
    rows = list(_csv.reader(StringIO(body)))
    assert rows[1][1] == "這是舊 schema 寫進去的純文字"  # first line only


def test_export_metadata_csv_filters_by_ids_query_param(fastapi_client, sample_record):
    """Audit Batch E F5: ?ids=1,3 只回傳那兩筆，不是整庫 dump。"""
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/a.mp4", filename="a.mp4"))
    db.upsert(sample_record(path="/tmp/b.mp4", filename="b.mp4"))
    db.upsert(sample_record(path="/tmp/c.mp4", filename="c.mp4"))

    resp = fastapi_client.get("/api/export/metadata-csv?ids=1,3")
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    import csv as _csv
    from io import StringIO
    rows = list(_csv.reader(StringIO(body)))
    # Header + 2 rows (ids 1 and 3, not 2)
    assert len(rows) == 3
    filenames = {rows[1][0], rows[2][0]}
    assert filenames == {"a.mp4", "c.mp4"}


def test_export_metadata_csv_ids_empty_filter_returns_header_only(fastapi_client, sample_record):
    """?ids= 全空字串視為「filter 但無 id」→ 只回 header，不回任何 row。"""
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/a.mp4", filename="a.mp4"))
    resp = fastapi_client.get("/api/export/metadata-csv?ids=,,,")
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert body.splitlines() == ["File Name,Description,Keywords,Comments,Scene"]


def test_export_metadata_csv_invalid_ids_returns_400(fastapi_client):
    resp = fastapi_client.get("/api/export/metadata-csv?ids=abc,1")
    assert resp.status_code == 400


def test_export_metadata_csv_to_accepts_ids_for_batch_scoped_export(
    fastapi_client, sample_record, tmp_path
):
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/a.mp4", filename="a.mp4"))
    db.upsert(sample_record(path="/tmp/b.mp4", filename="b.mp4"))
    dest = tmp_path / "scoped.csv"
    resp = fastapi_client.post(
        "/api/export/metadata-csv-to",
        json={"dest": str(dest), "ids": [2]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == 1
    csv_text = dest.read_bytes().decode("utf-8")
    import csv as _csv
    from io import StringIO
    rows = list(_csv.reader(StringIO(csv_text)))
    assert len(rows) == 2
    assert rows[1][0] == "b.mp4"


def test_export_metadata_csv_to_writes_to_user_picked_path(
    fastapi_client, sample_record, tmp_path
):
    """Tauri WKWebView 路徑：server 直接寫檔，body 跟 GET endpoint 一致。"""
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/clipA.mp4", filename="clipA.mp4"))
    db.upsert(sample_record(path="/tmp/clipB.mp4", filename="clipB.mp4"))

    dest = tmp_path / "subdir" / "metadata.csv"
    resp = fastapi_client.post(
        "/api/export/metadata-csv-to", json={"dest": str(dest)}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["path"] == str(dest)
    assert body["size"] > 0
    assert dest.exists()

    # body matches GET endpoint — read_bytes 避免 read_text 把 csv writer 的
    # \r\n 行尾正規化成 \n，造成假的不相等
    get_resp = fastapi_client.get("/api/export/metadata-csv")
    assert dest.read_bytes() == get_resp.content


def test_export_metadata_csv_to_rejects_system_dirs(fastapi_client):
    resp = fastapi_client.post(
        "/api/export/metadata-csv-to", json={"dest": "/etc/arkiv-injected.csv"}
    )
    assert resp.status_code == 403


def test_proxy_build_one_queues_single_media_when_proxy_missing(
    fastapi_client, sample_record, tmp_path, monkeypatch
):
    """Per-id proxy build: 只 queue 指定 media，不撈整庫。"""
    db = importlib.import_module("db")
    config = importlib.import_module("config")

    src = tmp_path / "src.mov"
    src.write_bytes(b"hevc-bytes")
    db.upsert(sample_record(path=str(src), filename="src.mov", ext=".mov"))
    proxies_dir = tmp_path / "proxies"; proxies_dir.mkdir()
    monkeypatch.setattr(config, "PROXIES_DIR", proxies_dir)

    queued = []
    import server
    monkeypatch.setattr(server, "_build_proxies", lambda items: queued.extend(items))

    resp = fastapi_client.post("/api/proxy/build/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] == 1
    assert body["media_id"] == 1
    assert body["filename"] == "src.mov"
    assert len(queued) == 1
    assert queued[0]["id"] == 1


def test_proxy_build_one_returns_404_for_missing_media(fastapi_client):
    resp = fastapi_client.post("/api/proxy/build/99999")
    assert resp.status_code == 404


def test_proxy_build_one_skips_when_proxy_already_exists(
    fastapi_client, sample_record, tmp_path, monkeypatch
):
    db = importlib.import_module("db")
    config = importlib.import_module("config")

    src = tmp_path / "src.mov"
    src.write_bytes(b"hevc-bytes")
    db.upsert(sample_record(path=str(src), filename="src.mov", ext=".mov"))
    proxies_dir = tmp_path / "proxies"; proxies_dir.mkdir()
    monkeypatch.setattr(config, "PROXIES_DIR", proxies_dir)
    # Pre-create the proxy file with the hash-scoped name
    config.proxy_path_for(1, str(src)).write_bytes(b"existing-proxy")

    import server
    queued = []
    monkeypatch.setattr(server, "_build_proxies", lambda items: queued.extend(items))

    resp = fastapi_client.post("/api/proxy/build/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] == 0
    assert "已存在" in body["message"]
    assert queued == []


def test_stream_returns_409_when_hevc_source_has_no_proxy(
    fastapi_client, sample_record, tmp_path, monkeypatch
):
    """Phase 7.7g: HEVC/ProRes 無 proxy 時回 409 + JSON 而非 silently 送原檔。"""
    db = importlib.import_module("db")
    import codec

    fake_mov = tmp_path / "iphone_clip.mov"
    fake_mov.write_bytes(b"fake-hevc-bytes")
    db.upsert(sample_record(
        path=str(fake_mov),
        filename="iphone_clip.mov",
        ext=".mov",
    ))
    codec.clear_cache()
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: "hevc")

    resp = fastapi_client.get("/api/stream/1")
    assert resp.status_code == 409
    body = resp.json()
    assert body == {
        "need_proxy": True,
        "media_id": 1,
        "filename": "iphone_clip.mov",
        "reason": "browser-incompatible codec (HEVC/ProRes); proxy required for playback",
        "hint": "POST /api/proxy/build to queue proxy generation",
    }


def test_stream_serves_h264_source_unchanged_when_proxy_check_says_no(
    fastapi_client, sample_record, tmp_path, monkeypatch
):
    """H.264 .mp4 / .mov 應該繼續直送原檔，不要被 7.7g 的 409 路徑誤殺。"""
    db = importlib.import_module("db")
    import codec

    real_mp4 = tmp_path / "fx30_clip.mp4"
    real_mp4.write_bytes(b"fake-h264-bytes")
    db.upsert(sample_record(
        path=str(real_mp4),
        filename="fx30_clip.mp4",
        ext=".mp4",
    ))
    codec.clear_cache()
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: "h264")

    resp = fastapi_client.get("/api/stream/1")
    assert resp.status_code == 200
    assert resp.content == b"fake-h264-bytes"


def test_stream_falls_back_to_original_when_proxy_probe_returns_unknown(
    fastapi_client, sample_record, tmp_path, monkeypatch
):
    """ffprobe 失敗 → codec.UNKNOWN tri-state，stream endpoint 應 fall through 送原檔。"""
    db = importlib.import_module("db")
    import codec

    real_clip = tmp_path / "unknown_codec.mp4"
    real_clip.write_bytes(b"fallback-bytes")
    db.upsert(sample_record(
        path=str(real_clip),
        filename="unknown_codec.mp4",
        ext=".mp4",
    ))
    codec.clear_cache()
    # probe_codec returns None → needs_proxy() yields UNKNOWN → endpoint serves original
    monkeypatch.setattr(codec, "probe_codec", lambda p, timeout=10.0: None)

    resp = fastapi_client.get("/api/stream/1")
    assert resp.status_code == 200
    assert resp.content == b"fallback-bytes"


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
