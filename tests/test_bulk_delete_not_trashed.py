"""Audit 2026-10-09 K1: bulk-delete must report which deletes did NOT reach the
recycle bin.

delete_media_full returns {file_deleted, warning}; bulk-delete used to keep only
the id, so a metadata-only delete (clip outside PROJECT_ROOT — every NAS
/Volumes library while ARKIV_MEDIA_ROOTS is unset) was indistinguishable from a
recoverable trash move, and the UI told the user it could be restored.
"""
import config as config_mod
import db as db_mod


def _insert(path, filename):
    with db_mod.get_conn() as conn:
        return conn.execute(
            "INSERT INTO media (path, filename) VALUES (?,?)", (path, filename)
        ).lastrowid


def _env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / ".arkiv").mkdir(parents=True)
    (root / "media-in").mkdir()
    monkeypatch.setattr(config_mod, "PROJECT_ROOT", root)
    monkeypatch.setattr(config_mod, "TRASH_DIR", root / ".arkiv" / "trash")
    monkeypatch.setattr(config_mod, "WAVEFORMS_DIR", root / "waveforms")
    monkeypatch.setattr(config_mod, "MEDIA_ROOTS", [])
    return root


def test_bulk_delete_reports_metadata_only_items(tmp_path, monkeypatch, fastapi_client):
    root = _env(tmp_path, monkeypatch)
    inside = root / "media-in" / "in.mov"
    inside.write_bytes(b"x")
    outside = tmp_path / "nas_vol" / "A001.mov"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    a = _insert("media-in/in.mov", "in.mov")
    b = _insert(str(outside), "A001.mov")

    r = fastapi_client.post(
        "/api/media/bulk-delete",
        json={"ids": [a, b, 99999], "allow_file_delete": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["deleted"]) == sorted([a, b])
    assert body["skipped"] == [99999]
    not_trashed = {x["media_id"]: x for x in body["not_trashed"]}
    assert list(not_trashed) == [b], body
    assert "metadata-only" in not_trashed[b]["warning"]
    assert {x["media_id"] for x in body["warnings"]} == {b}
    # and the outside original really is untouched, the inside one really trashed
    assert outside.exists()
    assert not inside.exists()


def test_bulk_delete_clean_run_has_empty_not_trashed(tmp_path, monkeypatch, fastapi_client):
    root = _env(tmp_path, monkeypatch)
    (root / "media-in" / "c.mov").write_bytes(b"x")
    c = _insert("media-in/c.mov", "c.mov")
    body = fastapi_client.post(
        "/api/media/bulk-delete", json={"ids": [c], "allow_file_delete": True}
    ).json()
    assert body["deleted"] == [c]
    assert body["not_trashed"] == []
    assert body["warnings"] == []
