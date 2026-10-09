"""Structural guards for the bug classes that kept recurring in arkiv.

Each guard scans the WHOLE surface (every route / every table / every module),
not the one call site a past fix touched — the point is to stop the *next*
instance of the class, which will be in a file nobody has looked at yet.

Where main still has known violations, the guard is a baseline RATCHET: the
KNOWN set may only shrink. A new violation fails; a KNOWN entry that got fixed
also fails (with "delete it from KNOWN") so the baseline can't silently rot into
an allowlist. Class catalogue: CONTRIBUTING.md § "Recurring bug classes".

Classes covered here:
  A. write routes without the same-site / DNS-rebinding guard   (#498, M14)
  B. media id used as an identity key — ids ARE reused           (#505, #507)
  C. non-atomic writes to a file that is the canonical copy      (#497, #502)

Every scanner has its own negative test (feed it a known-bad input, assert it
says "bad") — a guard that can't fail is not a guard.
"""
import ast
import inspect
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute

from tests.test_state_extraction import _iter_all_routes

REPO = Path(__file__).resolve().parent.parent
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# ════════════════════════════════════════════════════════════════════════════
# A. Every write route must pass the same-site guard
# ════════════════════════════════════════════════════════════════════════════
# A cross-site page can fire "simple" POSTs without a preflight, and a DNS-
# rebinding page reaches 127.0.0.1 with loopback trust. Per-route
# `_assert_same_site(request)` calls kept being forgotten on new routes (audit
# M14 → #498 moves it to a global SameSiteWriteGuard middleware). Until that
# middleware is on main, these are the write routes with no guard at all.
# Key = "<module>.<endpoint>" (stable across FastAPI versions, unlike paths of
# lazily-included routers). ONLY SHRINK THIS LIST.
KNOWN_UNGUARDED_WRITE_ROUTES = {
    "routers.admin.admin_create_token",
    "routers.admin.admin_revoke_token",
    "routers.admin.admin_purge_trash",
    "routers.admin.admin_restore_trash",
    "routers.settings.put_settings",
    "routers.settings.reset_setting",
    "routers.projects.add_project",
    "routers.projects.remove_project",
    "routers.projects.sync_projects",
    "routers.chat.chat_endpoint",
    "routers.bins.create_bin",
    "routers.bins.rename_bin",
    "routers.bins.delete_bin",
    "routers.bins.add_bin_items",
    "routers.bins.remove_bin_item",
    "routers.bins.copy_bin",
    "routers.offload.offload_preview",
    "routers.offload.offload_run",
    "routers.misc.open_file",
    "routers.misc.client_log",
    "routers.export.export_metadata_csv_to",
    "routers.export.export_to_file",
    "routers.export.export_batch",
    "routers.media.update_rating",
    "routers.media.update_inout",
    "routers.media.update_camera",
    "routers.media.add_tag",
    "routers.media.remove_tag",
    "routers.media.retranscribe_media",
    "routers.media.retry_vision",
    "routers.media.delete_media",
    "routers.media.bulk_delete_media",
    "routers.media.prune_missing_media",
    "routers.search.structured_query",
    "routers.ingest.scan_media",
    "routers.ingest.ingest_media",
    "routers.ingest.reingest_media",
    "routers.ingest.ingest_upload",
    "routers.ingest.ingest_media_ws",
}

GLOBAL_WRITE_GUARD_NAMES = {"SameSiteWriteGuard"}


def _has_global_write_guard(app):
    return any(getattr(m.cls, "__name__", "") in GLOBAL_WRITE_GUARD_NAMES
               for m in app.user_middleware)


def _dependency_calls(dependant):
    for d in dependant.dependencies:
        yield d.call
        yield from _dependency_calls(d)


def _route_is_guarded(route):
    try:
        src = inspect.getsource(route.endpoint)
    except (OSError, TypeError):
        src = ""
    if "_assert_same_site(" in src:
        return True
    return any(getattr(c, "__name__", "") == "_assert_same_site"
               for c in _dependency_calls(route.dependant))


def _route_key(route):
    return "{0}.{1}".format(route.endpoint.__module__, route.endpoint.__name__)


def _unguarded_write_routes(app):
    out = set()
    for r in _iter_all_routes(app):
        if not isinstance(r, APIRoute):
            continue
        if not (set(r.methods or ()) & WRITE_METHODS):
            continue
        if not _route_is_guarded(r):
            out.add(_route_key(r))
    return out


def test_detector_flags_an_unguarded_write_route():
    """Negative test for the detector itself."""
    from webguard import _assert_same_site

    app = FastAPI()

    @app.post("/bad")
    def bad_write():
        return {}

    @app.post("/good")
    def good_write(request: Request):
        _assert_same_site(request)
        return {}

    @app.get("/read")
    def a_read():
        return {}

    found = _unguarded_write_routes(app)
    assert any(k.endswith(".bad_write") for k in found)
    assert not any(k.endswith(".good_write") for k in found)
    assert not any(k.endswith(".a_read") for k in found)


def test_every_write_route_is_same_site_guarded(server_module):
    app = server_module.app
    if _has_global_write_guard(app):
        return  # global middleware covers every write route
    new = _unguarded_write_routes(app) - KNOWN_UNGUARDED_WRITE_ROUTES
    assert not new, (
        "new write route(s) without the same-site guard — call "
        "`_assert_same_site(request)` first thing in the handler (or land the "
        "global SameSiteWriteGuard, #498): {0}".format(sorted(new)))


def test_unguarded_write_route_baseline_only_shrinks(server_module):
    app = server_module.app
    if _has_global_write_guard(app):
        pytest.skip("global SameSiteWriteGuard is mounted — KNOWN_UNGUARDED_"
                    "WRITE_ROUTES is dead; delete it")
    stale = KNOWN_UNGUARDED_WRITE_ROUTES - _unguarded_write_routes(app)
    assert not stale, (
        "these KNOWN entries are now guarded (or gone) — delete them from "
        "KNOWN_UNGUARDED_WRITE_ROUTES so the ratchet tightens: {0}".format(sorted(stale)))


# ════════════════════════════════════════════════════════════════════════════
# B. media id is NOT a durable identity — SQLite reuses it
# ════════════════════════════════════════════════════════════════════════════
# media.id is `INTEGER PRIMARY KEY` without AUTOINCREMENT, so deleting the
# highest-id row hands that id to the next ingest. Anything that remembers a
# media id across a delete (resolve-plugin rows #505, sample marker / transcript
# backup revert / bins #507, trash) must either be cascaded away with the row or
# carry a second identity key (path / filename / content hash) and check it.

# Tables that hold a media_id WITHOUT `ON DELETE CASCADE` on purpose, and how
# they stay safe against reuse. ONLY SHRINK / justify each entry.
KNOWN_MEDIA_ID_WITHOUT_CASCADE = {
    # A trash row outlives its media row by design (it IS the record of the
    # delete). Restore must key on original_path / trash_path, never media_id.
    "trash": "historical record; restore keys on original_path (#499)",
}


def test_media_id_reuse_premise_holds(tmp_db):
    """Pins the premise of class B. If this ever fails (e.g. AUTOINCREMENT was
    added), class B's risk changed — revisit CONTRIBUTING.md § Recurring bug classes."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute("INSERT INTO media (path, filename) VALUES ('/a.mp4', 'a.mp4')")
        old = conn.execute("SELECT id FROM media WHERE path='/a.mp4'").fetchone()[0]
        conn.execute("DELETE FROM media WHERE id=?", (old,))
        conn.execute("INSERT INTO media (path, filename) VALUES ('/b.mp4', 'b.mp4')")
        new = conn.execute("SELECT id FROM media WHERE path='/b.mp4'").fetchone()[0]
    finally:
        conn.close()
    assert new == old, "media ids are no longer reused — update class B docs"


def _media_id_tables_without_cascade(conn):
    bad = set()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in tables:
        cols = [r[1] for r in conn.execute('PRAGMA table_info("{0}")'.format(t))]
        if t == "media" or not any(c == "media_id" or c.endswith("_media_id") for c in cols):
            continue
        fks = conn.execute('PRAGMA foreign_key_list("{0}")'.format(t)).fetchall()
        # foreign_key_list row: (id, seq, table, from, to, on_update, on_delete, match)
        cascaded = any(fk[2] == "media" and fk[3].endswith("media_id")
                       and fk[6].upper() == "CASCADE" for fk in fks)
        if not cascaded:
            bad.add(t)
    return bad


def test_media_id_detector_flags_a_table_without_cascade():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE media (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE good (media_id INTEGER REFERENCES media(id) ON DELETE CASCADE)")
    conn.execute("CREATE TABLE bad (media_id INTEGER)")
    conn.execute("CREATE TABLE weak (media_id INTEGER REFERENCES media(id))")
    assert _media_id_tables_without_cascade(conn) == {"bad", "weak"}


def test_every_media_id_column_cascades_or_is_justified(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    try:
        bad = _media_id_tables_without_cascade(conn)
    finally:
        conn.close()
    new = bad - set(KNOWN_MEDIA_ID_WITHOUT_CASCADE)
    assert not new, (
        "table(s) store media_id without ON DELETE CASCADE — ids are reused, so a "
        "stale row will attach to the next ingested clip. Add the cascade, or "
        "justify it in KNOWN_MEDIA_ID_WITHOUT_CASCADE with the second identity "
        "key it checks: {0}".format(sorted(new)))
    stale = set(KNOWN_MEDIA_ID_WITHOUT_CASCADE) - bad
    assert not stale, "now cascaded / gone — delete from KNOWN: {0}".format(sorted(stale))


# ════════════════════════════════════════════════════════════════════════════
# C. Writes to canonical files must be atomic (tmp + os.replace)
# ════════════════════════════════════════════════════════════════════════════
# A plain open(p, "w") truncates first: a crash / full disk / concurrent reader
# sees an empty or half file, and the next load treats "empty" as "nothing
# there" and overwrites the real data (#502 corrections dict, #497 offload
# resume state / MHL). Function-level heuristic: a function that opens a file
# for writing (or write_text/write_bytes) must also os.replace/os.rename (or
# `<tmp>.replace(dst)`) in the same body.
_SCAN_EXCLUDE = {"tests", ".venv", "venv", "node_modules", "src-tauri", "site",
                 "frontend", ".git", ".arkiv", "build", "dist"}

# "<path>:<function>" -> number of non-atomic write sites. Entries are an
# UNREVIEWED baseline (several are fine: CLI reports, probe files, build
# scripts). New entries fail; a count may only go down. Fixing one? Lower / drop
# it here. Genuinely-not-canonical new writer? Add it WITH a one-line reason.
KNOWN_NON_ATOMIC_WRITES = {
    "bench_stt.py:cmd_arkiv": 1,
    "bench_stt.py:cmd_compare": 1,
    "camera_report.py:write_camera_report": 1,
    "corrections.py:_write_backup": 1,
    "export.py:_emit": 1,
    "health.py:preflight_paths": 1,
    "ingest.py:_migrate_storage": 1,
    "ingest.py:_run_apply_aliases": 1,
    "ingest.py:_run_propose_aliases": 1,
    "ingest.py:main": 1,
    "mhl.py:create_manifest": 2,
    "resolve_plugin/arkiv_resolve.py:download_metadata_csv": 1,
    "resolve_plugin/arkiv_resolve_lan.py:download_metadata_csv": 1,
    "routers/export.py:export_metadata_csv_to": 1,
    "routers/export.py:export_to_file": 1,
    "routers/ingest.py:_save_upload_file": 1,
    "sample_prebuilt.py:load_prebuilt": 2,
    "sample_prebuilt.py:remove_sample": 1,
    "scripts/asr_serve.py:do_POST": 1,
    "scripts/build_sample_prebuilt.py:_package": 2,
}


def _own_nodes(fn):
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # nested defs are scanned as their own function
        yield n
        stack.extend(ast.iter_child_nodes(n))


def _open_mode(call):
    """Mode of open(p, m) / io.open(p, m) / os.fdopen(fd, m) — 2nd arg — or of
    Path.open(m) — 1st arg (the receiver is the path)."""
    f = call.func
    idx = 1
    if (isinstance(f, ast.Attribute) and f.attr == "open"
            and not (isinstance(f.value, ast.Name) and f.value.id in ("io", "codecs", "builtins", "os", "tarfile",
                                                                  "gzip", "bz2", "lzma", "zipfile"))):
        idx = 0
    mode = None
    if len(call.args) > idx and isinstance(call.args[idx], ast.Constant):
        mode = call.args[idx].value
    for k in call.keywords:
        if k.arg == "mode" and isinstance(k.value, ast.Constant):
            mode = k.value.value
    return mode


def _non_atomic_writes_in_source(src, label):
    out = {}
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        sites = 0
        atomic = False
        for n in _own_nodes(fn):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in ("open", "fdopen"):
                mode = _open_mode(n)
                if isinstance(mode, str) and ("w" in mode or "x" in mode):
                    sites += 1
            elif name in ("write_text", "write_bytes"):
                sites += 1
            elif isinstance(f, ast.Attribute) and name in ("replace", "rename"):
                recv = f.value
                if isinstance(recv, ast.Name) and recv.id in ("os", "_os"):
                    atomic = True
                elif name == "replace" and len(n.args) == 1 and "tmp" in ast.unparse(recv).lower():
                    atomic = True  # Path(tmp).replace(dst)
        if sites and not atomic:
            out["{0}:{1}".format(label, fn.name)] = sites
    return out


def _repo_non_atomic_writes():
    out = {}
    for p in sorted(REPO.rglob("*.py")):
        rel = p.relative_to(REPO)
        if set(rel.parts) & _SCAN_EXCLUDE:
            continue
        out.update(_non_atomic_writes_in_source(p.read_text(encoding="utf-8"), rel.as_posix()))
    return out


def test_atomic_write_detector_flags_plain_overwrite():
    src = (
        "import os, json\n"
        "def bad(p, d):\n"
        "    with open(p, 'w') as f:\n"
        "        json.dump(d, f)\n"
        "def bad2(p):\n"
        "    p.write_text('x')\n"
        "def bad3(p):\n"
        "    with p.open('w') as f:\n"
        "        f.write('x')\n"
        "def good(p, d):\n"
        "    tmp = str(p) + '.tmp'\n"
        "    with open(tmp, 'w') as f:\n"
        "        json.dump(d, f)\n"
        "    os.replace(tmp, p)\n"
        "def good2(p, tmp_path):\n"
        "    tmp_path.write_text('x')\n"
        "    tmp_path.replace(p)\n"
        "def reader(p):\n"
        "    return open(p).read().replace('a', 'b')\n"
    )
    assert _non_atomic_writes_in_source(src, "x.py") == {"x.py:bad": 1, "x.py:bad2": 1, "x.py:bad3": 1}


def test_no_new_non_atomic_writes():
    found = _repo_non_atomic_writes()
    regress = {k: v for k, v in found.items()
               if v > KNOWN_NON_ATOMIC_WRITES.get(k, 0)}
    assert not regress, (
        "non-atomic write(s) — write to a sibling tmp file then os.replace() it "
        "over the target, so a crash never leaves a truncated canonical file that "
        "the next load reads as 'empty': {0}".format(regress))


def test_non_atomic_write_baseline_only_shrinks():
    found = _repo_non_atomic_writes()
    stale = {k: v for k, v in KNOWN_NON_ATOMIC_WRITES.items() if found.get(k, 0) < v}
    assert not stale, (
        "fixed (or moved) — lower/delete these in KNOWN_NON_ATOMIC_WRITES: "
        "{0}".format({k: found.get(k, 0) for k in stale}))
