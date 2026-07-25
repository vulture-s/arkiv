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


# ── #1 (Windows correctness, 2026-07-25): the deny decision is host-independent ─
# On Windows, Path('/etc').resolve() drive-anchors to 'C:\etc', so the old
# '/'-rooted string match silently opened the 403 gate (4-fail on windows-latest).
# These feed already-normalised Windows-shaped strings straight to the pure
# denylist helper, so the Windows behaviour is provable on ANY host — no Windows
# runner needed. (The full-suite Windows run is separate evidence; see the
# 2026-07-24 arkiv-health-hardening handoff.)

@pytest.mark.parametrize("denied", [
    "c:/etc",                          # a POSIX literal after resolve() drive-anchors it
    "c:/windows",
    "c:/windows/system32",
    "d:/windows/system32/drivers",     # drive-agnostic
    "c:/program files",
    "c:/program files (x86)/evil",
    "c:/programdata/evil",
    "c:/users/me/appdata/roaming/microsoft/windows/start menu/programs/startup",
])
def test_offload_deny_reason_denies_windows_system_dirs(denied):
    import webguard
    assert webguard._offload_deny_reason(denied) != ""


@pytest.mark.parametrize("allowed", [
    "d:/backup/2026",
    "e:/dit/card01",
    "f:/volumes/program files backup",  # 'program files' as a leaf, not a root
    "c:/users/me/movies/exports",
])
def test_offload_deny_reason_allows_windows_backup_targets(allowed):
    import webguard
    assert webguard._offload_deny_reason(allowed) == ""


def test_offload_deny_reason_denies_posix_literal_host_independently():
    # The raw (pre-resolve) pass must deny a POSIX-absolute sensitive literal even
    # where resolve() would drive-anchor it off the '/'-rooted denylist — the exact
    # bug that let '/etc' through on windows-latest.
    import webguard
    assert webguard._offload_deny_reason("/etc") == "system"
    assert webguard._offload_deny_reason("/system/library") == "system"
    assert webguard._offload_deny_reason("/users/me/.ssh") == "sensitive"


# ── #1 (2026-07-25 audit follow-up): Windows namespace / DOS-device / admin-share
# forms must not slip past the deny roots, and a non-letter "drive" must not be
# stripped. These run the FULL pipeline (_norm_offload_path -> _offload_deny_reason)
# so the prefix canonicalisation is exercised. \\?\C:\Windows etc. previously
# normalised to '//?/c:/windows' and returned '' (a false negative on windows-latest).

@pytest.mark.parametrize("raw", [
    r"\\?\C:\Windows\System32",              # extended-length device path
    r"\\.\C:\Windows",                        # DOS-device path
    r"\\localhost\C$\Windows",               # admin drive share (\\host\C$)
    r"\\127.0.0.1\C$\Windows\System32",      # admin share addressed by IP
    r"\\?\UNC\fileserver\C$\Windows",        # UNC via the device namespace
    "C:\\Windows.",                          # trailing dot — Win32 strips it
    "C:\\Windows ",                          # trailing space — Win32 strips it
])
def test_offload_deny_reason_denies_windows_namespace_forms(raw):
    import webguard
    assert webguard._offload_deny_reason(webguard._norm_offload_path(raw)) != ""


@pytest.mark.parametrize("raw", [
    r"\\NAS\media\footage\2026",             # legit network share — not a system dir
    r"\\?\D:\Backup\2026",                    # extended-length path to a backup drive
    "1:/etc",                                 # non-letter 'drive' must NOT be drive-stripped
])
def test_offload_deny_reason_allows_legit_unc_extended_and_nonletter_drive(raw):
    import webguard
    assert webguard._offload_deny_reason(webguard._norm_offload_path(raw)) == ""


# ── #10: /api/retranscribe-all language validator ────────────────────────────

def test_retranscribe_all_rejects_non_iso639_language(fastapi_client):
    resp = fastapi_client.post("/api/retranscribe-all", json={"language": "中文"})
    assert resp.status_code == 422  # pydantic validation, before any batch work


def test_retranscribe_all_accepts_null_language(fastapi_client):
    # null is valid (auto-detect); must not 422 at the validation layer
    resp = fastapi_client.post("/api/retranscribe-all", json={"language": None})
    assert resp.status_code != 422


# ── round-5 #16: 'app' cache-clear must NOT nuke DB-referenced thumbnails ─────

def test_cache_clear_app_preserves_thumbnails(fastapi_client, tmp_path, monkeypatch):
    config = importlib.import_module("config")
    thumbs = tmp_path / "thumbnails"; thumbs.mkdir()
    (thumbs / "clip_thumb.jpg").write_bytes(b"jpg")
    (thumbs / "clip_frame0.jpg").write_bytes(b"jpg")
    monkeypatch.setattr(config, "THUMBNAILS_DIR", thumbs)

    # 'app' clears cheap regenerables only — thumbnails survive (grid + vision intact)
    r = fastapi_client.post("/api/cache/clear", params={"target": "app"})
    assert r.status_code == 200
    assert (thumbs / "clip_thumb.jpg").exists()
    assert not any("thumbnails" in c for c in r.json()["cleared"])

    # explicit target='thumbnails' still clears them
    r = fastapi_client.post("/api/cache/clear", params={"target": "thumbnails"})
    assert r.status_code == 200
    assert list(thumbs.iterdir()) == []
    assert any("thumbnails" in c for c in r.json()["cleared"])


# ── round-5 #15: chromadb clear invalidates the in-process client cache ───────

def test_cache_clear_chromadb_invalidates_client_cache(fastapi_client, tmp_path, monkeypatch):
    config = importlib.import_module("config")
    vectordb = importlib.import_module("vectordb")
    chroma = tmp_path / "chroma"; chroma.mkdir()
    (chroma / "index.bin").write_bytes(b"x")
    monkeypatch.setattr(config, "CHROMA_PATH", chroma)
    called = {"n": 0}
    monkeypatch.setattr(vectordb, "clear_client_cache", lambda: called.__setitem__("n", called["n"] + 1))

    r = fastapi_client.post("/api/cache/clear", params={"target": "chromadb"})
    assert r.status_code == 200
    assert not chroma.exists()          # index removed
    assert called["n"] == 1             # cached System dropped so a rebuild is seen


def test_cache_clear_refuses_chromadb_during_embed_rebuild(fastapi_client):
    import server
    assert server._embed_guard.acquire()  # R5-22: a rebuild is mid-flight (state.SingleFlight)
    try:
        r = fastapi_client.post("/api/cache/clear", params={"target": "chromadb"})
        assert r.status_code == 409
    finally:
        server._embed_guard.release()
