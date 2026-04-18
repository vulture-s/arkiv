import importlib


def _get_row_by_path(db, path):
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT * FROM media WHERE path = ?",
            (path,),
        ).fetchone()


def test_media_type_filter_covers_all_supported_extensions(tmp_db, sample_record):
    db = importlib.import_module("db")
    # Ingest accepts 7 video + 6 audio exts (see ingest.SUPPORTED).
    video_exts = [".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts"]
    audio_exts = [".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"]
    for i, ext in enumerate(video_exts + audio_exts):
        db.upsert(sample_record(path="/tmp/f{0}{1}".format(i, ext), ext=ext))

    video_rows, video_total = db.get_media_filtered(media_type="video", limit=100)
    audio_rows, audio_total = db.get_media_filtered(media_type="audio", limit=100)
    assert video_total == len(video_exts)
    assert audio_total == len(audio_exts)
    assert {r["ext"] for r in video_rows} == set(video_exts)
    assert {r["ext"] for r in audio_rows} == set(audio_exts)


def test_init_db_is_idempotent(tmp_db):
    db = importlib.import_module("db")
    db.init_db()
    db.init_db()
    with db.get_conn() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"media", "tags", "frames"}.issubset(tables)


def test_upsert_updates_existing_path_and_is_processed(tmp_db, sample_record):
    db = importlib.import_module("db")
    record = sample_record(path="/tmp/interview.mp4", filename="interview.mp4")
    db.upsert(record)
    assert db.is_processed("/tmp/interview.mp4") is True

    updated = sample_record(
        path="/tmp/interview.mp4",
        filename="interview-v2.mp4",
        duration_s=88.0,
        transcript="更新後的中文逐字稿",
    )
    db.upsert(updated)

    row = _get_row_by_path(db, "/tmp/interview.mp4")
    assert row["filename"] == "interview-v2.mp4"
    assert row["duration_s"] == 88.0
    assert row["transcript"] == "更新後的中文逐字稿"
    assert db.get_media_count() == 1


def test_get_media_list_and_count_support_pagination_and_filters(tmp_db, sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/a.mp4", duration_s=15, lang="zh"))
    db.upsert(sample_record(path="/tmp/b.mp3", ext=".mp3", duration_s=45, lang="en"))
    db.upsert(sample_record(path="/tmp/c.mov", ext=".mov", duration_s=90, lang="zh"))

    assert db.get_media_count() == 3
    assert db.get_media_count(min_duration=20, max_duration=60) == 1
    assert db.get_media_count(lang="zh") == 2

    page = db.get_media_list(offset=1, limit=1)
    assert len(page) == 1
    assert page[0]["path"] == "/tmp/b.mp3"

    zh_only = db.get_media_list(lang="zh", min_duration=10, max_duration=100)
    assert [row["path"] for row in zh_only] == ["/tmp/a.mp4", "/tmp/c.mov"]


def test_get_record_stats_rating_and_tags_flow(tmp_db, sample_record):
    db = importlib.import_module("db")
    empty_stats = db.get_stats()
    assert empty_stats["total"] == 0
    assert empty_stats["with_transcript"] == 0

    db.upsert(sample_record(path="/tmp/rated.mp4"))
    row = _get_row_by_path(db, "/tmp/rated.mp4")
    media_id = row["id"]

    assert db.get_record_by_id(media_id)["path"] == "/tmp/rated.mp4"
    assert db.get_record_by_id(9999) is None

    db.set_rating(media_id, "good", "保留片段")
    rated = db.get_record_by_id(media_id)
    assert rated["rating"] == "good"
    assert rated["rating_note"] == "保留片段"

    db.set_rating(media_id, None, None)
    cleared = db.get_record_by_id(media_id)
    assert cleared["rating"] is None
    assert cleared["rating_note"] is None

    db.add_tag(media_id, "訪談")
    db.add_tag(media_id, "訪談")
    db.add_tag(media_id, "人物")
    assert [tag["name"] for tag in db.get_tags(media_id)] == ["人物", "訪談"]
    db.remove_tag(media_id, "訪談")
    assert [tag["name"] for tag in db.get_tags(media_id)] == ["人物"]

    stats = db.get_stats()
    assert stats["total"] == 1
    assert stats["with_transcript"] == 1
    assert stats["with_thumb"] == 1
    assert stats["langs"] == {"zh": 1}


def test_get_media_filtered_applies_sort_and_combined_filters(tmp_db, sample_record):
    db = importlib.import_module("db")
    db.upsert(
        sample_record(
            path="/tmp/video-good.mp4",
            filename="b-roll.mp4",
            ext=".mp4",
            duration_s=120,
            size_mb=500,
            lang="zh",
            processed_at="2026-04-09T08:00:00",
        )
    )
    db.upsert(
        sample_record(
            path="/tmp/audio-review.mp3",
            filename="audio.mp3",
            ext=".mp3",
            duration_s=30,
            size_mb=20,
            lang="en",
            processed_at="2026-04-09T07:00:00",
        )
    )
    db.upsert(
        sample_record(
            path="/tmp/video-unrated.mov",
            filename="alpha.mov",
            ext=".mov",
            duration_s=60,
            size_mb=200,
            lang="zh",
            processed_at="2026-04-09T09:00:00",
        )
    )

    db.set_rating(1, "good")
    db.set_rating(2, "review")

    rows, total = db.get_media_filtered(sort="name", lang="zh", media_type="video")
    assert total == 2
    assert [row["filename"] for row in rows] == ["alpha.mov", "b-roll.mp4"]

    rows, total = db.get_media_filtered(sort="duration", rating="good")
    assert total == 1
    assert rows[0]["path"] == "/tmp/video-good.mp4"

    rows, total = db.get_media_filtered(sort="size", rating="unrated")
    assert total == 1
    assert rows[0]["path"] == "/tmp/video-unrated.mov"
