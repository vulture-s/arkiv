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


def test_delete_auto_tags_clears_machine_tags_but_keeps_manual(tmp_db, sample_record):
    """Re-ingest must drop stale auto tags (e.g. a fixed vision mislabel) while
    preserving user-added manual tags."""
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/retag.mp4"))
    media_id = _get_row_by_path(db, "/tmp/retag.mp4")["id"]

    db.add_tag(media_id, "三文魚", source="auto")  # wrong machine tag
    db.add_tag(media_id, "生魚", source="auto")
    db.add_tag(media_id, "保留", source="manual")  # user-added

    db.delete_auto_tags(media_id)
    remaining = [t["name"] for t in db.get_tags(media_id)]
    assert remaining == ["保留"]  # only the manual tag survives


def test_manual_add_promotes_auto_tag_so_it_survives_refresh(tmp_db, sample_record):
    """A tag first created by vision (auto) then confirmed by the user (manual)
    must be promoted to source='manual' so a re-ingest's auto clear keeps it —
    relying on the original source alone lost user-confirmed tags (Codex P2)."""
    db = importlib.import_module("db")
    db.upsert(sample_record(path="/tmp/promote.mp4"))
    media_id = _get_row_by_path(db, "/tmp/promote.mp4")["id"]

    db.add_tag(media_id, "生魚", source="auto")     # vision created it
    db.add_tag(media_id, "生魚", source="manual")   # user confirms it by hand
    # an auto re-add must NOT downgrade it back
    db.add_tag(media_id, "生魚", source="auto")

    db.delete_auto_tags(media_id)
    assert [t["name"] for t in db.get_tags(media_id)] == ["生魚"]  # survived


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


def test_resolve_path_rejects_traversal_outside_project_root():
    """Codex Round-2 audit (J2): a poisoned DB row with `../../../etc/passwd`
    used to silently expand under PROJECT_ROOT and let /api/stream serve
    out-of-root files. Now resolve_path raises so the streaming endpoint
    refuses the row instead of leaking host secrets."""
    import pytest
    db = importlib.import_module("db")

    with pytest.raises(ValueError, match="逃出 PROJECT_ROOT"):
        db.resolve_path("../../../etc/passwd")
    with pytest.raises(ValueError, match="逃出 PROJECT_ROOT"):
        db.resolve_path("../../../../../tmp/escape")


def test_resolve_path_passes_through_inside_root_and_absolute():
    import sys
    db = importlib.import_module("db")
    # Relative inside root → joined absolute path under PROJECT_ROOT. Stored
    # rel-paths are POSIX (to_relative.as_posix), but the RESOLVED absolute path
    # is OS-native (it's handed to open()/streaming), so compare separator-agnostically.
    inside = db.resolve_path("media/clip.mp4")
    assert inside.replace("\\", "/").endswith("media/clip.mp4")
    # Absolute path passed through as-is (legacy rows) — OS-native absolute form.
    abs_path = "C:\\tmp\\some_clip.mp4" if sys.platform == "win32" else "/tmp/some_clip.mp4"
    assert db.resolve_path(abs_path) == abs_path
    # Empty stays empty (idempotent)
    assert db.resolve_path("") == ""


def test_db_file_is_owner_only(tmp_db):
    """Token-hash DB must not be world/group-readable on POSIX."""
    import os, stat, sys
    if sys.platform == "win32":
        return  # chmod semantics differ on Windows
    db = importlib.import_module("db")
    with db.get_conn() as conn:
        conn.execute("SELECT 1")
    mode = stat.S_IMODE(os.stat(db.DB_PATH).st_mode)
    assert mode & 0o077 == 0, f"DB world/group-accessible: {oct(mode)}"
