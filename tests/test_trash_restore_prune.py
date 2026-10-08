"""Audit 2026-10-09 (E3): recycle bin + ghost-row prune.

  * HIGH restore_trash resolved a RELATIVE original_path against the process cwd
         (not PROJECT_ROOT), and its fallback referenced an undefined
         `PROJECT_ROOT` → NameError → 500. The Tauri app's cwd is the bundled
         backend dir, so restore failed for practically every App user.
  * MED  "清空全部" purged every trashed original with no confirmation; a negative
         ttl_days also meant "everything".
  * MED  prune-missing treated an unmounted NAS/external volume as "every file
         was deleted" and dropped the whole library (tags/ratings/transcripts),
         with no trash rows to come back from.
"""
import importlib
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def proj(tmp_path, monkeypatch, tmp_db):
    config = importlib.import_module("config")
    root = (tmp_path / "proj").resolve()
    (root / ".arkiv" / "trash").mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    elsewhere = tmp_path / "cwdx"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # Tauri: cwd = bundled backend dir, not the project
    return root


def _trash(root, rel, payload=b"clip"):
    import db
    tp = root / ".arkiv" / "trash" / ("1__" + Path(rel).name)
    tp.write_bytes(payload)
    db.trash_media(1, Path(rel).name, rel, str(tp))
    return db.list_trash()[0]["id"], tp


# ── HIGH: restore ───────────────────────────────────────────────────────────
def test_restore_relative_path_goes_back_under_project_root(proj):
    import db
    (proj / "footage" / "day1").mkdir(parents=True)
    tid, tp = _trash(proj, "footage/day1/A001.mov")
    dest = db.restore_trash(tid)
    assert Path(dest).is_absolute(), "re-ingest gets an absolute path, not a cwd-relative one"
    assert Path(dest) == proj / "footage" / "day1" / "A001.mov"
    assert Path(dest).read_bytes() == b"clip" and not tp.exists()
    assert db.list_trash() == []


def test_restore_falls_back_to_media_in_without_nameerror(proj):
    import db
    tid, _ = _trash(proj, "footage/gone/A002.mov")  # parent folder no longer exists
    dest = db.restore_trash(tid)
    assert Path(dest) == proj / "media-in" / "A002.mov"
    assert Path(dest).exists()


def test_restore_never_clobbers_existing_file(proj):
    import db
    (proj / "footage").mkdir()
    (proj / "footage" / "A003.mov").write_bytes(b"someone else")
    tid, _ = _trash(proj, "footage/A003.mov", payload=b"mine")
    dest = Path(db.restore_trash(tid))
    assert dest.parent == proj / "footage" and dest.name.endswith("__A003.mov")
    assert (proj / "footage" / "A003.mov").read_bytes() == b"someone else"
    assert dest.read_bytes() == b"mine"


def test_restore_endpoint_returns_200_from_foreign_cwd(proj, fastapi_client, monkeypatch):
    import routers.ingest as ri
    monkeypatch.setattr(ri, "_bg_ingest", lambda *a, **k: None)
    (proj / "footage").mkdir()
    tid, _ = _trash(proj, "footage/A004.mov")
    r = fastapi_client.post("/api/admin/trash/restore/{0}".format(tid))
    assert r.status_code == 200, r.text
    assert Path(r.json()["restored_to"]) == proj / "footage" / "A004.mov"


# ── MED: purge ──────────────────────────────────────────────────────────────
def test_purge_rejects_negative_ttl(proj, fastapi_client):
    import db
    _trash(proj, "footage/A005.mov")
    r = fastapi_client.post("/api/admin/trash/purge", json={"ttl_days": -1})
    assert r.status_code == 400
    assert len(db.list_trash()) == 1
    with pytest.raises(ValueError):
        db.purge_trash(-1)


def test_list_trash_reports_size_for_the_confirm_dialog(proj):
    import db
    _trash(proj, "footage/A006.mov", payload=b"x" * 1234)
    assert db.list_trash()[0]["size_bytes"] == 1234


def test_trash_modal_purge_all_goes_through_confirm():
    src = (ROOT / "frontend/src/lib/TrashModal.svelte").read_text(encoding="utf-8")
    assert "on:click={() => purge(0)}" not in src, "清空全部 must not be one click"
    assert "ConfirmDialog" in src


# ── MED: prune-missing vs unmounted storage ─────────────────────────────────
def _row(path, name):
    import db
    db.upsert({"path": path, "filename": name, "ext": ".mov", "duration_s": 1.0, "size_mb": 1.0})
    with db.get_conn() as conn:
        return conn.execute("SELECT id FROM media WHERE path=?", (path,)).fetchone()["id"]


def test_prune_skips_rows_on_unmounted_volume(proj, fastapi_client):
    import db
    vol = "/Volumes/NAS_Footage_{0}".format(uuid.uuid4().hex[:8])  # not mounted
    nas_ids = [_row("{0}/2026/clip{1}.mov".format(vol, i), "clip{0}.mov".format(i)) for i in range(3)]
    db.add_tag(nas_ids[0], "hero-shot")
    (proj / "footage").mkdir()
    gone_id = _row(str(proj / "footage" / "deleted.mov"), "deleted.mov")  # really deleted

    dry = fastapi_client.post("/api/media/prune-missing", json={"dry_run": True}).json()
    assert dry["dry_run"] is True and dry["pruned"] == 0
    assert dry["prunable"] == 1
    assert {u["root"]: u["count"] for u in dry["unavailable_roots"]} == {vol: 3}

    real = fastapi_client.post("/api/media/prune-missing", json={"dry_run": False}).json()
    assert real["pruned_ids"] == [gone_id]
    assert {u["root"] for u in real["unavailable_roots"]} == {vol}
    with db.get_conn() as conn:
        left = {r["id"] for r in conn.execute("SELECT id FROM media").fetchall()}
    assert set(nas_ids) <= left, "rows on an unmounted volume must survive"
    assert gone_id not in left


def test_prune_still_removes_file_deleted_from_mounted_storage(proj, fastapi_client):
    (proj / "footage").mkdir()
    gid = _row(str(proj / "footage" / "sub" / "gone.mov"), "gone.mov")  # whole subfolder gone
    real = fastapi_client.post("/api/media/prune-missing", json={"dry_run": False}).json()
    assert real["pruned_ids"] == [gid] and real["unavailable_roots"] == []


def test_frontend_prune_client_sends_json_body():
    src = (ROOT / "frontend/src/lib/api.js").read_text(encoding="utf-8")
    seg = src[src.index("export const pruneMissing"):]
    seg = seg[:seg.index("\n\n")]
    assert "dry_run: dryRun" in seg and "body:" in seg and "qs(" not in seg
