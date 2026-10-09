"""Acting on a media id without checking it still names the same clip.

`media.id` is INTEGER PRIMARY KEY with no AUTOINCREMENT, so SQLite hands a
deleted id to the next clip ingested (media_delete.py says so for 精選集). And a
legacy library can hold two rows for one file (abs + rel form, ingest.py H5).
Three writers trusted an id / a row alone and hit someone else's footage:

- delete of a duplicate row moved the file the surviving row still uses;
- transcript backup revert wrote an old clip's transcript onto a new clip;
- "remove sample" deleted (and trashed the original of) a user's own clip.
"""
import json

import config as config_mod
import db as db_mod
import media_delete


def _insert_media(path, filename, thumbnail=None):
    with db_mod.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO media (path, filename, thumbnail_path) VALUES (?,?,?)",
            (path, filename, thumbnail),
        )
        return cur.lastrowid


def _set_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config_mod, "TRASH_DIR", tmp_path / ".arkiv" / "trash")
    monkeypatch.setattr(config_mod, "WAVEFORMS_DIR", tmp_path / "waveforms")
    monkeypatch.setattr(config_mod, "MEDIA_ROOTS", [])
    monkeypatch.setattr(config_mod, "TRASH_TTL_DAYS", 30)
    (tmp_path / ".arkiv").mkdir(parents=True, exist_ok=True)
    (tmp_path / "waveforms").mkdir(parents=True, exist_ok=True)
    (tmp_path / "media-in").mkdir(parents=True, exist_ok=True)


# ── delete: a second row on the same file ────────────────────────────────────

def test_deleting_duplicate_row_keeps_the_file_the_other_row_uses(tmp_path, tmp_db, monkeypatch):
    _set_env(tmp_path, monkeypatch)
    src = tmp_path / "media-in" / "clip.mp4"
    src.write_bytes(b"footage")
    thumb = tmp_path / ".arkiv" / "clip_thumb.jpg"
    thumb.write_bytes(b"t")
    keep = _insert_media("media-in/clip.mp4", "clip.mp4", thumbnail=".arkiv/clip_thumb.jpg")
    dup = _insert_media(str(src), "clip.mp4", thumbnail=".arkiv/clip_thumb.jpg")  # legacy abs-form twin

    result = media_delete.delete_media_full(dup, allow_file_delete=True)

    assert src.exists(), "the surviving row's original must stay on disk"
    assert thumb.exists(), "the shared thumbnail must stay for the surviving row"
    assert result["file_deleted"] is False
    assert str(keep) in (result["warning"] or ""), result
    assert db_mod.list_trash() == []
    with db_mod.get_conn() as conn:
        assert conn.execute("SELECT id FROM media WHERE id=?", (dup,)).fetchone() is None
        assert conn.execute("SELECT id FROM media WHERE id=?", (keep,)).fetchone() is not None


def test_deleting_sole_row_still_moves_to_trash(tmp_path, tmp_db, monkeypatch):
    _set_env(tmp_path, monkeypatch)
    src = tmp_path / "media-in" / "solo.mp4"
    src.write_bytes(b"footage")
    other = tmp_path / "media-in" / "other.mp4"
    other.write_bytes(b"x")
    mid = _insert_media("media-in/solo.mp4", "solo.mp4")
    _insert_media("media-in/other.mp4", "other.mp4")  # unrelated row must not block

    result = media_delete.delete_media_full(mid, allow_file_delete=True)

    assert result["file_deleted"] is True and result["warning"] is None
    assert not src.exists() and other.exists()


# ── transcript backup revert vs. a reused id ─────────────────────────────────

def test_revert_skips_a_row_whose_id_now_belongs_to_another_clip(tmp_path, tmp_db, monkeypatch):
    import corrections
    _set_env(tmp_path, monkeypatch)
    a = _insert_media("media-in/a.mp4", "a.mp4")
    with db_mod.get_conn() as conn:
        conn.execute("UPDATE media SET transcript=? WHERE id=?", ("我們用富田電源線", a))
    applied = corrections.apply([{"from": "富田", "to": "古河", "scope": "global", "post": True}])
    assert applied["media_updated"] == 1

    media_delete.delete_media_full(a, allow_file_delete=False)
    b = _insert_media("media-in/b.mp4", "b.mp4")
    assert b == a, "precondition: SQLite reused the id"
    with db_mod.get_conn() as conn:
        conn.execute("UPDATE media SET transcript=? WHERE id=?", ("完全不同的訪談內容", b))

    out = corrections.revert()

    assert out["restored"] == 0
    assert out.get("skipped") == [b]
    assert db_mod.get_record_by_id(b)["transcript"] == "完全不同的訪談內容"


def test_revert_still_restores_the_same_clip(tmp_path, tmp_db, monkeypatch):
    import corrections
    _set_env(tmp_path, monkeypatch)
    a = _insert_media("media-in/a.mp4", "a.mp4")
    with db_mod.get_conn() as conn:
        conn.execute("UPDATE media SET transcript=? WHERE id=?", ("我們用富田電源線", a))
    corrections.apply([{"from": "富田", "to": "古河", "scope": "global", "post": True}])
    assert db_mod.get_record_by_id(a)["transcript"] == "我們用古河電源線"

    out = corrections.revert()

    assert out["restored"] == 1
    assert db_mod.get_record_by_id(a)["transcript"] == "我們用富田電源線"


def test_revert_of_a_legacy_backup_without_paths_still_works(tmp_path, tmp_db, monkeypatch):
    """Backups written before this fix carry no path. They can't be checked, so
    they keep the old behaviour rather than becoming un-revertable."""
    import corrections
    _set_env(tmp_path, monkeypatch)
    a = _insert_media("media-in/a.mp4", "a.mp4")
    bdir = corrections._backups_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "recorrect-20260101T000000.json").write_text(json.dumps({
        "rules": [], "media": [{"id": a, "transcript": "舊", "segments_json": None, "words_json": None}],
    }), encoding="utf-8")

    out = corrections.revert()

    assert out["restored"] == 1
    assert db_mod.get_record_by_id(a)["transcript"] == "舊"


# ── remove sample vs. a reused id ────────────────────────────────────────────

def test_remove_sample_never_touches_a_user_clip_holding_a_sample_id(tmp_path, tmp_db, monkeypatch):
    import sample_prebuilt
    _set_env(tmp_path, monkeypatch)
    monkeypatch.setattr(sample_prebuilt, "_arkiv_dir", lambda: tmp_path / ".arkiv")
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "sample1.mp4").write_bytes(b"s")
    (tmp_path / "clips" / "sample2.mp4").write_bytes(b"s")
    s1 = _insert_media("clips/sample1.mp4", "sample1.mp4")
    s2 = _insert_media("clips/sample2.mp4", "sample2.mp4")
    sample_prebuilt._loaded_marker().write_text(json.dumps(
        {"media_ids": [s1, s2], "clips": ["sample1.mp4", "sample2.mp4"]}))

    media_delete.delete_media_full(s2, allow_file_delete=True)  # user removes one by hand
    own = tmp_path / "media-in" / "interview_final.mov"
    own.write_bytes(b"precious")
    mine = _insert_media("media-in/interview_final.mov", "interview_final.mov")
    assert mine == s2, "precondition: SQLite reused the id"

    out = sample_prebuilt.remove_sample()

    assert own.exists(), "a user's own clip must never be deleted by remove-sample"
    assert db_mod.get_record_by_id(mine) is not None
    assert db_mod.get_record_by_id(s1) is None
    assert out["removed"] == 1
