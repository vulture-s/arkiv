"""Pre-built sample library (A1 · launch Wave-0): the fresh-only auto-seed, the
one-click remove + dismiss, the install-dir guard, and db.delete_media cascade.

Drives sample_prebuilt against a temp, explicitly-configured project (its own
PROJECT_ROOT / store paths) using the REAL bundled artifact. Chroma is faked by
conftest, so vector copy/cleanup is exercised only as file ops / best-effort — the
semantic-search proof is the integration run, not a unit test (needs a live embed)."""
import importlib

import pytest

config = importlib.import_module("config")
db = importlib.import_module("db")
sp = importlib.import_module("sample_prebuilt")

_HAVE_ARTIFACT = sp.artifact_available()
_skip_no_artifact = pytest.mark.skipif(not _HAVE_ARTIFACT, reason="prebuilt artifact not bundled")


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A fresh, explicitly-configured project: PROJECT_ROOT + the four store paths
    all under tmp_path, DB initialised, media table empty."""
    root = tmp_path / "proj"
    arkiv = root / ".arkiv"
    arkiv.mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.setattr(config, "DB_PATH", arkiv / "project.db")
    monkeypatch.setattr(config, "CHROMA_PATH", arkiv / "chroma_db")
    monkeypatch.setattr(config, "THUMBNAILS_DIR", arkiv / "thumbnails")
    db.init_db()
    return root


@_skip_no_artifact
def test_load_seeds_fresh_project(project):
    res = sp.load_prebuilt()
    assert res["ok"] and res["media_ids"] == [1, 2, 3, 4]
    # rows landed
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 4
        assert c.execute("SELECT COUNT(*) FROM frames").fetchone()[0] > 0
    # files landed where the relative paths resolve
    assert (project / "clips" / "caminandes_llama.mp4").exists()
    assert list((project / ".arkiv" / "thumbnails").glob("*.jpg"))
    st = sp.status()
    assert st["loaded"] and st["media_ids"] == [1, 2, 3, 4] and not st["dismissed"]


@_skip_no_artifact
def test_load_is_idempotent(project):
    sp.load_prebuilt()
    again = sp.load_prebuilt()
    assert again["ok"] and again.get("already")
    with db.get_conn() as c:  # not doubled
        assert c.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 4


@_skip_no_artifact
def test_remove_deletes_only_sample_and_dismisses(project):
    sp.load_prebuilt()
    rm = sp.remove_sample()
    assert rm["ok"] and rm["removed"] == 4
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 0  # cascade
    assert not (project / "clips" / "caminandes_llama.mp4").exists()
    st = sp.status()
    assert not st["loaded"] and st["dismissed"]


@_skip_no_artifact
def test_load_refuses_non_fresh_project(project, sample_record):
    db.upsert(sample_record(path="/tmp/mine.mp4"))
    res = sp.load_prebuilt()
    assert not res["ok"] and res["reason"] == "project-not-fresh"


@_skip_no_artifact
def test_autoseed_fires_on_fresh_configured_root(project):
    r = sp.maybe_autoseed()
    assert r["seeded"] is True
    assert sp._media_count() == 4


@_skip_no_artifact
def test_autoseed_skips_after_dismiss(project):
    sp.load_prebuilt()
    sp.remove_sample()          # sets dismiss flag
    r = sp.maybe_autoseed()
    assert r["seeded"] is False and r["reason"] == "skip"
    assert sp._media_count() == 0


def test_autoseed_never_touches_install_dir(monkeypatch):
    # PROJECT_ROOT == BASE_DIR is the bare-uvicorn / test default — must never seed.
    monkeypatch.setattr(config, "PROJECT_ROOT", config.BASE_DIR)
    r = sp.maybe_autoseed()
    assert r["seeded"] is False and r["reason"] == "unconfigured-root"


def test_delete_media_cascades_and_reports_thumbs(project, sample_record):
    db.upsert(sample_record(path="/tmp/one.mp4", thumbnail_path="t.jpg"))
    with db.get_conn() as c:
        mid = c.execute("SELECT id FROM media WHERE path=?", ("/tmp/one.mp4",)).fetchone()[0]
        db.add_tag(mid, "keep", _conn=c)
    thumbs = db.delete_media(mid)
    assert thumbs is not None and "t.jpg" in thumbs
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM media WHERE id=?", (mid,)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM tags WHERE media_id=?", (mid,)).fetchone()[0] == 0  # cascade
    assert db.delete_media(999999) is None  # not found


# ── Audit-2026-07-17 regressions (harness-review findings #1/#2/#3/#6) ─────────

@_skip_no_artifact
def test_remove_spares_user_footage(project, sample_record):
    """Finding #1: remove must delete ONLY the sample, never the user's own footage."""
    sp.load_prebuilt()                                        # sample ids 1-4
    db.upsert(sample_record(path="/tmp/mine.mp4", filename="mine.mp4"))  # user row id 5
    rm = sp.remove_sample()
    assert rm["removed"] == 4
    with db.get_conn() as c:
        assert [r[0] for r in c.execute("SELECT filename FROM media").fetchall()] == ["mine.mp4"]


@_skip_no_artifact
def test_force_load_captures_only_sample_ids(project, sample_record):
    """Finding #1 core: even force-loaded onto a library with user footage at a
    non-colliding id, the marker records the PREBUILT ids only — so remove can't
    reach the user's rows."""
    db.upsert(sample_record(path="/tmp/mine.mp4", filename="mine.mp4"))
    with db.get_conn() as c:
        c.execute("UPDATE media SET id=100 WHERE path=?", ("/tmp/mine.mp4",))
    res = sp.load_prebuilt(force=True)
    assert res["ok"] and set(res["media_ids"]) == {1, 2, 3, 4}   # NOT 100
    sp.remove_sample()
    with db.get_conn() as c:
        assert [r[0] for r in c.execute("SELECT id FROM media").fetchall()] == [100]  # user survived


@_skip_no_artifact
def test_merge_survives_added_live_column(project):
    """Finding #3: an added live column must not break the merge (column-intersection,
    not positional SELECT *)."""
    with db.get_conn() as c:
        c.execute("ALTER TABLE media ADD COLUMN future_col TEXT")
    res = sp.load_prebuilt()
    assert res["ok"] and set(res["media_ids"]) == {1, 2, 3, 4}
    assert sp._media_count() == 4


@_skip_no_artifact
def test_partial_failure_leaves_removable_rows(project, monkeypatch):
    """Finding #2: a copy failure AFTER the merge must still leave the rows removable
    (marker written before the copies), not orphaned."""
    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(sp.shutil, "copytree", _boom)   # chroma copy is the first copy step
    with pytest.raises(OSError):
        sp.load_prebuilt()
    assert sp.is_loaded() and sp._media_count() == 4    # rows in + marker present
    assert sp.remove_sample()["removed"] == 4 and sp._media_count() == 0


def test_safe_extract_rejects_symlink(tmp_path):
    """Finding #6: a symlink member is rejected (traversal-through-link defense)."""
    import tarfile as _tf
    tarp = tmp_path / "evil.tar"
    with _tf.open(tarp, "w") as t:
        info = _tf.TarInfo("link")
        info.type = _tf.SYMTYPE
        info.linkname = "/etc/passwd"
        t.addfile(info)
    dest = tmp_path / "out"
    dest.mkdir()
    with _tf.open(tarp) as t:
        with pytest.raises(ValueError):
            sp._safe_extract(t, dest)
