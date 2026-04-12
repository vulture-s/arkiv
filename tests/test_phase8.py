import importlib
from pathlib import Path


def test_to_relative_idempotent(tmp_db, tmp_path, monkeypatch):
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    sample = tmp_path / "media" / "clip.mp4"
    rel = db.to_relative(str(sample))
    assert rel == "media/clip.mp4"
    assert db.to_relative(rel) == rel


def test_resolve_path_idempotent(tmp_db, tmp_path, monkeypatch):
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    resolved = db.resolve_path("media/clip.mp4")
    assert resolved == str(tmp_path / "media" / "clip.mp4")
    assert db.resolve_path(resolved) == resolved


def test_to_relative_outside_project_root(tmp_db, tmp_path, monkeypatch):
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    outside = "/var/tmp/outside.mp4"
    assert db.to_relative(outside) == outside


def test_is_processed_both_forms(tmp_db, tmp_path, monkeypatch, sample_record):
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    abs_path = tmp_path / "media" / "clip.mp4"
    record = sample_record(path="media/clip.mp4", thumbnail_path="thumbs/clip.jpg")
    db.upsert(record)
    assert db.is_processed("media/clip.mp4") is True
    assert db.is_processed(str(abs_path)) is True


def test_migrate_to_relative(tmp_db, tmp_path, monkeypatch, sample_record):
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    abs_path = str(tmp_path / "media" / "clip.mp4")
    abs_thumb = str(tmp_path / "thumbnails" / "clip.jpg")
    db.upsert(sample_record(path=abs_path, thumbnail_path=abs_thumb))
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO frames (media_id, frame_index, timestamp_s, thumbnail_path)
            VALUES (1, 0, 1.5, ?)
            """,
            (abs_thumb,),
        )
    db.migrate_to_relative()
    media_row = db.get_record_by_id(1)
    frame_row = db.get_frames(1)[0]
    assert media_row["path"] == "media/clip.mp4"
    assert media_row["thumbnail_path"] == "thumbnails/clip.jpg"
    assert frame_row["thumbnail_path"] == "thumbnails/clip.jpg"


def test_resolve_record_in_api(fastapi_client, tmp_path, monkeypatch, sample_record):
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    db.upsert(sample_record(path="media/clip.mp4", thumbnail_path="thumbnails/clip.jpg"))
    response = fastapi_client.get("/api/media")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["path"] == str(tmp_path / "media" / "clip.mp4")
    assert item["thumbnail_path"] == str(tmp_path / "thumbnails" / "clip.jpg")


def test_adaptive_frame_count_short():
    frames = importlib.import_module("frames")
    assert frames._adaptive_frame_count(1.5) == 1


def test_adaptive_frame_count_medium():
    frames = importlib.import_module("frames")
    assert frames._adaptive_frame_count(30) == 5


def test_adaptive_frame_count_long():
    frames = importlib.import_module("frames")
    assert frames._adaptive_frame_count(120) == 7


def test_schema_new_columns_exist(tmp_db):
    db = importlib.import_module("db")
    with db.get_conn() as conn:
        media_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(media)").fetchall()
        }
        frame_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(frames)").fetchall()
        }
    assert {"focus_score", "editability_score", "edit_reason"}.issubset(media_cols)
    assert {"content_type", "focus_score", "edit_reason"}.issubset(frame_cols)


def test_upsert_frame_with_quality(tmp_db):
    db = importlib.import_module("db")
    db.upsert(
        {
            "path": "/tmp/quality.mp4",
            "filename": "quality.mp4",
            "ext": ".mp4",
            "duration_s": 12.0,
            "size_mb": 5.0,
            "processed_at": "2026-04-11T00:00:00",
        }
    )
    db.upsert_frame(
        media_id=1,
        frame_index=0,
        timestamp_s=2.0,
        thumbnail_path="thumbnails/quality.jpg",
        description="人物訪談中景",
        tags="人物,訪談",
        content_type="Talking-Head",
        focus_score=4,
        exposure="normal",
        stability="穩定",
        audio_quality="清晰",
        atmosphere="溫暖",
        energy="中",
        edit_position="開場",
        edit_reason="建立主題與人物",
    )
    frame = db.get_frames(1)[0]
    assert frame["content_type"] == "Talking-Head"
    assert frame["focus_score"] == 4
    assert frame["edit_reason"] == "建立主題與人物"


def test_compute_editability(tmp_db):
    db = importlib.import_module("db")
    assert db.compute_editability(
        {
            "focus_score": 5,
            "exposure": "normal",
            "stability": "穩定",
            "audio_quality": "清晰",
            "rating": "good",
        }
    ) == 100.0
    assert db.compute_editability(
        {
            "focus_score": 1,
            "exposure": "dark",
            "stability": "嚴重晃動",
            "audio_quality": "嘈雜",
            "rating": "ng",
        }
    ) == 0.0


def test_scenes_endpoint(fastapi_client, sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/scene.mp4", thumbnail_path="/tmp/scene.jpg"))
    db.upsert_frame(
        media_id=1,
        frame_index=0,
        timestamp_s=1.25,
        thumbnail_path="thumbnails/scene_frame0.jpg",
        description="手持走入店內",
        tags="門市,走位",
        content_type="Establishing",
        focus_score=3,
        atmosphere="紀實",
        energy="中",
        edit_position="開場",
        edit_reason="建立場景",
    )
    response = fastapi_client.get("/api/media/1/scenes")
    assert response.status_code == 200
    data = response.json()
    assert data["media_id"] == 1
    assert data["total"] == 1
    assert data["scenes"][0]["content_type"] == "Establishing"
    assert data["scenes"][0]["thumbnail_url"].endswith("/scene_frame0.jpg")


def test_is_usable_frame_rejects_black(tmp_path):
    vis = importlib.import_module("vision")
    from PIL import Image
    black = Image.new("L", (320, 180), 0)
    p = tmp_path / "black.jpg"
    black.save(str(p))
    assert vis._is_usable_frame(str(p)) is False


def test_is_usable_frame_accepts_normal(tmp_path):
    vis = importlib.import_module("vision")
    from PIL import Image
    import numpy as np
    arr = np.random.randint(50, 200, (180, 320), dtype=np.uint8)
    img = Image.fromarray(arr, "L")
    p = tmp_path / "normal.jpg"
    img.save(str(p))
    assert vis._is_usable_frame(str(p)) is True


def test_db_add_tag_with_conn(tmp_db):
    """add_tag with _conn parameter uses the provided connection (no deadlock)."""
    db = importlib.import_module("db")
    db.upsert({"path": "/test.mp4", "filename": "test.mp4", "ext": ".mp4"})
    with db.get_conn() as conn:
        mid = conn.execute("SELECT id FROM media WHERE path='/test.mp4'").fetchone()[0]
        db.add_tag(mid, "hello", source="auto", _conn=conn)
    tags = db.get_tags(mid)
    assert any(t["name"] == "hello" for t in tags)


def test_db_delete_frames_with_conn(tmp_db):
    """delete_frames with _conn parameter uses the provided connection."""
    db = importlib.import_module("db")
    db.upsert({"path": "/test2.mp4", "filename": "test2.mp4", "ext": ".mp4"})
    with db.get_conn() as conn:
        mid = conn.execute("SELECT id FROM media WHERE path='/test2.mp4'").fetchone()[0]
    db.upsert_frame(media_id=mid, frame_index=0, timestamp_s=1.0)
    assert len(db.get_frames(mid)) == 1
    with db.get_conn() as conn:
        db.delete_frames(mid, _conn=conn)
    assert len(db.get_frames(mid)) == 0


def test_db_upsert_frame_with_conn(tmp_db):
    """upsert_frame with _conn parameter uses the provided connection."""
    db = importlib.import_module("db")
    db.upsert({"path": "/test3.mp4", "filename": "test3.mp4", "ext": ".mp4"})
    with db.get_conn() as conn:
        mid = conn.execute("SELECT id FROM media WHERE path='/test3.mp4'").fetchone()[0]
        db.upsert_frame(media_id=mid, frame_index=0, timestamp_s=1.0,
                        description="test", _conn=conn)
    frames = db.get_frames(mid)
    assert len(frames) == 1
    assert frames[0]["description"] == "test"


def test_vision_only_no_deadlock(tmp_db, monkeypatch, tmp_path):
    """Simulate --vision-only flow: vision results written in same conn as tags."""
    db = importlib.import_module("db")
    vis = importlib.import_module("vision")
    # Insert media + frame with empty description
    db.upsert({"path": "/vo_test.mp4", "filename": "vo_test.mp4", "ext": ".mp4"})
    with db.get_conn() as conn:
        mid = conn.execute("SELECT id FROM media WHERE path='/vo_test.mp4'").fetchone()[0]
    # Create a dummy thumbnail
    from PIL import Image
    thumb = tmp_path / "thumb.jpg"
    Image.new("RGB", (160, 90), (128, 128, 128)).save(str(thumb))
    db.upsert_frame(media_id=mid, frame_index=0, timestamp_s=1.0,
                    thumbnail_path=str(thumb), description="")
    # Simulate writing vision result + tags in same conn (the fixed pattern)
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE frames SET description=?, content_type=? WHERE media_id=? AND frame_index=?",
            ("test desc", "A-Roll", mid, 0),
        )
        db.add_tag(mid, "兒童", source="auto", _conn=conn)
        db.add_tag(mid, "教室", source="auto", _conn=conn)
    # Verify
    frames = db.get_frames(mid)
    assert frames[0]["description"] == "test desc"
    tags = db.get_tags(mid)
    assert len(tags) == 2


def test_describe_frames_representative_strategy(monkeypatch):
    vis = importlib.import_module("vision")
    calls = []

    def mock_full(path, max_retries=2):
        calls.append(("full", path))
        r = vis._empty_result()
        r["description"] = "full desc"
        r["content_type"] = "Talking-Head"
        r["atmosphere"] = "溫馨"
        r["energy"] = "中"
        r["edit_position"] = "開場"
        r["edit_reason"] = "建立場景"
        return r

    def mock_light(path, max_retries=2):
        calls.append(("light", path))
        r = vis._empty_result()
        r["description"] = "light desc"
        r["focus_score"] = 3
        return r

    monkeypatch.setattr(vis, "_describe_one", mock_full)
    monkeypatch.setattr(vis, "_describe_one_light", mock_light)
    monkeypatch.setattr(vis, "_is_usable_frame", lambda p: True)
    results = vis.describe_frames(["/a.jpg", "/b.jpg", "/c.jpg"])
    assert len(results) == 3
    # Middle (idx 1) = full, others = light
    assert calls[0] == ("full", "/b.jpg")
    assert calls[1] == ("light", "/a.jpg")
    assert calls[2] == ("light", "/c.jpg")
    # Light frames inherit from representative
    assert results[0]["content_type"] == "Talking-Head"
    assert results[0]["atmosphere"] == "溫馨"
    assert results[2]["edit_position"] == "開場"
