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
from typing import Any, List, Optional, Set

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
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
from routers.media import router as media_router  # noqa: E402
from routers.search import router as search_router  # noqa: E402
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
app.include_router(media_router)
app.include_router(search_router)

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

# RetranscribeRequest / ActivateLangRequest + the single-clip /api/media/{id}
# retranscribe, remotion-props, transcripts (list + activate) and retry-vision
# routes moved to routers/media.py (R5-25 / #51), mounted via app.include_router
# above. (The BATCH /api/retranscribe-all lives in routers/retranscribe.py.)


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
