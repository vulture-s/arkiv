"""Per-project DB resolver (projects.resolve_project_db).

Phase 8.0c renamed the per-project SQLite DB media.db → project.db. Libraries
indexed before the rename keep their corpus in the legacy .arkiv/media.db and
may carry only an empty .arkiv/project.db stub. Federation search + registry
sync used to hardcode project.db, so those projects returned nothing. The
resolver falls back to media.db in exactly that case while leaving a real,
populated project.db untouched.
"""
import importlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _write_media_db(db_path: Path, rows: int) -> None:
    """Create a SQLite DB with a `media` table holding `rows` rows."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE media (id INTEGER PRIMARY KEY, path TEXT, filename TEXT, "
            "duration_s REAL, rating TEXT, lang TEXT, ext TEXT, transcript TEXT)"
        )
        for idx in range(1, rows + 1):
            conn.execute(
                "INSERT INTO media (id, path, filename, transcript) VALUES (?, ?, ?, ?)",
                (idx, "clips/{0}.mp4".format(idx), "clip_{0}.mov".format(idx), "token {0}".format(idx)),
            )
        conn.commit()
    finally:
        conn.close()


def test_falls_back_to_media_db_when_project_db_is_empty_stub(tmp_path):
    """The core bug: empty project.db stub + populated legacy media.db."""
    projects = importlib.import_module("projects")
    root = tmp_path / "legacy-project"
    arkiv = root / ".arkiv"
    _write_media_db(arkiv / "project.db", rows=0)   # schema-only stub, 0 rows
    _write_media_db(arkiv / "media.db", rows=480)   # legacy corpus with data

    resolved = projects.resolve_project_db(root)
    assert resolved == arkiv / "media.db"


def test_falls_back_when_project_db_is_zero_byte_stub(tmp_path):
    """A literal 0-byte project.db (no schema at all) is also treated as empty."""
    projects = importlib.import_module("projects")
    root = tmp_path / "zero-byte"
    arkiv = root / ".arkiv"
    arkiv.mkdir(parents=True)
    (arkiv / "project.db").write_bytes(b"")         # 0-byte file
    _write_media_db(arkiv / "media.db", rows=12)

    assert projects.resolve_project_db(root) == arkiv / "media.db"


def test_populated_project_db_is_unaffected(tmp_path):
    """A real, populated project.db always wins — even if a legacy media.db with
    data sits beside it. Backward-compat guarantee for existing libraries."""
    projects = importlib.import_module("projects")
    root = tmp_path / "modern-project"
    arkiv = root / ".arkiv"
    _write_media_db(arkiv / "project.db", rows=50)  # populated → must be chosen
    _write_media_db(arkiv / "media.db", rows=999)   # stale legacy copy, ignored

    assert projects.resolve_project_db(root) == arkiv / "project.db"


def test_absent_both_defaults_to_project_db(tmp_path):
    """No DB at all → default project.db path (health preflight flags it as
    db_missing downstream, same as before)."""
    projects = importlib.import_module("projects")
    root = tmp_path / "fresh-project"
    (root / ".arkiv").mkdir(parents=True)
    assert projects.resolve_project_db(root) == root / ".arkiv" / "project.db"


def test_empty_project_db_and_empty_media_db_defaults_to_project_db(tmp_path):
    """Neither DB has data → keep the modern default (nothing to prefer)."""
    projects = importlib.import_module("projects")
    root = tmp_path / "both-empty"
    arkiv = root / ".arkiv"
    _write_media_db(arkiv / "project.db", rows=0)
    _write_media_db(arkiv / "media.db", rows=0)
    assert projects.resolve_project_db(root) == arkiv / "project.db"


def test_explicit_override_always_wins(tmp_path):
    """An explicit path takes precedence over both project.db and media.db and
    is returned verbatim (honor explicit DB-path overrides)."""
    projects = importlib.import_module("projects")
    root = tmp_path / "overridden"
    arkiv = root / ".arkiv"
    _write_media_db(arkiv / "project.db", rows=5)
    _write_media_db(arkiv / "media.db", rows=5)
    override = tmp_path / "elsewhere" / "custom.db"
    assert projects.resolve_project_db(root, explicit=override) == override


def test_sync_projects_uses_legacy_media_db_mtime(tmp_path, monkeypatch):
    """The second hardcode site: registry sync records last_indexed_at from the
    resolved DB, so a legacy project reflects media.db's mtime, not the stub's."""
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "projects.json"))
    projects = importlib.import_module("projects")

    root = tmp_path / "legacy-sync"
    arkiv = root / ".arkiv"
    _write_media_db(arkiv / "project.db", rows=0)
    _write_media_db(arkiv / "media.db", rows=7)

    # Distinct mtimes so we can prove which file sync read.
    media_mtime = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    stub_mtime = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    os.utime(arkiv / "media.db", (media_mtime, media_mtime))
    os.utime(arkiv / "project.db", (stub_mtime, stub_mtime))

    projects.add_project("legacy-sync", str(root))
    synced = projects.sync_projects()
    assert len(synced) == 1
    expected = projects._iso_from_mtime(arkiv / "media.db")
    assert synced[0].last_indexed_at == expected


# ── Optional real-library check (opt-in via env, skipped otherwise) ───────────
# Point ARKIV_TEST_LEGACY_PROJECT at a real project root whose corpus lives in
# the legacy .arkiv/media.db behind an empty .arkiv/project.db stub to exercise
# the resolver + federation against a real database end-to-end.
_REAL_ROOT = os.getenv("ARKIV_TEST_LEGACY_PROJECT", "")


@pytest.mark.skipif(
    not (_REAL_ROOT and (Path(_REAL_ROOT) / ".arkiv" / "media.db").exists()),
    reason="set ARKIV_TEST_LEGACY_PROJECT to a mounted legacy project to run",
)
def test_real_legacy_project_prefers_media_db_and_federation_returns_results():
    projects = importlib.import_module("projects")
    federation = importlib.import_module("federation")
    root = Path(_REAL_ROOT)

    assert projects.resolve_project_db(root) == root / ".arkiv" / "media.db"

    proj = projects.ProjectMeta(name="legacy-real", path=root)
    res = federation.query_single_project(proj, "A", limit=5, q_embed=None, fallback_sql=True)
    assert res.status == "ok", res.error
    assert len(res.items) > 0
