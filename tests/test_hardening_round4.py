"""Regression tests for the fable-audit 2026-07-12 security round-4 fixes.

Each test pins one confirmed finding from
docs/2026-07-12-fable-self-check-baseline.md so a future refactor can't silently
regress it:

  #1  /api/offload dst → OS-sensitive-dir denylist
  #3  _allowed_export_roots splits on os.pathsep (not literal ':')
  #4  /api/cache/clear same-site guard (CSRF-open ChromaDB rmtree)
  #10 /api/retranscribe-all language validator
  export-403 body no longer echoes the absolute approved roots
  /reingest + /retranscribe "file not found" no longer leaks the absolute path
  db.py _add_column_if_missing rejects non-allowlisted DDL identifiers
"""
import importlib
import os

import pytest
from fastapi import HTTPException


# ── #3 + export-403 body: export-roots parsing & non-leaking 403 ──────────────

def test_allowed_export_roots_splits_on_os_pathsep(server_module, tmp_path, monkeypatch):
    a = tmp_path / "exp_a"
    b = tmp_path / "exp_b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("ARKIV_EXPORT_ROOTS", f"{a}{os.pathsep}{b}")
    roots = server_module._allowed_export_roots()
    assert a.resolve() in roots
    assert b.resolve() in roots
    # A single Windows-style entry must not be shredded into two bogus roots.
    monkeypatch.setenv("ARKIV_EXPORT_ROOTS", str(a))
    assert server_module._allowed_export_roots() == [a.resolve()]


def test_export_dest_safe_403_body_hides_absolute_roots(server_module, tmp_path, monkeypatch):
    from pathlib import Path
    secret_root = tmp_path / "very-secret-export-root"
    secret_root.mkdir()
    monkeypatch.setenv("ARKIV_EXPORT_ROOTS", str(secret_root))
    with pytest.raises(HTTPException) as exc:
        server_module._assert_export_dest_safe(Path("/etc/evil.csv"))
    assert exc.value.status_code == 403
    # the resolved absolute approved root must NOT appear in the error body
    assert str(secret_root) not in str(exc.value.detail)
    assert "very-secret-export-root" not in str(exc.value.detail)


# ── #4: /api/cache/clear same-site guard ─────────────────────────────────────

def test_cache_clear_allows_non_browser_client(fastapi_client):
    # No Origin / Sec-Fetch-Site (curl / script) → passes the same-site gate.
    resp = fastapi_client.post("/api/cache/clear", params={"target": "waveforms"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_cache_clear_rejects_cross_site_sec_fetch(fastapi_client):
    resp = fastapi_client.post(
        "/api/cache/clear",
        params={"target": "all"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 403


def test_cache_clear_rejects_foreign_origin(fastapi_client):
    resp = fastapi_client.post(
        "/api/cache/clear",
        params={"target": "chromadb"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403


# ── reingest / retranscribe "file not found" path-leak ───────────────────────

_GHOST_ABS = "/Volumes/home/secret-proj/footage/ghost-clip.mov"


def _seed_ghost(sample_record):
    db = importlib.import_module("db")
    db.upsert(sample_record(path=_GHOST_ABS, filename="ghost-clip.mov"))
    return db


@pytest.mark.parametrize("route", ["reingest", "retranscribe"])
def test_missing_media_error_does_not_leak_absolute_path(fastapi_client, sample_record, route):
    _seed_ghost(sample_record)
    resp = fastapi_client.post(f"/api/media/1/{route}", json={})
    assert resp.status_code == 400
    detail = str(resp.json().get("detail", ""))
    assert "ghost-clip.mov" in detail          # basename surfaced
    assert "/Volumes/" not in detail            # absolute path not leaked
    assert "secret-proj" not in detail


# ── db.py: DDL identifier guard ──────────────────────────────────────────────

def test_add_column_if_missing_rejects_unsafe_identifiers(tmp_db):
    db = importlib.import_module("db")
    with db.get_conn() as conn:
        with pytest.raises(ValueError):
            db._add_column_if_missing(conn, "media; DROP TABLE media--", "x", "TEXT")
        with pytest.raises(ValueError):
            db._add_column_if_missing(conn, "media", "bad col", "TEXT")
        with pytest.raises(ValueError):
            db._add_column_if_missing(conn, "not_a_migration_table", "x", "TEXT")
        # a legitimate identifier from the migration allowlist still works (idempotent)
        db._add_column_if_missing(conn, "media", "hardening_probe_col", "TEXT")
        db._add_column_if_missing(conn, "media", "hardening_probe_col", "TEXT")


# ── #1: /api/offload OS-sensitive-dir denylist ───────────────────────────────

@pytest.mark.parametrize("bad", [
    "~/Library/LaunchAgents",
    "~/Library/LaunchDaemons",
    "~/.ssh",
    "/etc",
    "/etc/cron.d",
    "/System/Library",
])
def test_offload_dst_denies_system_dirs(server_module, bad):
    with pytest.raises(HTTPException) as exc:
        server_module._assert_offload_dst_safe(bad)
    assert exc.value.status_code == 403


def test_offload_dst_allows_normal_backup_target(server_module, tmp_path):
    # a plain user/backup directory (the DIT card→drive use case) must pass
    server_module._assert_offload_dst_safe(str(tmp_path))
    server_module._assert_offload_dst_safe("/Volumes/BackupDrive/2026")  # need not exist


def test_offload_route_403s_system_dst_without_spawning(fastapi_client, tmp_path):
    src = tmp_path / "card"
    src.mkdir()
    resp = fastapi_client.post(
        "/api/offload",
        json={"src": str(src), "dst": ["/etc"], "organize": None, "include_heic": False},
    )
    assert resp.status_code == 403


# ── #10: /api/retranscribe-all language validator ────────────────────────────

def test_retranscribe_all_rejects_non_iso639_language(fastapi_client):
    resp = fastapi_client.post("/api/retranscribe-all", json={"language": "中文"})
    assert resp.status_code == 422  # pydantic validation, before any batch work


def test_retranscribe_all_accepts_null_language(fastapi_client):
    # null is valid (auto-detect); must not 422 at the validation layer
    resp = fastapi_client.post("/api/retranscribe-all", json={"language": None})
    assert resp.status_code != 422
