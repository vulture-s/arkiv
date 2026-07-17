"""
Media Asset Manager — FastAPI Backend
Serves the UI (index.html) and provides REST API for all CRUD operations.

Usage:
    uvicorn server:app --host 0.0.0.0 --port 8501
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Optional, Set

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import admin
import bins as bins_store
import chat
import codec
import auth
from auth import require_scopes
import config
import corrections
import db
import federation
import projects as project_registry
import settings as settings_store
import tag_quality


# ── Ingest single-flight guard + WS broadcaster ──────────────────────────────
# Extracted to state.py (fable-audit 2026-07-12) as the APIRouter-split foundation:
# the ingest single-flight slot and the WS IngestBroadcaster are shared runtime
# state that a future per-router split must import as ONE instance (forking them
# would silently break the H3 double-whisper-OOM guard). Re-exported here so
# existing call sites + tests keep referencing server._acquire_ingest_slot /
# server.ingest_ws unchanged.
import threading as _threading
from state import (  # noqa: F401  (re-exported for backward compat)
    IngestBroadcaster,
    _acquire_ingest_slot,
    _release_ingest_slot,
    ingest_ws,
    embed_rebuild as _embed_guard,
    retranscribe as _retranscribe_guard,
    proxy_build as _proxy_guard,
    _rebuild_embeddings,
)

# R5-22 (#52): the embed-rebuild / retranscribe single-flight guards + the
# retranscribe progress dict now live in state.py as SingleFlight OBJECTS
# (_embed_guard / _retranscribe_guard), so an APIRouter-split module imports a
# live instance instead of a frozen bool copy. The retranscribe progress dict is
# _retranscribe_guard.progress (mutated in place).

# The offload per-source single-flight primitives (_offload_lock / _offload_active
# / _acquire_offload_slot / _release_offload_slot) moved to routers/offload.py
# with the /api/offload handlers (R5-25 / #51).


# ── Init ─────────────────────────────────────────────────────────────────────
# R5-23 (#53): DB init + log-filter install are SIDE EFFECTS and must NOT fire at
# import time — a transitional `import server` (e.g. during the APIRouter split, a
# tooling import, or a test collection) used to create .arkiv/ and run migrations
# against the env-configured PRODUCTION db before any preflight ran. They now run
# in _lifespan, so they fire on real app startup only. TestClient enters the
# lifespan via its context manager, so the fixtures keep working.

# Redact `?token=` from uvicorn access logs. /api/stream accepts the token as a
# query param (a <video src> can't send a header), and uvicorn's default access
# log records the full request line incl. query string → the raw token would be
# written to stdout / any redirected logfile. This filter scrubs it everywhere
# the access logger formats a request path. (Class defined at import; INSTALLED in
# _lifespan so a bare import doesn't mutate global logging state.)
import logging as _logging
import re as _re
_TOKEN_QS = _re.compile(r"(token=)[^&\s\"']+")
class _RedactTokenFilter(_logging.Filter):
    def filter(self, record):
        try:
            if record.args:
                record.args = tuple(
                    _TOKEN_QS.sub(r"\1REDACTED", a) if isinstance(a, str) else a
                    for a in record.args
                )
        except Exception:
            pass
        return True

@asynccontextmanager
async def _lifespan(app: FastAPI):
    db.init_db()  # R5-23 (#53): create/migrate the db on startup, not at import
    _install_token_redaction_filter()
    _bootstrap_admin_token()  # late-bound; defined below near the admin routes
    yield
    # no shutdown work today; add teardown after the yield when needed


def _install_token_redaction_filter() -> None:
    # Idempotent: never stack a second filter if the app is (re)started in-process.
    logger = _logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _RedactTokenFilter) for f in logger.filters):
        logger.addFilter(_RedactTokenFilter())

app = FastAPI(title="Media Asset Manager API", lifespan=_lifespan)
# R5-25 / #51: web-security boundary guards live in webguard.py (a leaf service
# module, no server state) so the coming APIRouter split can import them without
# the router→server→router import cycle. `_ALLOWED_ORIGINS` is owned there (both
# the CORS middleware below AND webguard._assert_same_site need it). Re-exported
# here for backward compat — call sites / tests referencing
# `server._assert_export_dest_safe` etc. keep working unchanged.
from webguard import (  # noqa: F401,E402 (re-exported for backward compat)
    _ALLOWED_ORIGINS,
    _ALLOWED_EXPORT_EXTS,
    _allowed_export_roots,
    _assert_export_dest_safe,
    _OFFLOAD_DENY_SUBSTR,
    _OFFLOAD_DENY_ROOTS,
    _assert_offload_dst_safe,
    _allowed_ingest_roots,
    _assert_ingest_path_safe,
    _assert_same_site,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# R5-25 / #51 router split: route groups peeled from this module into focused
# APIRouter modules under routers/, mounted here. Each is self-contained (imports
# auth/admin/db/state/… + the leaf service modules directly — no server import).
from routers.admin import router as admin_router  # noqa: E402
from routers.settings import router as settings_router  # noqa: E402
from routers.projects import router as projects_router  # noqa: E402
from routers.chat import router as chat_router  # noqa: E402
from routers.cache import router as cache_router  # noqa: E402
from routers.bins import router as bins_router  # noqa: E402
from routers.offload import router as offload_router  # noqa: E402
from routers.analytics import router as analytics_router  # noqa: E402
from routers.retranscribe import router as retranscribe_router  # noqa: E402
from routers.proxy import router as proxy_router  # noqa: E402
from routers.recorrect import router as recorrect_router  # noqa: E402
from routers.misc import router as misc_router  # noqa: E402
from routers.sample import router as sample_router  # noqa: E402
from routers.export import router as export_router  # noqa: E402
from routers.media import router as media_router  # noqa: E402
from routers.search import router as search_router  # noqa: E402
from routers.ingest import router as ingest_router  # noqa: E402
app.include_router(admin_router)
app.include_router(settings_router)
app.include_router(projects_router)
app.include_router(chat_router)
app.include_router(cache_router)
app.include_router(bins_router)
app.include_router(offload_router)
app.include_router(analytics_router)
app.include_router(retranscribe_router)
app.include_router(proxy_router)
app.include_router(recorrect_router)
app.include_router(misc_router)
app.include_router(sample_router)
app.include_router(export_router)
app.include_router(media_router)
app.include_router(search_router)
app.include_router(ingest_router)

ROOT = Path(__file__).parent
# Built Svelte SPA (frontend/dist). Gitignored — produced by `npm run build`.
FRONTEND_DIST = ROOT / "frontend" / "dist"

# Serve thumbnails through an AUTHED route (create dir if missing so it always
# works). Honor ARKIV_THUMBNAILS_DIR (via config.THUMBNAILS_DIR) instead of
# hardcoding ROOT / "thumbnails" — otherwise any deployment that points
# thumbnails elsewhere (test rig, docker, worktree QA) silently 404s every
# /thumbnails/*.jpg.
thumbs_dir = config.THUMBNAILS_DIR
thumbs_dir.mkdir(parents=True, exist_ok=True)


# audit M12: thumbnails were a StaticFiles mount, which bypasses the auth
# dependency entirely — any unauthenticated client could pull every scene
# thumbnail. Serve via an authed route instead: loopback trust still covers the
# local UI token-free; a remote client needs a videos_read token.
@app.get("/thumbnails/{name}")
def serve_thumbnail(
    name: str,
    _tok: dict = Depends(require_scopes("videos_read")),
):
    # basename + containment check: no separators (an encoded %2F decodes into
    # the path param), no hidden/dot names, and the resolved path must stay
    # inside the thumbnails dir (symlink defense).
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(404, "not found")
    try:
        resolved = (thumbs_dir / name).resolve()
        resolved.relative_to(thumbs_dir.resolve())
    except (OSError, ValueError):
        raise HTTPException(404, "not found")
    if not resolved.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(str(resolved))


# R5-25 / #51: path-resolution helpers live in pathres.py (a leaf service module,
# no server state) so the coming APIRouter split can import them without the
# router→server→router import cycle. Re-exported here for backward compat —
# existing call sites and tests that reference `server._resolve_media_path` etc.
# keep working unchanged.
from pathres import (  # noqa: F401 (re-exported for backward compat)
    _basename_safe,
    _looks_absolute,
    _display_path,
    _resolve_record,
    _resolve_frame,
    _resolve_media_path,
    _proxy_ready,
)
# R5-25 / #51: export-format builders (CSV/EDL/FCPXML/SRT/VTT serialisers + the
# timecode/framerate math they share) live in export_builders.py (a leaf service
# module — pure, plus db for the CSV query). Re-exported here for backward compat.
from export_builders import (  # noqa: F401 (re-exported for backward compat)
    _CSV_FORMULA_PREFIXES,
    _csv_safe,
    _parse_frame_tags,
    _build_metadata_csv,
    _edl_reel,
    _media_streams,
    _edl_comment,
    _subtitle_ts,
    _subtitle_text,
    _edl_timecode,
    _start_tc_seconds,
    _edl_fps_warning,
    _fcpxml_rational,
)
# R5-25 / #51: request-input parsers / option builders (the ?ids= query parser +
# the IngestRequest→CLI-flags translator + the whisper language allowlist) live
# in reqopts.py (a leaf — config + fastapi only). Re-exported here for compat.
from reqopts import (  # noqa: F401 (re-exported for backward compat)
    _parse_ids_query,
    _INGEST_LANGUAGES,
    _INGEST_LANGUAGE_CODES,
    _ingest_cmd_opts,
)
# R5-25 / #51: the bulk media-record fetch helpers (_get_tags_bulk H15 /
# _get_light_records_by_ids H16) live in mediarecords.py (a leaf — db only).
# They're shared by the media group (list_media, now in routers/media.py) AND the
# search group (structured_query / search_all, which stay here for a later peel),
# so extracting them breaks the router→server→router cycle. Re-exported here for
# backward compat — call sites / tests referencing server._get_tags_bulk keep working.
from mediarecords import (  # noqa: F401 (re-exported for backward compat)
    _get_tags_bulk,
    _get_light_records_by_ids,
)


# ── Models ───────────────────────────────────────────────────────────────────

# RatingUpdate / TagCreate + the /api/media route group (list_media + the per-clip
# sub-resources) moved to routers/media.py (R5-25 / #51), mounted via
# app.include_router above. The bulk-fetch helpers they shared with the search
# group (_get_tags_bulk / _get_light_records_by_ids) live in mediarecords.py,
# re-exported at the top of this module.


# ProjectCreate + the /api/projects handlers + path sanitisers moved to
# routers/projects.py (R5-25 / #51), mounted via app.include_router below.


# BinCreate / BinItemRef / BinAddItems / BinCopyRequest + the /api/bins handlers
# + the copy helpers moved to routers/bins.py (R5-25 / #51), mounted below.


# CreateTokenRequest + the /api/admin/tokens handlers moved to routers/admin.py
# (R5-25 / #51 router split), mounted via app.include_router below.


# ChatRequest / ChatResponse + the /api/chat handlers + _chat_owner_filter moved
# to routers/chat.py (R5-25 / #51), mounted via app.include_router below.


# _split_csv moved to routers/search.py (R5-25 / #51) with its only caller
# /api/search/all.


def _bootstrap_admin_token():
    raw = admin.bootstrap_admin_token_if_empty()
    if raw:
        print("=" * 70, flush=True)
        print("[BOOTSTRAP] Admin token seeded from ARKIV_ADMIN_BOOTSTRAP_TOKEN env.", flush=True)
        print("            Token name: 'bootstrap', scope: ['admin']", flush=True)
        print("            Generate per-machine tokens via POST /api/admin/tokens,", flush=True)
        print("            then unset ARKIV_ADMIN_BOOTSTRAP_TOKEN + revoke 'bootstrap'.", flush=True)
        print("=" * 70, flush=True)
        return

    with db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM access_tokens").fetchone()["c"]
    if count == 0:
        print("=" * 70, flush=True)
        print("[BOOTSTRAP] No access tokens in DB and ARKIV_ADMIN_BOOTSTRAP_TOKEN unset.", flush=True)
        print("            API endpoints will return 401. To bootstrap:", flush=True)
        print("              1. export ARKIV_ADMIN_BOOTSTRAP_TOKEN=$(openssl rand -base64 32)", flush=True)
        print("              2. restart server (it will seed admin token)", flush=True)
        print("              3. POST /api/admin/tokens to create per-machine tokens", flush=True)
        print("=" * 70, flush=True)


# ── API Routes ───────────────────────────────────────────────────────────────

# The /api/search/all (federated) + /api/search/query (structured) routes, with
# StructuredQuery / _structured_sort_key / _split_csv, moved to routers/search.py
# (R5-25 / #51), mounted via app.include_router below. The shared bulk-fetch
# helpers live in mediarecords.py; path sanitisation in pathres.py.


# _project_paths_visible / _sanitize_project_paths + the 5 /api/projects handlers
# moved to routers/projects.py (R5-25 / #51), mounted via app.include_router below.


# --- Cross-library 精選集 (bins): persistent named selections spanning projects ---
# Storage is ~/.arkiv-bins.json (bins.py), separate from any project.db. Items
# reference clips by (project_name, media_id) — never mutating the source library.

# _unique_dest / _copy_clip_verified / _bin_detail_payload + the 8 /api/bins
# handlers moved to routers/bins.py (R5-25 / #51), mounted via include_router.


# --- Phase 9.7 G5②: persisted settings (curated key/value overrides) ---

# The /api/settings group (_resolve_settings_scope + SettingsUpdate + the 3
# handlers) moved to routers/settings.py (R5-25 / #51), mounted via
# app.include_router below.


# The /api/media route group — media_position, media_pool, list_media (+ its
# semantic/SQL search branches) — moved to routers/media.py (R5-25 / #51), mounted
# via app.include_router above. _VIDEO_EXTS/_AUDIO_EXTS (re-derived from mediatypes
# there) and the shared bulk-fetch helpers (now mediarecords.py) went with them.


# get_media_detail + the per-clip sub-resources (waveform/scenes/chapters/rating/
# tags GET+POST+DELETE) moved to routers/media.py (R5-25 / #51), mounted above.


# The dashboard analytics routes (/api/stats, /api/tags, /api/collections,
# /api/duration-by-lang, /api/size-by-ext) — with _current_project_registry_name
# and _thumb_url — now live in routers/analytics.py (R5-25 / #51).


# ── Metadata Export (Phase 7.6) ──────────────────────────────────────────────
#
# All six export routes (/api/export/metadata-csv[-to], /api/media/{id}/export/{fmt},
# /api/media/{id}/export-to, /api/export/batch, /api/export/timeline/{fmt}) with
# _attachment_headers moved to routers/export.py (R5-25 / #51), mounted via
# app.include_router below. The CSV/EDL/subtitle/FCPXML serialisers live in
# export_builders.py; _parse_ids_query in reqopts.py; the export-dest guard in
# webguard.py — all imported by the router directly.


# ── Ingest ────────────────────────────────────────────────────────────────────
#
# IngestRequest / ScanRequest + MEDIA_EXTS/VIDEO_EXTS/AUDIO_EXTS/
# UNSUPPORTED_STILL_EXTS + _build_scan_manifest + the /api/ingest family
# (/engines, /scan, POST /api/ingest, /api/media/{id}/reingest) AND the WS
# progress channel (_ws_authorized, /ws/ingest, _run_ingest_with_ws /
# _on_ingest_ws_done / _ingest_ws_tasks, POST /api/ingest/ws) moved to
# routers/ingest.py (R5-25 / #51), mounted via app.include_router above. The
# shared single-flight slot + broadcaster (_acquire_ingest_slot /
# _release_ingest_slot / ingest_ws) live in state.py; _ingest_cmd_opts /
# _INGEST_LANGUAGES in reqopts.py; _assert_ingest_path_safe in webguard.py —
# all imported by the router directly.


# ── DIT Offload (card → backup) — powers the /dit UI ─────────────────────────

# OffloadPreviewRequest / OffloadRequest + the /api/offload* handlers + /dit
# redirect moved to routers/offload.py (R5-25 / #51), mounted via include_router.


# ── Re-transcribe ─────────────────────────────────────────────────────────────

# RetranscribeRequest / ActivateLangRequest + the single-clip /api/media/{id}
# retranscribe, remotion-props, transcripts (list + activate) and retry-vision
# routes moved to routers/media.py (R5-25 / #51), mounted via app.include_router
# above. (The BATCH /api/retranscribe-all lives in routers/retranscribe.py.)


# The /api/media/{id}/reingest handler (holds the H3 ingest slot) moved to
# routers/ingest.py (R5-25 / #51) with the rest of the ingest family, mounted via
# app.include_router above.


# ── Cache Management ──────────────────────────────────────────────────────────

# _dir_size_mb + the /api/cache/info + /api/cache/clear handlers moved to
# routers/cache.py (R5-25 / #51), mounted via app.include_router below.


# ── Export ────────────────────────────────────────────────────────────────────
#
# The export routes + _attachment_headers moved to routers/export.py (R5-25 / #51).
# Their format serialisers (_edl_reel / _media_streams / _edl_comment /
# _subtitle_ts / _subtitle_text / _edl_timecode / _start_tc_seconds /
# _edl_fps_warning / _fcpxml_rational) live in export_builders.py; _log_safe moved
# to routers/misc.py with /api/client-log.


# ── WebSocket: Ingest Progress ───────────────────────────────────────────
#
# _ws_authorized (CSWSH/origin + loopback-trust handshake auth), the
# @router.websocket("/ws/ingest") endpoint, the _run_ingest_with_ws worker with
# its _on_ingest_ws_done callback + _ingest_ws_tasks task-ref set, and the
# POST /api/ingest/ws trigger moved to routers/ingest.py (R5-25 / #51) with the
# rest of the ingest family, mounted via app.include_router above. _ALLOWED_ORIGINS
# lives in webguard.py; the shared broadcaster (ingest_ws) + single-flight slot in
# state.py — all imported by the router directly.


# (Svelte cutover Phase 3) The Tailwind CDN proxy + /tailwind-static.css routes
# were only consumed by the retired legacy index.html — removed with it. The
# Svelte SPA ships its own bundled CSS under /assets.


# ── Video Streaming ──────────────────────────────────────────────────────────
#
# /api/stream (+ /api/embed/rebuild, /api/open-file, /api/client-log with its
# _log_safe sanitiser) moved to routers/misc.py (R5-25 / #51), mounted via
# app.include_router below. _rebuild_embeddings lives in state.py (shared by the
# embed route + the recorrect rebuild chain), re-exported at the top of this module.


# ── Phase 9.6b: per-project correction dictionary ────────────────────────────
#
# The correction-dictionary + recorrect routes (/api/corrections GET/PUT,
# /api/recorrect POST, /api/recorrect/backups GET, /api/recorrect/revert POST)
# with CorrectionsBody / RevertBody moved to routers/recorrect.py (R5-25 / #51),
# mounted via app.include_router below.


# ── Phase 9.6d: project-wide batch retranscribe (2a) ─────────────────────────
#
# The /api/retranscribe-all POST + /status poller + _run_retranscribe_all worker
# (RetranscribeAllRequest with its ISO-639 validator, the two-lock ordering) moved
# to routers/retranscribe.py (R5-25 / #51), mounted via app.include_router below.


# ── Serve Frontend ───────────────────────────────────────────────────────────

# Serve the built Svelte SPA (frontend/dist/index.html). The SPA is hash-routed
# (svelte-spa-router) so "/" is the only HTML entry the browser ever requests —
# no catch-all fallback needed; /assets/* is a StaticFiles mount below. Read fresh
# each request (no cache), matching the previous dev behaviour.
#
# Svelte cutover Phase 3 (2026-06-26): the legacy Tailwind index.html + the
# ARKIV_UI=legacy escape hatch are retired — the SPA is the only UI. A missing
# build now surfaces a clear "run npm run build" message instead of silently
# falling back to a page that no longer exists.
def _load_index() -> str:
    spa = FRONTEND_DIST / "index.html"
    if spa.exists():
        return spa.read_text(encoding="utf-8")
    return (
        "<h1>arkiv</h1><p>UI build not found. Run "
        "<code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code>.</p>"
    )


@app.get("/", response_class=HTMLResponse)
def serve_index():
    return _load_index()


# (Svelte cutover Phase 3) The /legacy route + the old Tailwind index.html it
# served are retired — the SPA is the only UI now.


# Built SPA assets (frontend/dist/assets/*-<hash>.js|css, referenced as /assets/…
# by the built index.html). Mounted only when the build exists; without it "/"
# returns a clear "run npm run build" message instead.
# Registered last → never shadows the explicit /api, /thumbnails, /dit routes.
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="spa-assets")
