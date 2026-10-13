"""Tests for unified media delete + orphan reconcile (Phase 14.5).

Covers: file moved to trash (not unlinked), derivatives (proxy/waveform/thumbnail)
cleaned, external paths are metadata-only, missing rows -> None, and
prune-missing removes ghost records.
"""
import db as db_mod
import config as config_mod
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


def test_delete_moves_original_to_trash(tmp_path, tmp_db, monkeypatch):
    _set_env(tmp_path, monkeypatch)
    src = tmp_path / "media-in" / "clip.mp4"
    src.write_bytes(b"dummy")
    mid = _insert_media("media-in/clip.mp4", "clip.mp4")

    result = media_delete.delete_media_full(mid, allow_file_delete=True, token_info=None)

    assert result is not None
    assert result["file_deleted"] is True
    assert result["warning"] is None
    assert not src.exists()
    trash_files = list((config_mod.TRASH_DIR).glob("*__clip.mp4"))
    assert trash_files, "original should be moved into trash"
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT id FROM media WHERE id=?", (mid,)).fetchone()
    assert row is None


def test_delete_cleans_proxy_waveform_thumbnail(tmp_path, tmp_db, monkeypatch):
    _set_env(tmp_path, monkeypatch)
    src = tmp_path / "media-in" / "clip.mp4"
    src.write_bytes(b"dummy")
    thumb = tmp_path / ".arkiv" / "thumb.jpg"
    thumb.write_bytes(b"t")
    mid = _insert_media("media-in/clip.mp4", "clip.mp4", thumbnail=".arkiv/thumb.jpg")
    proxy = config_mod.proxy_path_for(mid, str(tmp_path / "media-in" / "clip.mp4"))
    proxy.write_bytes(b"p")
    wf = config_mod.WAVEFORMS_DIR / ("%d_8.json" % mid)
    wf.write_bytes(b"w")

    media_delete.delete_media_full(mid, allow_file_delete=True, token_info=None)

    assert not proxy.exists(), "proxy should be removed"
    assert not wf.exists(), "waveform cache should be removed"
    assert not thumb.exists(), "thumbnail should be removed"


def test_external_path_is_metadata_only(tmp_path, tmp_db, monkeypatch):
    _set_env(tmp_path, monkeypatch)
    ext = "/etc/arkiv_external_test_clip.mp4"
    mid = _insert_media(ext, "ext_clip.mp4")

    result = media_delete.delete_media_full(mid, allow_file_delete=True, token_info=None)

    assert result is not None
    assert result["file_deleted"] is False
    assert "metadata-only" in (result["warning"] or "")
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT id FROM media WHERE id=?", (mid,)).fetchone()
    assert row is None


def test_delete_missing_row_returns_none(tmp_db):
    assert media_delete.delete_media_full(999999) is None


def test_prune_missing_removes_ghost_records(tmp_path, tmp_db, monkeypatch):
    _set_env(tmp_path, monkeypatch)
    ghost = _insert_media("media-in/gone.mp4", "gone.mp4")
    src = tmp_path / "media-in" / "keep.mp4"
    src.write_bytes(b"x")
    keep = _insert_media("media-in/keep.mp4", "keep.mp4")

    missing = db_mod.iter_missing()
    assert any(m["id"] == ghost for m in missing)
    assert not any(m["id"] == keep for m in missing)

    removed = 0
    for m in missing:
        r = media_delete.delete_media_full(m["id"], allow_file_delete=False, token_info=None)
        if r is not None:
            removed += 1

    assert removed == 1
    with db_mod.get_conn() as conn:
        rows = conn.execute("SELECT id FROM media").fetchall()
    ids = [r["id"] for r in rows]
    assert ghost not in ids
    assert keep in ids


def test_trash_list_and_purge(tmp_path, tmp_db, monkeypatch):
    _set_env(tmp_path, monkeypatch)
    src = tmp_path / "media-in" / "clip.mp4"
    src.write_bytes(b"dummy")
    mid = _insert_media("media-in/clip.mp4", "clip.mp4")
    media_delete.delete_media_full(mid, allow_file_delete=True, token_info=None)

    trash = db_mod.list_trash()
    assert len(trash) == 1
    assert trash[0]["media_id"] == mid

    purged = db_mod.purge_trash(ttl_days=0)
    assert purged == 1
    assert db_mod.list_trash() == []
