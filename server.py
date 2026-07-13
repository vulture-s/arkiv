"""
Media Asset Manager — FastAPI Backend
Serves the UI (index.html) and provides REST API for all CRUD operations.

Usage:
    uvicorn server:app --host 0.0.0.0 --port 8501
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Literal, Optional, Set

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

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
import mediatypes
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
from routers.export import router as export_router  # noqa: E402
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
app.include_router(export_router)

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


# ── Models ───────────────────────────────────────────────────────────────────

class RatingUpdate(BaseModel):
    # Constrain to the stored vocabulary (or None to clear) — an arbitrary string
    # used to be persisted verbatim, corrupting rating stats and sort buckets.
    rating: Optional[Literal["good", "ng", "review"]] = None
    note: Optional[str] = None


class TagCreate(BaseModel):
    name: str
    # Public callers may not mint 'auto' tags — those are owned by the vision
    # pipeline and wiped on re-ingest, so a client-supplied source='auto' tag
    # would silently vanish. Force 'manual'.
    source: Literal["manual"] = "manual"

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        # strip control chars (newlines etc.), collapse whitespace, reject empty,
        # cap length — an empty/whitespace/control-char/huge tag used to be stored.
        cleaned = " ".join("".join(c for c in (v or "") if c == " " or ord(c) >= 0x20 and c not in "\x7f").split())
        if not cleaned:
            raise ValueError("tag name must not be empty")
        if len(cleaned) > 100:
            raise ValueError("tag name too long (max 100)")
        return cleaned
 
 
# ProjectCreate + the /api/projects handlers + path sanitisers moved to
# routers/projects.py (R5-25 / #51), mounted via app.include_router below.


# BinCreate / BinItemRef / BinAddItems / BinCopyRequest + the /api/bins handlers
# + the copy helpers moved to routers/bins.py (R5-25 / #51), mounted below.


# CreateTokenRequest + the /api/admin/tokens handlers moved to routers/admin.py
# (R5-25 / #51 router split), mounted via app.include_router below.


# ChatRequest / ChatResponse + the /api/chat handlers + _chat_owner_filter moved
# to routers/chat.py (R5-25 / #51), mounted via app.include_router below.


def _split_csv(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    parts = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts or None


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

@app.get("/api/search/all")
def search_all(
    q: str = Query(..., alias="q"),
    # audit H13: declarative bounds — limit=-1 silently truncated, and
    # timeout=999999 parked a threadpool worker on a stalled peer for hours.
    limit: int = Query(50, ge=1, le=500),
    per_project_limit: int = Query(20, ge=1, le=100),
    projects: Optional[str] = None,
    tag: Optional[str] = None,
    timeout: float = Query(10.0, gt=0.0, le=30.0),
    no_fallback_sql: bool = False,
    _tok: dict = Depends(require_scopes("videos_read")),
):
    try:
        payload = federation.search_all_projects(
            q,
            limit=limit,
            per_project_limit=per_project_limit,
            project_names=_split_csv(projects),
            tag=tag,
            timeout=timeout,
            fallback_sql=not no_fallback_sql,
        )
    except project_registry.RegistryError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Phase 16.2: federation results carry absolute media + project paths; strip
    # them at the API boundary so a videos_read client can't map the operator's
    # cross-project directory layout. project_path is reduced to its folder
    # basename; the internal absolute_path / relative_path fields are dropped so
    # only the sanitized `path` survives.
    def _basename_only(value):
        return _basename_safe(value)

    for item in payload.get("items", []) or []:
        # Route relative_path through _display_path FIRST: for an out-of-root row
        # federation's relative_path is actually the *absolute* path (it falls
        # back to str(stored) when relative_to() fails), so it must be basenamed,
        # never copied through. Then drop the internal absolute/relative fields —
        # leaving relative_path in place was the residual leak this closes.
        chosen = item.get("relative_path")
        if chosen is None:
            chosen = item.get("path") or ""
        item["path"] = _display_path(chosen)
        item.pop("absolute_path", None)
        item.pop("relative_path", None)
        if item.get("project_path"):
            item["project_path"] = _basename_only(item["project_path"])

    # Errors carry the absolute project_path on timeout / preflight failure
    # (federation sets project_path=str(project.path)); a failed project must not
    # leak its absolute root either.
    for err in payload.get("errors", []) or []:
        if err.get("project_path"):
            err["project_path"] = _basename_only(err["project_path"])

    status_code = 200
    if payload.get("projects_queried") and payload.get("projects_failed", 0) >= payload.get("projects_queried", 0):
        status_code = 207
    return JSONResponse(content=payload, status_code=status_code)


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


@app.get("/api/media/position/{media_id}")
def media_position(
    media_id: int,
    sort: str = "date",
    lang: Optional[str] = None,
    rating: Optional[str] = None,
    media_type: Optional[str] = None,
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """Find the row offset of a media item in the current sort/filter view."""
    filters = {}
    if lang:
        filters["lang"] = lang
    if rating:
        filters["rating"] = rating
    if media_type:
        filters["media_type"] = media_type
    where, params = db._build_filter_clause(**filters)
    order = db.SORT_MAP.get(sort, "id")
    # audit L12: single window-function query instead of fetching the whole view
    # into Python; and a clip NOT in the current view now says so explicitly
    # (in_view=false) instead of a fake offset 0 that is indistinguishable from
    # genuinely being first. offset stays 0 in that case so the existing UI
    # fallback (jump to page 0) keeps working.
    with db.get_conn() as conn:
        row = conn.execute(
            f"SELECT pos FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY {order}) - 1 AS pos "
            f"FROM media WHERE {where}) ranked WHERE id = ?",
            (*params, media_id),
        ).fetchone()
    if row is None:
        return {"id": media_id, "offset": 0, "in_view": False}
    return {"id": media_id, "offset": row["pos"], "in_view": True}


@app.get("/api/media/pool")
def media_pool(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """Lightweight full list for left sidebar media pool — grouped by folder."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, ext, duration_s, rating, path FROM media ORDER BY path, filename"
        ).fetchall()
    items = []
    for r in rows:
        p = r["path"] or ""
        # Use parent directory name as folder; skip generic names like "reels"
        parts = p.replace("\\", "/").rstrip("/").split("/")
        folder = parts[-2] if len(parts) >= 2 else ""
        if folder.lower() in ("reels", "clips", "raw", "media", "footage"):
            folder = parts[-3] if len(parts) >= 3 else folder
        items.append({
            "id": r["id"],
            "filename": r["filename"],
            "ext": r["ext"],
            "duration_s": r["duration_s"],
            "rating": r["rating"],
            "folder": folder,
        })
    return {"items": items, "total": len(items)}


# audit H14: ext buckets mirror db._build_filter_clause's media_type sets so the
# search branch applies the SAME filter the SQL (non-search) path does. R5-24:
# both now come from the shared mediatypes source, so they can't drift apart.
_VIDEO_EXTS = mediatypes.VIDEO_EXT
_AUDIO_EXTS = mediatypes.AUDIO_EXT


def _get_tags_bulk(media_ids) -> dict:
    """audit H15: tags for many media ids in ONE query — the per-row db.get_tags
    loop opened a fresh SQLite connection per record (501 connections for a
    500-row page). Returns {media_id: [tag dicts]} matching db.get_tags shape."""
    out = {int(m): [] for m in media_ids}
    if not out:
        return out
    ids = list(out.keys())
    placeholders = ",".join("?" * len(ids))
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, media_id, name, source FROM tags "
            "WHERE media_id IN ({0}) ORDER BY name".format(placeholders),
            ids,
        ).fetchall()
    for r in rows:
        out[r["media_id"]].append({"id": r["id"], "name": r["name"], "source": r["source"]})
    return out


def _get_light_records_by_ids(media_ids) -> list:
    """audit H16: LIGHT_COLS records for many ids in ONE query, preserving input
    order. The semantic-search path did a per-hit SELECT * (words_json /
    segments_json — tens of MB across a page) plus one connection per hit."""
    ids = [int(m) for m in media_ids]
    if not ids:
        return []
    # fable-audit round-5 #21: chunk the IN() so a broad structured/semantic query
    # (thousands of matching ids) doesn't blow past SQLite's max variable count
    # (999 on old builds) → "too many SQL variables" HTTP 500.
    by_id = {}
    _CHUNK = 500
    with db.get_conn() as conn:
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start:start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT {db.LIGHT_COLS} FROM media WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            for r in rows:
                by_id[r["id"]] = dict(r)
    return [by_id[i] for i in ids if i in by_id]


@app.get("/api/media")
def list_media(
    offset: int = 0,
    limit: int = 50,
    sort: str = "date",
    lang: Optional[str] = None,
    rating: Optional[str] = None,
    media_type: Optional[str] = None,
    q: Optional[str] = None,
    ids: Optional[str] = None,
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """List media with filters, sorting, and pagination."""
    # Clamp pagination: a negative limit becomes SQLite LIMIT -1 (unbounded full
    # dump) and a huge limit blows up the vector-search n_results.
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    # Deep-link from chat: #/main-live?ids=2,5,9 shows EXACTLY the relevant clips
    # chat returned (a filtered subset), in order. _parse_ids_query raises 400 on
    # malformed ids and returns None when no filter is requested (absent/empty ?ids=,
    # same convention as the export endpoints); [] means "filter, but no rows". Cap
    # so a huge ?ids can't unbound the batched fetch (H16).
    id_list = _parse_ids_query(ids)
    if id_list is not None:
        records = _get_light_records_by_ids(id_list[:500])
        for rec in records:
            _resolve_record(rec)
        tags_by_id = _get_tags_bulk([rec["id"] for rec in records])
        for rec in records:
            rec["tags"] = tags_by_id.get(rec["id"], [])
        return {"items": records, "total": len(records), "search": True}
    if q:
        enriched = []
        search_warning = None
        import vectordb as vdb
        # audit M19: search used to ignore `offset` entirely (page 2 == page 1).
        # Collect up to offset+limit matches, then slice the requested page.
        # Capped so a huge ?offset can't inflate n_results / SQL LIMIT (audit
        # H13-class DoS); search hits past 2000 are noise anyway.
        needed = min(offset + limit, 2000)

        def _passes_filters(rec: dict) -> bool:
            # Applied to the ENRICHED record (which has real lang/rating), not the
            # raw vector hit — vectordb results carry no `rating`, so filtering on
            # them dropped every semantic hit under `rating=good` and silently fell
            # through to an unfiltered SQL LIKE (H8).
            if lang and rec.get("lang") != lang:
                return False
            rv = rec.get("rating")
            if rating == "unrated" and rv is not None:
                return False
            if rating and rating != "unrated" and rv != rating:
                return False
            # audit H14: media_type was silently ignored on the search branch
            # (?q=...&media_type=video still returned audio). Mirror the SQL
            # path's ext buckets.
            ext = (rec.get("ext") or "").lower()
            if media_type == "video" and ext not in _VIDEO_EXTS:
                return False
            if media_type == "audio" and ext not in _AUDIO_EXTS:
                return False
            return True

        # Try semantic search first (requires vectordb with embeddings)
        try:
            raw = vdb.search(q, n_results=needed * 3)
            seen = set()
            ordered_ids = []
            hit_by_id = {}
            for r in raw:
                mid = int(r["media_id"])
                if mid in seen:
                    continue
                seen.add(mid)
                ordered_ids.append(mid)
                hit_by_id[mid] = r
            # audit H16: one batched LIGHT_COLS fetch instead of per-hit SELECT *
            for rec in _get_light_records_by_ids(ordered_ids):
                if not _passes_filters(rec):
                    continue
                _resolve_record(rec)
                hit = hit_by_id[rec["id"]]
                rec["score"] = hit.get("score", 0)
                rec["excerpt"] = hit.get("excerpt", "")
                enriched.append(rec)
                if len(enriched) >= needed:
                    break
        except vdb.EmbeddingDimensionMismatch as exc:
            # Don't silently SQL-degrade a dim mismatch — log it and surface a hint
            # so the operator knows semantic search is off until they rebuild.
            _logging.getLogger(__name__).warning("semantic search degraded: %s", exc)
            search_warning = str(exc)
        except Exception as exc:
            # audit L8: was a bare `except: pass` — Ollama being down degraded to
            # SQL search with zero signal anywhere. Log + flag the degradation.
            _logging.getLogger(__name__).warning(
                "semantic search failed, falling back to SQL: %s", exc
            )
            search_warning = "semantic search unavailable (SQL fallback used)"

        # Fallback: SQL text search (filename, transcript, tags) — same lang/rating
        # filter applied so a degraded search still honors the active filters.
        if not enriched:
            seen_ids = set()
            like = f"%{q}%"
            # audit H17: push the active filters into WHERE and bound the scan —
            # the old query LIKE-scanned the whole table, built every matching
            # record, then threw away everything past `limit`.
            filter_sql = ""
            filter_params: list = []
            if lang:
                filter_sql += " AND lang = ?"
                filter_params.append(lang)
            if rating == "unrated":
                filter_sql += " AND rating IS NULL"
            elif rating:
                filter_sql += " AND rating = ?"
                filter_params.append(rating)
            if media_type == "video":
                filter_sql += " AND ext IN ({0})".format(",".join("?" * len(_VIDEO_EXTS)))
                filter_params.extend(sorted(_VIDEO_EXTS))
            elif media_type == "audio":
                filter_sql += " AND ext IN ({0})".format(",".join("?" * len(_AUDIO_EXTS)))
                filter_params.extend(sorted(_AUDIO_EXTS))
            with db.get_conn() as conn:
                rows = conn.execute(
                    f"SELECT {db.LIGHT_COLS} FROM media "
                    "WHERE (filename LIKE ? OR transcript LIKE ?)" + filter_sql +
                    " ORDER BY id LIMIT ?",
                    (like, like, *filter_params, needed),
                ).fetchall()
                for r in rows:
                    rec = dict(r)
                    _resolve_record(rec)
                    enriched.append(rec)
                    seen_ids.add(rec["id"])

            # Also search by tag name (bounded — audit H17)
            if len(enriched) < needed:
                with db.get_conn() as conn:
                    tag_rows = conn.execute(
                        "SELECT DISTINCT media_id FROM tags WHERE name LIKE ? LIMIT ?",
                        (like, needed * 3),
                    ).fetchall()
                tag_ids = [tr["media_id"] for tr in tag_rows if tr["media_id"] not in seen_ids]
                for rec in _get_light_records_by_ids(tag_ids):
                    if not _passes_filters(rec):
                        continue
                    _resolve_record(rec)
                    enriched.append(rec)
                    seen_ids.add(rec["id"])
                    if len(enriched) >= needed:
                        break

        # audit M19: slice the requested page; total = bounded match count (the
        # same "items seen so far" semantic for both search sub-paths).
        items = enriched[offset:offset + limit]
        # audit H15: one bulk tag query for the returned page only
        tags_by_id = _get_tags_bulk([rec["id"] for rec in items])
        for rec in items:
            rec["tags"] = tags_by_id.get(rec["id"], [])
        resp = {"items": items, "total": len(enriched), "search": True}
        if search_warning:
            resp["search_degraded"] = True
            resp["warning"] = search_warning
        return resp

    filters = {}
    if lang:
        filters["lang"] = lang
    if rating:
        filters["rating"] = rating
    if media_type:
        filters["media_type"] = media_type

    records, total = db.get_media_filtered(
        offset=offset, limit=limit, sort=sort, **filters,
    )
    # Attach tags to each record — one bulk query for the page instead of one
    # SQLite connection per row (audit H15).
    tags_by_id = _get_tags_bulk([rec["id"] for rec in records])
    for rec in records:
        _resolve_record(rec)
        rec["tags"] = tags_by_id.get(rec["id"], [])

    return {"items": records, "total": total, "search": False}


class StructuredQuery(BaseModel):
    # Phase 9.7 G6: structured query — AND/OR over typed field conditions, with
    # an optional semantic (vector) leg. `conditions` is validated by
    # query_builder.compile_spec; we keep the model permissive and let the
    # builder raise the precise error.
    match: str = "all"
    conditions: List[dict]
    limit: int = 50
    offset: int = 0
    sort: str = "date"


def _structured_sort_key(sort: str):
    if sort == "duration":
        return lambda r: (r.get("duration_s") or 0), True
    if sort == "size":
        return lambda r: (r.get("size_mb") or 0), True
    if sort == "name":
        return lambda r: (r.get("filename") or "").lower(), False
    # default: most recent first
    return lambda r: (r.get("processed_at") or ""), True


@app.post("/api/search/query")
def structured_query(
    body: StructuredQuery,
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """Structured query: typed field conditions combined by AND/OR, with an
    optional semantic leg run through the vector index. Returns the same
    `{items, total, search}` shape as /api/media so the UI can reuse renderers."""
    import query_builder

    try:
        compiled = query_builder.compile_spec(
            {"match": body.match, "conditions": body.conditions}
        )
    except query_builder.QueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    where, params = compiled["where"], compiled["params"]
    terms, match = compiled["semantic_terms"], compiled["match"]
    warning = None

    sql_ids = None
    if where:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id FROM media WHERE " + where, params
            ).fetchall()
        sql_ids = {r["id"] for r in rows}

    sem_ids = None
    if terms:
        import vectordb as vdb
        sets = []
        for term in terms:
            try:
                raw = vdb.search(term, n_results=2000)
                sets.append({int(r["media_id"]) for r in raw})
            except Exception as exc:  # Ollama down / dim mismatch → degrade, flag it
                _logging.getLogger(__name__).warning(
                    "structured query semantic leg failed: %s", exc
                )
                warning = "semantic search unavailable (some terms ignored)"
                sets.append(set())
        if sets:
            sem_ids = set.intersection(*sets) if match == "all" else set.union(*sets)

    if sql_ids is not None and sem_ids is not None:
        final_ids = (sql_ids & sem_ids) if match == "all" else (sql_ids | sem_ids)
    elif sql_ids is not None:
        final_ids = sql_ids
    else:
        final_ids = sem_ids or set()

    records = _get_light_records_by_ids(list(final_ids))
    keyfn, reverse = _structured_sort_key(body.sort)
    records.sort(key=keyfn, reverse=reverse)

    total = len(records)
    offset = max(0, body.offset)
    limit = max(1, min(500, body.limit))
    items = records[offset:offset + limit]
    tags_by_id = _get_tags_bulk([rec["id"] for rec in items])
    for rec in items:
        _resolve_record(rec)
        rec["tags"] = tags_by_id.get(rec["id"], [])

    resp = {"items": items, "total": total, "search": True, "structured": True}
    if warning:
        resp["search_degraded"] = True
        resp["warning"] = warning
    return resp


@app.get("/api/media/{media_id}")
def get_media_detail(
    media_id: int,
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """Get full media record with tags and frames."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    _resolve_record(rec)
    # Structured frame analysis data
    rec["frames"] = [_resolve_frame(frame) for frame in db.get_frames(media_id)]
    if rec.get("editability_score") is None:
        for frame in rec["frames"]:
            if frame.get("focus_score") is not None:
                rec["editability_score"] = db.compute_editability(frame)
                break
    # Legacy frame_tags_parsed for backwards compat
    if rec.get("frame_tags"):
        try:
            rec["frame_tags_parsed"] = json.loads(rec["frame_tags"])
        except Exception:
            rec["frame_tags_parsed"] = []
    # User-facing tags: screen quality-defect noise + keep only the top-N most
    # confident (focus-weighted) tags, so a clip doesn't dump 10+ tags on the user.
    top = set(tag_quality.rank_media_tags(rec.get("frame_tags_parsed") or []))
    all_tags = tag_quality.filter_tag_records(db.get_tags(media_id))
    rec["tags"] = [t for t in all_tags if t["name"] in top] if top else all_tags
    # Optional LLM-canonicalized tag list (populated by `ingest.py --canonicalize-tags`).
    # Returned alongside raw tags so the UI can toggle raw ↔ canonical; null until run.
    if rec.get("canonical_tags"):
        try:
            rec["canonical_tags"] = json.loads(rec["canonical_tags"])
        except Exception:
            rec["canonical_tags"] = None
    # fable-audit round-5 #26 (codex-verified): words_json (word-level timing JSON,
    # multi-MB) has NO frontend consumer here — the inspector's transcript seek uses
    # segments_json (kept), and word-level data is served separately via
    # /api/media/{id}/remotion-props. Drop only words_json from this per-click
    # response so it isn't shipped over NAS/Tailscale on every arrow-key. The shared
    # db.get_record_by_id is untouched (export/retranscribe/remotion still need it).
    rec.pop("words_json", None)
    return rec


@app.get("/api/media/{media_id}/waveform")
def get_media_waveform(
    media_id: int,
    bins: int = 60,
    _tok: dict = Depends(require_scopes("media_read")),
):
    """Return pre-computed audio peaks (0..1) for the inspector waveform.
    Cached per (id, bins) under waveforms/<id>_<bins>.json."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    # Clamp BEFORE the no-audio early return — otherwise ?bins=999999 on a
    # no-audio clip allocates a ~8MB [0.0]*999999 list (DoS).
    bins = max(8, min(500, bins))
    if not rec.get("has_audio"):
        return {"media_id": media_id, "bins": bins, "peaks": [0.0] * bins}
    cache_dir = ROOT / "waveforms"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{media_id}_{bins}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache_path.unlink(missing_ok=True)
    file_path = Path(_resolve_media_path(rec["path"]))
    if not file_path.exists():
        raise HTTPException(404, "找不到檔案")
    peaks = _compute_waveform(str(file_path), bins)
    if peaks is None:
        raise HTTPException(500, "波形計算失敗")
    payload = {"media_id": media_id, "bins": bins, "peaks": peaks}
    try:
        # audit L5: atomic write (tmp + os.replace) — a direct write_text could
        # leave a concurrent reader a torn/partial JSON. pid-suffixed tmp name so
        # two concurrent writers don't tear each other's tmp file either.
        tmp_path = cache_path.with_name("{0}.{1}.tmp".format(cache_path.name, os.getpid()))
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp_path, cache_path)
    except Exception:
        pass
    return payload


def _compute_waveform(path: str, bins: int):
    """Decode mono 8kHz PCM via ffmpeg and return `bins` peak-amplitude values 0..1."""
    import subprocess
    import numpy as np
    cmd = [
        config.FFMPEG_PATH, "-v", "quiet", "-i", path,
        "-ac", "1", "-ar", "8000", "-f", "s16le", "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0 or not r.stdout:
            return None
        samples = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return [0.0] * bins
        splits = np.array_split(samples, bins)
        return [float(np.abs(s).max()) / 32768.0 if s.size else 0.0 for s in splits]
    except Exception:
        return None


@app.get("/api/media/{media_id}/scenes")
def get_media_scenes(
    media_id: int,
    _tok: dict = Depends(require_scopes("media_read")),
):
    # Per-scene shape (breaking change 2026-06-29): each scene = one scene-detect
    # boundary persisted in frames table, with computed end_s from the next
    # frame's start (or media.duration_s for the last). Consumers (smart-edit,
    # OpenMontage arkiv_clip_search, Vyra, Palmier) expect start/end/duration +
    # vision metadata per scene, not per-frame flat list.
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    frames = db.get_frames(media_id)
    media_duration_s = float(rec.get("duration_s") or 0.0)
    scenes = []
    for i, frame in enumerate(frames):
        start_s = float(frame["timestamp_s"])
        if i + 1 < len(frames):
            end_s = float(frames[i + 1]["timestamp_s"])
        else:
            end_s = media_duration_s
        if end_s < start_s:
            end_s = start_s
        scene = {
            "scene_index": frame["frame_index"],
            "start_s": start_s,
            "end_s": end_s,
            "duration_s": end_s - start_s,
            "description": frame.get("description", ""),
            "content_type": frame.get("content_type"),
            "focus_score": frame.get("focus_score"),
            "atmosphere": frame.get("atmosphere"),
            "energy": frame.get("energy"),
            "edit_position": frame.get("edit_position"),
            "edit_reason": frame.get("edit_reason"),
            "stability": frame.get("stability"),
            "exposure": frame.get("exposure"),
            "audio_quality": frame.get("audio_quality"),
        }
        if frame.get("thumbnail_path"):
            scene["keyframe_url"] = "/thumbnails/{0}".format(
                Path(_resolve_media_path(frame["thumbnail_path"])).name
            )
        scenes.append(scene)
    return {
        "media_id": media_id,
        "media_duration_s": media_duration_s,
        "scenes": scenes,
        "total": len(scenes),
    }


@app.get("/api/media/{media_id}/chapters")
def get_media_chapters(
    media_id: int,
    format: str = "youtube",
    _tok: dict = Depends(require_scopes("media_read")),
):
    """ProChapter-style chapter markers from the clip's scene frames.

    `format=youtube`   → `MM:SS Title` lines (first marker forced to 0:00).
    `format=ffmetadata` → ffmpeg chapter file (embed with -map_metadata).
    """
    if format not in ("youtube", "ffmetadata"):
        raise HTTPException(422, "format must be 'youtube' or 'ffmetadata'")
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    import export
    text = export.build_chapters(media_id, fmt=format)
    count = text.count("[CHAPTER]") if format == "ffmetadata" else (len(text.splitlines()) if text else 0)
    return {"media_id": media_id, "format": format, "chapters": text, "count": count}


@app.patch("/api/media/{media_id}/rating")
def update_rating(
    media_id: int,
    body: RatingUpdate,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Set or clear rating for a media asset.

    PATCH semantics (audit M20): a field OMITTED from the body is left
    untouched; an explicit null clears it. PATCH {rating:'good'} used to
    silently wipe the stored note (PUT semantics in a PATCH endpoint).
    """
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    provided = body.model_fields_set  # audit M20: omitted vs explicit-null
    sets, params = [], []
    if "rating" in provided:
        sets.append("rating = ?")
        params.append(body.rating)
    if "note" in provided:
        sets.append("rating_note = ?")
        params.append(body.note)
    if sets:
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE media SET {0} WHERE id = ?".format(", ".join(sets)),
                (*params, media_id),
            )
    new_rating = body.rating if "rating" in provided else rec.get("rating")
    new_note = body.note if "note" in provided else rec.get("rating_note")
    return {"ok": True, "rating": new_rating, "note": new_note}


@app.get("/api/media/{media_id}/tags")
def get_tags(
    media_id: int,
    _tok: dict = Depends(require_scopes("videos_read")),
):
    return db.get_tags(media_id)


@app.post("/api/media/{media_id}/tags")
def add_tag(
    media_id: int,
    body: TagCreate,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    db.add_tag(media_id, body.name, body.source)
    return {"ok": True, "tags": db.get_tags(media_id)}


@app.delete("/api/media/{media_id}/tags/{tag_name}")
def remove_tag(
    media_id: int,
    tag_name: str,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    db.remove_tag(media_id, tag_name)
    return {"ok": True, "tags": db.get_tags(media_id)}


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

class IngestRequest(BaseModel):
    path: str
    limit: int = 0
    # ingest.py engine options, surfaced so the redesign's ingest setup dialog
    # (docs/design/redesign-2026 op-01) has a real backend to bind to. Each
    # defaults to the pre-existing behaviour, so existing callers are unchanged.
    skip_vision: bool = False
    refresh: bool = False
    recursive: bool = False
    max_failures: int = 0
    skip_failed: bool = False
    no_embed: bool = False
    # brick 4: transcription engine knobs. whisper_guard = quality preset 0-4
    # (None = default); language = forced whisper code (zh/en/ja/ko, None = auto).
    whisper_guard: Optional[int] = None
    language: Optional[str] = None


# _INGEST_LANGUAGES / _INGEST_LANGUAGE_CODES / _ingest_cmd_opts moved to
# reqopts.py (R5-25 / #51), re-exported at the top of this module.


class ScanRequest(BaseModel):
    path: str

MEDIA_EXTS = mediatypes.MEDIA_EXT
VIDEO_EXTS = mediatypes.VIDEO_EXT
AUDIO_EXTS = mediatypes.AUDIO_EXT
assert VIDEO_EXTS | AUDIO_EXTS == MEDIA_EXTS  # the two must partition MEDIA_EXTS
# camera raw / stills the ingest pipeline does not process — surfaced in the scan
# manifest as "skipped" so the redesign's setup dialog (op-01) can show what will
# not be ingested (e.g. a card of .mov clips + .crw stills).
UNSUPPORTED_STILL_EXTS = {".crw", ".cr2", ".cr3", ".arw", ".nef", ".dng",
                          ".raf", ".orf", ".rw2", ".heic", ".heif", ".tif", ".tiff"}


def _build_scan_manifest(files: list, unsupp: dict) -> dict:
    """Aggregate a scanned file list into op-01's MANIFEST panel: counts + sizes
    by category (video / audio) plus the unsupported-stills skip count."""
    def cat(exts: set) -> dict:
        sub = [f for f in files if Path(f["name"]).suffix.lower() in exts]
        return {"count": len(sub), "size_mb": round(sum(f["size_mb"] for f in sub), 1)}
    return {
        "video": cat(VIDEO_EXTS),
        "audio": cat(AUDIO_EXTS),
        "unsupported": {"count": sum(unsupp.values()), "by_ext": dict(sorted(unsupp.items()))},
        "total_size_mb": round(sum(f["size_mb"] for f in files), 1),
    }

# _allowed_ingest_roots / _assert_ingest_path_safe moved to webguard.py
# (R5-25 / #51) and are re-exported at the top of this module.


@app.get("/api/ingest/engines")
def ingest_engines(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """brick 4 — real options for the setup dialog's transcription pickers, so the
    UI is driven by backend truth (config.WHISPER_GUARD_LAYERS) instead of a
    hardcoded list that would drift. whisper_modes = quality presets 0-4;
    languages = the curated forced-language set (None/omit = auto-detect)."""
    modes = [
        {"mode": k, "name": config.WHISPER_GUARD_LAYERS[k].get("name", str(k))}
        for k in sorted(config.WHISPER_GUARD_LAYERS)
    ]
    # brick 4b: the vision picker's options come from the installed Ollama models
    # (queried live, vision-capable only), so the setup dialog is driven by
    # backend truth instead of a hardcoded list. Empty when Ollama is unreachable
    # → the UI falls back to a free-text field. Always include the current
    # effective model so the active selection shows even if detection missed it
    # (or Ollama is down).
    import vision as _vision
    cur_vision = settings_store.effective("vision.model")
    vision_models = _vision.list_vision_models()
    if cur_vision and cur_vision not in vision_models:
        vision_models = sorted(set(vision_models) | {cur_vision})
    # Phase 9.7 G5③: the dialog's defaults come from the persisted settings
    # (library default), falling back to config. These are genuinely consumed —
    # IngestSetup pre-fills its pickers from them, and the vision model/num_ctx
    # are what an ingest run actually uses (ingest.py reads settings.effective).
    return {
        "whisper_modes": modes,
        "default_mode": settings_store.effective("transcription.default_mode"),
        "default_language": settings_store.effective("transcription.default_language"),
        "default_recursive": settings_store.effective("ingest.recursive"),
        "vision_model": cur_vision,
        "vision_models": vision_models,
        "vision_num_ctx": settings_store.effective("vision.num_ctx"),
        "languages": _INGEST_LANGUAGES,
    }


@app.post("/api/ingest/scan")
def scan_media(
    body: ScanRequest,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Quick scan — return file list without processing."""
    target = Path(body.path).expanduser().resolve()
    if not target.is_dir():
        raise HTTPException(400, "路徑不是有效的目錄")
    _assert_ingest_path_safe(target)
    files = []
    unsupp: dict = {}  # ext -> count, for the MANIFEST "skipped" line
    for f in sorted(target.rglob("*")):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in MEDIA_EXTS:
            already = db.is_processed(str(f)) if hasattr(db, 'is_processed') else False
            files.append({"name": f.name, "size_mb": round(f.stat().st_size / 1048576, 1), "path": str(f), "already": already})
        elif ext in UNSUPPORTED_STILL_EXTS:
            unsupp[ext] = unsupp.get(ext, 0) + 1
    return {
        "total": len(files),
        "new": sum(1 for f in files if not f["already"]),
        "manifest": _build_scan_manifest(files, unsupp),
        "files": files,
    }

@app.post("/api/ingest")
def ingest_media(
    body: IngestRequest,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Trigger ingest from the web UI — runs ingest.py as subprocess."""
    import subprocess, sys
    target = Path(body.path).expanduser().resolve()
    if not target.is_dir():
        raise HTTPException(400, "路徑不是有效的目錄")
    _assert_ingest_path_safe(target)
    cmd = [sys.executable, str(ROOT / "ingest.py"), "--dir", str(target)]
    if body.limit > 0:
        cmd += ["--limit", str(body.limit)]
    cmd += _ingest_cmd_opts(body)
    if not _acquire_ingest_slot():  # audit H3
        raise HTTPException(409, "已有匯入任務進行中，請稍候")
    try:
        # run_tree, not subprocess.run: on timeout the whole ingest.py→ffmpeg/whisper
        # tree is killed, not just the direct child (fable-audit round-5 #2 / #12).
        import proctree
        result = proctree.run_tree(cmd, timeout=1800, cwd=str(ROOT))
        payload = {
            "ok": result.returncode == 0,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
        }
        if result.returncode != 0:
            # audit L11: failures used to come back HTTP 200 + ok:false — keep
            # the body shape for the UI but surface the failure as a 5xx so
            # status-code monitors see it.
            return JSONResponse(status_code=500, content=payload)
        return payload
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "匯入逾時（>30 分鐘）")  # audit L11: was 200 + ok:false
    finally:
        _release_ingest_slot()


# ── DIT Offload (card → backup) — powers the /dit UI ─────────────────────────

# OffloadPreviewRequest / OffloadRequest + the /api/offload* handlers + /dit
# redirect moved to routers/offload.py (R5-25 / #51), mounted via include_router.


# ── Re-transcribe ─────────────────────────────────────────────────────────────

class RetranscribeRequest(BaseModel):
    language: str = "zh"

    # audit M23: an arbitrary string used to flow straight into whisper → 500
    # with raw str(e) leaked to the client + a polluted lang column on partial
    # writes. Accept ISO-639-shaped codes only (whisper's set is 2-3 letters).
    @field_validator("language")
    @classmethod
    def _check_language(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if not _re.fullmatch(r"[a-z]{2,3}", v):
            raise ValueError("language must be a 2-3 letter ISO-639 code (e.g. 'zh', 'en')")
        return v

@app.get("/api/media/{media_id}/remotion-props")
def get_remotion_props(
    media_id: int,
    _tok: dict = Depends(require_scopes("media_read")),
):
    """Export word-level timestamps as Remotion CellPhoneReel props."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    words = json.loads(rec.get("words_json") or "[]")
    return {
        "captions": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in words],
        "duration": rec.get("duration_s", 0),
        "filename": rec.get("filename", ""),
    }

@app.post("/api/media/{media_id}/retranscribe")
def retranscribe_media(
    media_id: int,
    body: RetranscribeRequest,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Re-run Whisper with specified language."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    media_path = _resolve_media_path(rec.get("path", ""))
    if not Path(media_path).exists():
        # fable-audit 2026-07-12: don't leak the resolved absolute path in the
        # error body — surface the PROJECT_ROOT-relative/basename form (Phase 16.2).
        raise HTTPException(400, f"找不到媒體檔案：{_display_path(rec.get('path') or '')}")
    try:
        import transcribe as tr
        text, lang, segments, words = tr.transcribe(media_path, language=body.language)
    except Exception as e:
        # Extraction/transcription failed — leave the existing transcript untouched
        # rather than blanking it (audit H1/H2).
        raise HTTPException(500, "retranscribe 失敗：{0}".format(e))
    # An empty result means "no speech"; for a clip that already has a transcript
    # that's almost always a regression (transient decode failure), not intent —
    # refuse to overwrite a good transcript with nothing (audit H1).
    if not (text or "").strip() and (rec.get("transcript") or "").strip():
        raise HTTPException(422, "transcribe 回空，拒絕覆寫既有逐字稿（可能是音訊擷取失敗）")
    active_lang = lang or body.language
    seg_json = json.dumps(segments, ensure_ascii=False) if segments else None
    words_json = json.dumps(words, ensure_ascii=False) if words else None
    # fable-audit round-5 #17: when retranscribing into the SAME language, the G2
    # archive-outgoing below writes the old text to transcripts[(id, lang)] and then
    # the new-active archive overwrites that very row — so a hand-corrected transcript
    # would be destroyed with no recoverable copy. Snapshot it to the durable
    # correction-backups first (restorable via the same revert). Cross-language
    # retranscribes are already safe (the outgoing language's archive row survives).
    backup_name = None
    if active_lang == rec.get("lang") and (rec.get("transcript") or "").strip():
        backup_name = corrections._write_backup(
            [{"id": media_id, "transcript": rec.get("transcript"),
              "segments_json": rec.get("segments_json"),
              "words_json": rec.get("words_json"), "lang": rec.get("lang")}],
            [{"op": "retranscribe", "language": active_lang}],
        )
    with db.get_conn() as conn:
        # G2: archive the OUTGOING transcript first so retranscribing into a
        # different language preserves the previous one (else zh→en would lose zh).
        if (rec.get("transcript") or "").strip() and rec.get("lang"):
            db.upsert_transcript(media_id, rec["lang"], rec.get("transcript"),
                                 rec.get("segments_json"), rec.get("words_json"), _conn=conn)
        conn.execute(
            "UPDATE media SET transcript=?, lang=?, segments_json=?, words_json=? WHERE id=?",
            (text, active_lang, seg_json, words_json, media_id),
        )
        # archive the new active language too (its row mirrors media.*).
        db.upsert_transcript(media_id, active_lang, text, seg_json, words_json, _conn=conn)
    return {"ok": True, "transcript_length": len(text), "language": active_lang, "backup": backup_name}


class ActivateLangRequest(BaseModel):
    lang: str


@app.get("/api/media/{media_id}/transcripts")
def list_transcripts(media_id: int, _tok: dict = Depends(require_scopes("videos_read"))):
    """All archived transcript languages for a clip (Phase 9.7 G2). The active
    language (media.lang) shows the LIVE media.* content; others show their
    archived content. Lazily backfills the active language on first read so
    pre-feature / ingest-created transcripts appear without an explicit retranscribe."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    active_lang = rec.get("lang")
    rows = db.get_transcripts(media_id)
    have = {r["lang"] for r in rows}
    if (rec.get("transcript") or "").strip() and active_lang and active_lang not in have:
        db.upsert_transcript(media_id, active_lang, rec.get("transcript"),
                             rec.get("segments_json"), rec.get("words_json"))
        rows = db.get_transcripts(media_id)
    for r in rows:
        if r["lang"] == active_lang:
            # the active row mirrors the live cache (authoritative for search/export)
            r["transcript"] = rec.get("transcript")
            r["segments_json"] = rec.get("segments_json")
            r["words_json"] = rec.get("words_json")
            r["active"] = True
        else:
            r["active"] = False
    return {"active_lang": active_lang, "transcripts": rows}


@app.post("/api/media/{media_id}/transcript/activate")
def activate_transcript(
    media_id: int,
    body: ActivateLangRequest,
    request: Request,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Make an archived language the active transcript (Phase 9.7 G2) — copies it
    into media.* so search / export / subtitles use it. The previously-active
    language stays archived and switchable."""
    _assert_same_site(request)
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    row = db.get_transcript(media_id, body.lang)
    if not row:
        raise HTTPException(404, "該語言尚無轉錄")
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE media SET transcript=?, lang=?, segments_json=?, words_json=? WHERE id=?",
            (row["transcript"], row["lang"], row["segments_json"], row["words_json"], media_id),
        )
    return {"ok": True, "active_lang": body.lang}


@app.post("/api/media/{media_id}/retry-vision")
def retry_vision(
    media_id: int,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Retry vision analysis on frames with empty descriptions.
    Two-phase fallback: primary model → lighter fallback model."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    frames = db.get_frames(media_id)
    empty_frames = [f for f in frames if not f.get("description")]
    if not empty_frames:
        return {"ok": True, "message": "所有幀都已有描述", "patched": 0}

    import vision as vis
    frame_paths = [_resolve_media_path(f["thumbnail_path"]) for f in empty_frames]

    # Phase 1: try primary vision model
    results = vis.describe_frames(frame_paths)
    failed = [i for i, r in enumerate(results) if r.get("error") or not r.get("description")]

    # Phase 2: fallback to lighter model for failed frames. Config-driven (unified
    # with ingest.py) and skipped gracefully when the fallback model isn't
    # installed, instead of 404-ing once per failed frame.
    if failed and config.VISION_FALLBACK_MODEL and vis.model_available(config.VISION_FALLBACK_MODEL):
        fallback_model = config.VISION_FALLBACK_MODEL
        # round-5 #50: pass the fallback model straight through to _call_vision.
        # The old vis.VISION_MODEL global-swap was dead (_call_vision re-read the
        # model from settings) — and being a module global it also raced across
        # concurrent retry-vision calls. Threading the arg fixes both.
        retry_paths = [frame_paths[i] for i in failed]
        retry_results = vis.describe_frames(retry_paths, model=fallback_model)
        for idx, retry_r in zip(failed, retry_results):
            if retry_r.get("description") and not retry_r.get("error"):
                results[idx] = retry_r

    # Write results to DB
    patched = 0
    with db.get_conn() as conn:
        for f, vr in zip(empty_frames, results):
            desc = vr.get("description", "")
            tags = ",".join(vr.get("tags", []))
            if desc:
                conn.execute(
                    """
                    UPDATE frames
                    SET description=?, tags=?, content_type=?, focus_score=?, exposure=?,
                        stability=?, audio_quality=?, atmosphere=?, energy=?,
                        edit_position=?, edit_reason=?
                    WHERE media_id=? AND frame_index=?
                    """,
                    (
                        desc,
                        tags,
                        vr.get("content_type"),
                        vr.get("focus_score"),
                        vr.get("exposure"),
                        vr.get("stability"),
                        vr.get("audio_quality"),
                        vr.get("atmosphere"),
                        vr.get("energy"),
                        vr.get("edit_position"),
                        vr.get("edit_reason"),
                        media_id,
                        f["frame_index"],
                    )
                )
                for tag_name in vr.get("tags", []):
                    tag_name = tag_name.strip()
                    if tag_name and tag_name != "```":
                        # _conn=conn: add_tag must reuse the open write txn, else it
                        # opens a 2nd connection that deadlocks on our own writer lock
                        # (audit C1 — 30s wait then the whole patch rolls back / 500).
                        db.add_tag(media_id, tag_name, source="auto", _conn=conn)
                patched += 1
        # Update legacy frame_tags. Read frames through the SAME conn so we see the
        # UPDATEs just written above, not a stale pre-txn snapshot (audit M1).
        all_frames = db.get_frames(media_id, _conn=conn)
        frame_tags = [{"description": f.get("description", ""), "tags": f.get("tags", "").split(",") if f.get("tags") else []} for f in all_frames]
        frame_tags_json = json.dumps(frame_tags, ensure_ascii=False)
        # max over all scored frames (not the first), and leave the prior score
        # untouched when nothing scored rather than nulling it (audit M1).
        scores = [db.compute_editability(fr) for fr in all_frames if fr.get("focus_score") is not None]
        editability_score = max(scores) if scores else None
        conn.execute(
            "UPDATE media SET frame_tags=?, editability_score=COALESCE(?, editability_score) WHERE id=?",
            (frame_tags_json, editability_score, media_id),
        )

    still_empty = sum(1 for vr in results if not vr.get("description") or vr.get("error"))
    return {
        "ok": still_empty == 0,
        "patched": patched,
        "still_empty": still_empty,
        "total_frames": len(empty_frames),
    }


@app.post("/api/media/{media_id}/reingest")
def reingest_media(
    media_id: int,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Re-run full ingest pipeline: probe + whisper + thumbnail + llava + embed."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    media_path = _resolve_media_path(rec.get("path", ""))
    if not Path(media_path).exists():
        # fable-audit 2026-07-12: don't leak the resolved absolute path in the
        # error body — surface the PROJECT_ROOT-relative/basename form (Phase 16.2).
        raise HTTPException(400, f"找不到媒體檔案：{_display_path(rec.get('path') or '')}")
    # G2/G7: a fresh ingest re-runs Whisper and overwrites media.transcript — which
    # would silently destroy a hand-corrected transcript (e.g. via the 9.6b
    # correction dictionary). Snapshot the current transcript into the per-language
    # archive first, so it survives and can be reactivated. (This is the real core
    # of the retracted "conflict merge UI" gap — non-destructive + restorable, the
    # arkiv RP-4 way, instead of asking the user to merge field-by-field.)
    if (rec.get("transcript") or "").strip() and rec.get("lang"):
        db.upsert_transcript(
            media_id, rec["lang"], rec["transcript"],
            rec.get("segments_json"), rec.get("words_json"),
        )
    import subprocess, sys, proctree
    if not _acquire_ingest_slot():  # audit H3 — don't run concurrently with another ingest
        raise HTTPException(409, "已有匯入任務進行中，請稍候")
    try:
        # Single-file mode (ingest.py handles a file path as --dir). The old
        # `--dir <parent> --limit 1` re-processed the alphabetically-first file of
        # the folder, not this media — silently refreshing the WRONG row (audit H4).
        # run_tree kills the whole tree on timeout (round-5 #2 / #12), so an orphaned
        # ffmpeg can't keep writing after the 600s cap with the slot released.
        result = proctree.run_tree(
            [sys.executable, str(ROOT / "ingest.py"), "--dir", media_path, "--refresh"],
            timeout=600, cwd=str(ROOT),
        )
        payload = {
            "ok": result.returncode == 0,
            "stdout": result.stdout[-1000:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
        }
        if result.returncode != 0:
            # audit L11: same error-schema unification as /api/ingest — failure
            # is a 5xx with the diagnostic body, not 200 + ok:false.
            return JSONResponse(status_code=500, content=payload)
        return payload
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "重新處理逾時（>10 分鐘）")  # audit L11
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        _release_ingest_slot()


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

def _ws_authorized(ws: WebSocket, scope: str) -> bool:
    """Authorize a WebSocket handshake. `require_scopes` (a Request-typed Depends)
    does NOT apply to @app.websocket routes, so this is enforced manually:
    - Origin check (CSWSH): a browser always sends Origin; reject cross-site so a
      malicious page can't open ws:// to a loopback-trusted instance.
    - same loopback-trust rule as HTTP (loopback peer + no forwarding header), else
    - a `?token=` with the required scope (a browser ws can't set headers)."""
    # CSWSH guard. A browser always sends Origin; accept it if it's same-origin
    # (its authority == the request Host — the normal case for ANY deployment
    # host/port, incl. remote + HTTPS reverse proxy) or in the static dev/Tauri
    # allowlist. A cross-site page (different authority) is rejected. Non-browser
    # clients (no Origin) fall through to token auth.
    origin = ws.headers.get("origin")
    if origin is not None:
        origin_authority = origin.split("://", 1)[-1]
        host_header = ws.headers.get("host", "")
        if origin_authority != host_header and origin not in _ALLOWED_ORIGINS:
            return False
    host = ws.client.host if ws.client is not None else ""
    if auth._trust_loopback() and host in auth._LOOPBACK_HOSTS and not auth._looks_proxied(ws):
        return True
    try:
        tok = auth.resolve_raw_token((ws.query_params.get("token") or "").strip(), host)
    except Exception:
        return False
    return scope in tok.get("scopes", ())


@app.websocket("/ws/ingest")
async def ws_ingest(ws: WebSocket):
    """WebSocket endpoint for real-time ingest progress updates.

    Previously accepted ANY client (the HTTP scope-gate doesn't reach websocket
    routes) → any LAN/tailnet client, or a malicious browser page (CSWSH), could
    connect and stream every ingest's filenames. Now gated: Origin + ingest_write.
    """
    if not _ws_authorized(ws, "ingest_write"):
        await ws.close(code=1008)  # policy violation
        return
    if not await ingest_ws.connect(ws):  # may refuse over the connection cap
        return
    try:
        while True:
            await ws.receive_text()  # keep alive, client can send pings
    except WebSocketDisconnect:
        ingest_ws.disconnect(ws)


# Tracks the single in-flight WS ingest. asyncio.create_task references must be
# held or the task can be GC'd mid-run and its exceptions silently dropped; the
# flag also serializes ingests so concurrent runs don't hammer the same SQLite DB.
_ingest_ws_tasks: set = set()
def _on_ingest_ws_done(task: "asyncio.Task") -> None:
    _ingest_ws_tasks.discard(task)
    _release_ingest_slot()  # audit H3 — shared single-flight guard
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        import traceback
        print(
            "[ingest-ws] task crashed: "
            + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            flush=True,
        )


async def _run_ingest_with_ws(target: Path, limit: int, opts: Optional[list] = None):
    """Run ingest as a single subprocess, parse stdout for progress."""
    import re, sys

    cmd = [sys.executable, str(ROOT / "ingest.py"), "--dir", str(target)]
    if limit > 0:
        cmd += ["--limit", str(limit)]
    if opts:
        cmd += opts

    await ingest_ws.broadcast({"type": "start", "total": limit or 0})

    # stderr merged into stdout (STDOUT) rather than a separate PIPE: ingest.py is
    # log-heavy, and an unread stderr=PIPE fills its ~64KB OS buffer, blocks the
    # child's write, stalls the stdout read loop, and `proc.wait()` hangs forever.
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, cwd=str(ROOT),
        # brick 3: drive the structured per-stage progress protocol (own-line JSON
        # events) instead of parsing the compact inline `>probe` human markers.
        env={**os.environ, "ARKIV_STAGE_EVENTS": "1"},
        # audit M3: a single stdout line beyond the default 64KB reader limit
        # raises inside the read loop; give pathological log lines 1MB headroom.
        limit=2 ** 20,
    )

    ok, skipped, failed = 0, 0, 0
    last_total = limit or 0
    # brick 3 structured-protocol state: the in-flight file (so a stage event can
    # be attributed) + running per-stage tallies (PROBED/TRANSCRIBED/… aggregate).
    cur_idx, cur_total, cur_file = 0, 0, ""
    stage_counts: dict = {}
    # Filenames can contain spaces — match non-greedily up to the trailing " >"
    # / " ...[OK]" marker instead of \S+ (which truncated at the first space).
    file_re = re.compile(r"\[(\d+)/(\d+)\]\s+(SKIP\s+)?(.+?)\s+>")
    done_re = re.compile(r"\[(\d+)/(\d+)\]\s+(.+?)\s+.+\[OK\]")

    # audit M3: if the read loop dies (oversized line, task cancel, broadcast
    # error) nobody drains stdout — the child wedges forever on a full pipe
    # while the done-callback frees the single-flight slot, allowing a second
    # concurrent ingest. Always kill the subprocess on the way out.
    try:
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            print(f"[ingest-ws] {text}", flush=True)

            # Structured per-stage protocol (brick 3): ingest.py emits one JSON
            # event per line behind the __ARKIV__ sentinel. This fully replaces the
            # human-line regexes for normal files; SKIP lines still flow through
            # file_re below. cur_* tracks the in-flight file so a bare stage event
            # can be attributed to it.
            if text.startswith("__ARKIV__ "):
                try:
                    ev = json.loads(text[len("__ARKIV__ "):])
                except Exception:
                    ev = None
                if not isinstance(ev, dict):
                    continue
                t, status = ev.get("t"), ev.get("status")
                if t == "file" and status == "start":
                    cur_idx, cur_total, cur_file = int(ev.get("index", 0)), int(ev.get("total", 0)), ev.get("file", "")
                    last_total = max(last_total, cur_total)
                    await ingest_ws.broadcast({
                        "type": "file", "index": cur_idx, "total": cur_total,
                        "filename": cur_file, "status": "transcribing",
                    })
                elif t == "stage":
                    st = ev.get("stage", "")
                    stage_counts[st] = stage_counts.get(st, 0) + 1
                    await ingest_ws.broadcast({
                        "type": "stage", "stage": st,
                        "index": cur_idx, "total": cur_total,
                        "filename": ev.get("file") or cur_file,
                        "counts": dict(stage_counts),
                    })
                elif t == "file" and status == "phase1_done":
                    ok += 1
                    last_total = max(last_total, int(ev.get("total", cur_total)))
                    await ingest_ws.broadcast({
                        "type": "file", "index": int(ev.get("index", cur_idx)),
                        "total": int(ev.get("total", cur_total)),
                        "filename": ev.get("file", cur_file), "status": "done",
                    })
                continue  # structured line fully handled — skip the human regexes

            # Parse progress lines like "[1/3] FX30.5365.MP4 >probe >whisper..."
            m = file_re.match(text)
            if m:
                idx, total, skip, fname = m.group(1), m.group(2), m.group(3), m.group(4)
                last_total = max(last_total, int(total))
                if skip:
                    skipped += 1
                    await ingest_ws.broadcast({
                        "type": "file", "index": int(idx), "total": int(total),
                        "filename": fname.strip(), "status": "skipped"
                    })
                else:
                    await ingest_ws.broadcast({
                        "type": "file", "index": int(idx), "total": int(total),
                        "filename": fname.strip(), "status": "transcribing"
                    })

            # Parse completion "[OK]"
            d = done_re.match(text)
            if d:
                ok += 1
                last_total = max(last_total, int(d.group(2)))
                await ingest_ws.broadcast({
                    "type": "file", "index": int(d.group(1)), "total": int(d.group(2)),
                    "filename": d.group(3).strip(), "status": "done"
                })

            # Parse "Found N media files"
            if text.startswith("Found "):
                fm = re.search(r"Processing (\d+)", text)
                if fm:
                    last_total = max(last_total, int(fm.group(1)))
                    await ingest_ws.broadcast({"type": "start", "total": int(fm.group(1))})

        await proc.wait()
    finally:
        if proc.returncode is None:  # audit M3: loop exited abnormally — reap child
            proc.kill()
            await proc.wait()
    # Derive failed from the observed total (not `limit`, which is 0 for "all"
    # and overstates when it exceeds the real file count) and surface the exit
    # code so a nonzero ingest result (e.g. vision halt) is visible to the UI.
    failed = max(0, last_total - ok - skipped)
    rc = proc.returncode or 0
    print(f"[ingest-ws] COMPLETE ok={ok} skipped={skipped} failed={failed} rc={rc}", flush=True)

    await ingest_ws.broadcast({
        "type": "complete", "ok": ok, "skipped": skipped, "failed": failed,
        "returncode": rc
    })


@app.post("/api/ingest/ws")
async def ingest_media_ws(
    body: IngestRequest,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Trigger ingest with WebSocket progress broadcasting.

    Mirrors the /api/ingest twin's gates: ingest_write scope + resolve() +
    _assert_ingest_path_safe. Previously this endpoint had NEITHER, so any client
    could drive the ingest pipeline over an arbitrary directory (Codex-class
    finding from the overnight audit).
    """
    target = Path(body.path).expanduser().resolve()
    if not target.is_dir():
        raise HTTPException(400, f"路徑不是有效的目錄：{body.path}")
    _assert_ingest_path_safe(target)
    if not _acquire_ingest_slot():  # audit H3 — shared single-flight with REST ingest/reingest
        raise HTTPException(409, "已有匯入進行中，請等待完成後再試")
    task = asyncio.create_task(_run_ingest_with_ws(target, body.limit, _ingest_cmd_opts(body)))
    _ingest_ws_tasks.add(task)
    task.add_done_callback(_on_ingest_ws_done)
    return {"ok": True, "message": "已開始匯入 — 連線 /ws/ingest 取得進度"}


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
