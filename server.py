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
from pathlib import Path
from typing import Any, List, Literal, Optional, Set

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import admin
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
import smart_collections
import tag_aliases
import tag_quality


# ── Ingest single-flight guard ───────────────────────────────────────────────
# One guard for ALL ingest entry points (REST /api/ingest, reingest, WS) so two
# full pipelines can't run at once → DB-lock contention + double whisper + OOM on a
# 16GB box (audit H3). threading.Lock because REST runs in the threadpool while the
# WS variant runs on the event loop.
import threading as _threading
_ingest_lock = _threading.Lock()
_ingest_active = False

def _acquire_ingest_slot() -> bool:
    global _ingest_active
    with _ingest_lock:
        if _ingest_active:
            return False
        _ingest_active = True
        return True

def _release_ingest_slot() -> None:
    global _ingest_active
    with _ingest_lock:
        _ingest_active = False

# audit M9 (partial): vision.VISION_MODEL is module-global, so two concurrent
# retry-vision calls could interleave their save/swap/restore and permanently
# pin the fallback model as "primary" (silent quality degradation). Serializing
# the swap window removes the race; the full fix (pass the model as a
# describe_frames parameter) needs a vision.py signature change.
_vision_fallback_lock = _threading.Lock()

# audit M8: single-flight for the embed rebuild — double-clicking the rebuild
# button used to launch N concurrent drop+rebuild subprocesses over the same
# Chroma collection.
_embed_rebuild_lock = _threading.Lock()
_embed_rebuild_active = False

# Phase 9.6d: project-wide batch retranscribe (single-flight + progress poll).
_retranscribe_lock = _threading.Lock()
_retranscribe_active = False
_retranscribe_progress = {"total": 0, "done": 0, "failed": 0, "current": None, "running": False, "backup": None}

# ── WebSocket connection manager ────────────────────────────────────────────
_MAX_WS_CONNECTIONS = 32  # cap concurrent progress listeners (DoS guard)


class IngestBroadcaster:
    """Manages WebSocket connections for ingest progress updates."""
    def __init__(self):
        self.connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> bool:
        if len(self.connections) >= _MAX_WS_CONNECTIONS:
            await ws.close(code=1013)  # try again later
            return False
        await ws.accept()
        # audit L6: re-check after the await — concurrent handshakes can all pass
        # the pre-accept check before any of them is added to the set (TOCTOU).
        if len(self.connections) >= _MAX_WS_CONNECTIONS:
            await ws.close(code=1013)
            return False
        self.connections.add(ws)
        return True

    def disconnect(self, ws: WebSocket):
        self.connections.discard(ws)

    async def broadcast(self, data: dict):
        dead = set()
        # snapshot: send_json awaits, so a concurrent connect/disconnect mutating
        # the live set mid-iteration would raise "Set changed size" (audit H9).
        for ws in list(self.connections):
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self.connections -= dead

ingest_ws = IngestBroadcaster()

# ── Init ─────────────────────────────────────────────────────────────────────
db.init_db()

# Redact `?token=` from uvicorn access logs. /api/stream accepts the token as a
# query param (a <video src> can't send a header), and uvicorn's default access
# log records the full request line incl. query string → the raw token would be
# written to stdout / any redirected logfile. This filter scrubs it everywhere
# the access logger formats a request path.
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
_logging.getLogger("uvicorn.access").addFilter(_RedactTokenFilter())

app = FastAPI(title="Media Asset Manager API")
_ALLOWED_ORIGINS = [
    "http://localhost:8501",
    "http://127.0.0.1:8501",
    "http://localhost:5173",     # Vite dev server (frontend dev + ws proxy)
    "http://127.0.0.1:5173",
    "https://tauri.localhost",   # Tauri webview
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


def _basename_safe(value: str) -> str:
    """Separator-agnostic basename. `os.path.basename` on a POSIX host treats `\\`
    as an ordinary character, so a Windows project path (`C:\\Users\\me\\proj`)
    handed over by a cross-platform federation peer would survive `basename` +
    `rstrip("/")` and leak intact. Normalise both separators first."""
    if not value:
        return value
    normalized = str(value).replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1] or normalized


def _looks_absolute(p: str) -> bool:
    """True for POSIX (`/x`), Windows drive (`C:\\x`), or UNC (`\\\\host`) absolute
    paths. `os.path.isabs` on a POSIX host misses the Windows forms, which would
    let a cross-platform peer's absolute path slip past the leak guard."""
    if not p:
        return False
    return (
        p.startswith("/")
        or p.startswith("\\\\")
        # Windows drive form `X:` followed by EITHER separator (`C:\` or `C:/` —
        # Windows APIs emit and accept both). Security-first call on an inherent
        # ambiguity: `C:/...` could also be a POSIX *relative* path under a dir
        # literally named "C:", but a leak guard must not let a Windows-absolute
        # path through, and a Unix media dir literally named "C:" is pathological.
        # So we basename it (the rare round-trip loss is the accepted trade-off vs
        # re-opening the path leak). (Codex round-1 wanted C:/ preserved; round-2
        # showed that re-leaks C:/ Windows absolutes — no-leak wins.)
        or (len(p) >= 3 and p[0].isalpha() and p[1] == ":" and p[2] in ("\\", "/"))
    )


def _display_path(path: str) -> str:
    """Phase 16.2: the non-leaking path form for API responses.

    Returning the absolute fs path leaked the operator's directory tree
    (/Volumes/home/影片專案/…) to any read-scope / loopback client. We return the
    PROJECT_ROOT-relative form; a legacy row whose stored path is absolute AND
    outside PROJECT_ROOT (so to_relative can't relativize it) is reduced to its
    basename rather than leaking the full path. Relative paths round-trip through
    /api/open-file (db.is_processed matches relative; the server re-absolutizes);
    once a library is migrated (ingest.py --migrate-relative) every row is
    relative and open-file works for all of them.
    """
    if not path:
        return path
    rel = db.to_relative(path)
    if _looks_absolute(rel):  # absolute (POSIX/Windows/UNC) & outside root — don't leak
        return _basename_safe(rel)
    return rel


def _resolve_record(rec: dict) -> dict:
    if rec.get("path"):
        rec["path"] = _display_path(rec["path"])
    if rec.get("thumbnail_path"):
        rec["thumbnail_path"] = _display_path(rec["thumbnail_path"])
    return rec


def _resolve_frame(frame: dict) -> dict:
    if frame.get("thumbnail_path"):
        frame["thumbnail_path"] = _display_path(frame["thumbnail_path"])
    return frame


def _resolve_media_path(path: str) -> str:
    if not path:
        return path
    if os.name == "nt" and path.startswith("/"):
        return path
    return db.resolve_path(path)


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
 
 
class ProjectCreate(BaseModel):
    name: str
    path: str
    tags: Optional[List[str]] = None

    # audit M21: empty / control-char / separator-bearing names were accepted —
    # a name containing '/' can be created but never DELETEd (the path param
    # can't match it), and unbounded names bloat the registry.
    @field_validator("name")
    @classmethod
    def _clean_project_name(cls, v: str) -> str:
        cleaned = " ".join(
            "".join(c for c in (v or "") if c == " " or (ord(c) >= 0x20 and c != "\x7f")).split()
        )
        if not cleaned:
            raise ValueError("project name must not be empty")
        if len(cleaned) > 100:
            raise ValueError("project name too long (max 100)")
        if "/" in cleaned or "\\" in cleaned:
            raise ValueError("project name must not contain path separators")
        return cleaned


class CreateTokenRequest(BaseModel):
    name: str
    scopes: List[str]
    description: Optional[str] = None
    expires_in_days: Optional[int] = None
    allowed_ips: Optional[List[str]] = None


class ChatRequest(BaseModel):
    prompt: str
    conversation_id: Optional[str] = None
    project_scope: Optional[List[str]] = None

    @field_validator("prompt")
    @classmethod
    def _check_prompt(cls, v: str) -> str:
        # reject empty/whitespace. Oversize prompts are NOT rejected — existing
        # behavior trims them for the LLM (chat._trim_prompt) and that's tested;
        # the storage-cap for the persisted copy lives in the handler instead.
        if not v or not v.strip():
            raise ValueError("prompt must not be empty")
        return v

    @field_validator("project_scope")
    @classmethod
    def _cap_scope(cls, v):
        if v is not None and len(v) > 200:
            raise ValueError("project_scope too large (max 200)")
        return v


class ChatResponse(BaseModel):
    conversation_id: str
    assistant_text: str
    scene_ids: List[object]
    intent: Optional[str] = None
    tokens_used: int
    latency_ms: int


def _split_csv(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    parts = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts or None


@app.on_event("startup")
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

def _chat_owner_filter(tok: dict):
    """SQL fragment + params restricting chat_conversations to those a token may
    read. Loopback / admin (the local owner) see all → no filter. Any other token
    sees only its own conversations plus legacy ones with no recorded owner.
    Returns ('', ()) for the unrestricted case."""
    tok_id = (tok or {}).get("id")
    scopes = (tok or {}).get("scopes") or ()
    if tok_id == "loopback" or "admin" in scopes:
        return "", ()
    return " AND (user_token_id = ? OR user_token_id IS NULL)", (tok_id,)


@app.post("/api/chat", response_model=ChatResponse)
def chat_endpoint(
    request: Request,
    req: ChatRequest,
    _tok: dict = Depends(require_scopes("chat_write")),
) -> ChatResponse:
    del request
    if req.conversation_id is None:
        conv_id = chat.create_conversation(
            user_token_id=_tok.get("id"),
            first_prompt=req.prompt,
            project_scope=req.project_scope,
        )
    else:
        conv_id = req.conversation_id
        # Ownership on the WRITE path too: a non-owner who learns a conversation
        # id must not be able to append a prompt/response to someone else's
        # history (read-side filtering alone left this open). 404 (not 403/400)
        # so a non-owner can't even confirm the id exists.
        owner_sql, owner_params = _chat_owner_filter(_tok)
        with db.get_conn() as conn:
            owned = conn.execute(
                "SELECT 1 FROM chat_conversations WHERE id = ?" + owner_sql,
                (conv_id, *owner_params),
            ).fetchone()
        if not owned:
            raise HTTPException(status_code=404, detail="conversation not found")

    # Persist a length-capped copy: the LLM only ever sees a trimmed prompt
    # (chat._trim_prompt), so storing the raw multi-MB original would bloat the
    # conversation DB unboundedly for no benefit.
    chat.persist_message(conv_id, role="user", content=req.prompt[:8000])
    result = chat.dispatch(req.prompt, conv_id, project_scope=req.project_scope)
    chat.persist_message(
        conv_id,
        role="assistant",
        content=result["assistant_text"],
        intent=result.get("intent", "compilation"),
        scene_ids=result["scene_ids"],
        tokens_used=result["tokens_used"],
        stage=result.get("stage", "done"),
        latency_ms=result.get("latency_ms"),
    )
    return ChatResponse(
        conversation_id=conv_id,
        assistant_text=result["assistant_text"],
        scene_ids=result["scene_ids"],
        intent=result.get("intent", "compilation"),
        tokens_used=result["tokens_used"],
        latency_ms=result.get("latency_ms", 0),
    )


@app.get("/api/chat/history/{conv_id}")
def get_chat_history(
    request: Request,
    conv_id: str,
    limit: int = 50,
    _tok: dict = Depends(require_scopes("chat_read")),
) -> dict:
    del request
    limit = max(1, min(500, limit))
    # Conversation ownership: a remote token may only read its OWN conversations
    # (or legacy ones with no owner). The ownership column was recorded on create
    # but never enforced on read, so any chat_read token could read every other
    # token's history. Loopback / admin (the local owner) see everything.
    owner_sql, owner_params = _chat_owner_filter(_tok)
    with db.get_conn() as conn:
        conv = conn.execute(
            "SELECT id, title, project_scope_json, created_at, updated_at "
            "FROM chat_conversations WHERE id = ?" + owner_sql,
            (conv_id, *owner_params),
        ).fetchone()
        if not conv:
            # 404 (not 403) so a non-owner can't even confirm the id exists
            raise HTTPException(status_code=404, detail="conversation not found")

        rows = conn.execute(
            "SELECT id, role, content, intent, scene_ids_json, tokens_used, stage, "
            "latency_ms, created_at FROM chat_messages "
            "WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ?",
            (conv_id, limit),
        ).fetchall()

    return {
        "conversation": dict(conv),
        "messages": [dict(row) for row in rows],
    }


@app.get("/api/chat/conversations")
def list_chat_conversations(
    request: Request,
    limit: int = 50,
    _tok: dict = Depends(require_scopes("chat_read")),
) -> dict:
    del request
    limit = max(1, min(500, limit))
    owner_sql, owner_params = _chat_owner_filter(_tok)
    where = (" WHERE 1=1" + owner_sql) if owner_sql else ""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, project_scope_json, created_at, updated_at "
            "FROM chat_conversations" + where + " ORDER BY updated_at DESC LIMIT ?",
            (*owner_params, limit),
        ).fetchall()
    return {"conversations": [dict(row) for row in rows]}


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


@app.get("/api/projects")
def list_projects(
    _tok: dict = Depends(require_scopes("projects_read")),
):
    # A corrupt ~/.arkiv-projects.json must not 500 with a raw stack trace on
    # every read — return a clean error the UI can show.
    try:
        projects = [project.to_dict() for project in project_registry.list_registry_projects()]
    except project_registry.RegistryError as exc:
        raise HTTPException(status_code=500, detail="project registry unreadable: {0}".format(exc))
    return {"projects": projects, "total": len(projects)}


@app.post("/api/projects")
def add_project(
    body: ProjectCreate,
    _tok: dict = Depends(require_scopes("projects_write")),
):
    # audit M21: registry add_project silently REPLACES an existing entry of the
    # same name — surface the collision as 409 instead of overwriting.
    try:
        existing = {p.name for p in project_registry.list_registry_projects()}
    except project_registry.RegistryError as exc:
        raise HTTPException(status_code=500, detail="project registry unreadable: {0}".format(exc))
    if body.name in existing:
        raise HTTPException(status_code=409, detail="project name already exists: {0}".format(body.name))
    try:
        project = project_registry.add_project(body.name, body.path, body.tags or [])
    except project_registry.RegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return project.to_dict()


@app.delete("/api/projects/{name}")
def remove_project(
    name: str,
    _tok: dict = Depends(require_scopes("projects_write")),
):
    try:
        project = project_registry.remove_project(name)
    except project_registry.RegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return project.to_dict()


@app.post("/api/projects/sync")
def sync_projects(
    _tok: dict = Depends(require_scopes("projects_write")),
):
    try:
        projects = [project.to_dict() for project in project_registry.sync_projects()]
    except project_registry.RegistryError as exc:
        raise HTTPException(status_code=500, detail="project registry unreadable: {0}".format(exc))
    return {"projects": projects, "total": len(projects)}


@app.get("/api/projects/health")
def projects_health(
    _tok: dict = Depends(require_scopes("projects_read")),
):
    projects = project_registry.health_projects()
    ok_count = sum(1 for item in projects if item["status"] == "ok")
    return {"projects": projects, "total": len(projects), "ok": ok_count}


# --- Phase 9.7 G5②: persisted settings (curated key/value overrides) ---

def _resolve_settings_scope(scope: str) -> str:
    """Validate the scope param. 'global' is the library-wide default; any other
    value must be a known project root (registry or the current PROJECT_ROOT) —
    we never let an arbitrary string become a scope row, so the table can't be
    used as free-form key/value storage."""
    if not scope or scope == settings_store.GLOBAL_SCOPE:
        return settings_store.GLOBAL_SCOPE
    known = {str(config.PROJECT_ROOT)}
    try:
        known.update(p.path for p in project_registry.list_registry_projects())
    except project_registry.RegistryError:
        pass
    if scope not in known:
        raise HTTPException(status_code=400, detail="unknown settings scope: {0}".format(scope))
    return scope


@app.get("/api/settings")
def get_settings(
    scope: str = "global",
    _tok: dict = Depends(require_scopes("projects_read")),
):
    """Effective settings (default ← global ← project) + metadata per key."""
    resolved = _resolve_settings_scope(scope)
    project = None if resolved == settings_store.GLOBAL_SCOPE else resolved
    return {"scope": resolved, "settings": settings_store.describe(project=project)}


class SettingsUpdate(BaseModel):
    scope: str = "global"
    values: dict


@app.put("/api/settings")
def put_settings(
    body: SettingsUpdate,
    _tok: dict = Depends(require_scopes("admin")),
):
    """Persist a batch of overrides at the given scope. Validate-all-then-write:
    a single bad key/value rejects the whole batch (422), nothing is stored."""
    resolved = _resolve_settings_scope(body.scope)
    try:
        written = settings_store.put(body.values, scope=resolved)
    except settings_store.SettingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    project = None if resolved == settings_store.GLOBAL_SCOPE else resolved
    return {
        "scope": resolved,
        "written": written,
        "settings": settings_store.describe(project=project),
    }


@app.delete("/api/settings/{key}")
def reset_setting(
    key: str,
    scope: str = "global",
    _tok: dict = Depends(require_scopes("admin")),
):
    """Drop one override so the key falls back to the next layer down."""
    resolved = _resolve_settings_scope(scope)
    try:
        settings_store.reset(key, scope=resolved)
    except settings_store.SettingError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"scope": resolved, "reset": key}


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
# search branch applies the SAME filter the SQL (non-search) path does.
_VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".insv", ".360"})
_AUDIO_EXTS = frozenset({".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"})


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
    placeholders = ",".join("?" * len(ids))
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT {db.LIGHT_COLS} FROM media WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
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
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """List media with filters, sorting, and pagination."""
    # Clamp pagination: a negative limit becomes SQLite LIMIT -1 (unbounded full
    # dump) and a huge limit blows up the vector-search n_results.
    limit = max(1, min(500, limit))
    offset = max(0, offset)
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


@app.get("/api/stats")
def get_stats(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """Aggregate stats for dashboard."""
    stats = db.get_stats()
    stats["rating"] = db.get_rating_stats()
    # Real disk usage of the volume holding the arkiv data — feeds the sidebar's
    # Storage footer (was a hardcoded "4.8 TB · 12 TB · 40%" placeholder). Best
    # effort: a stat() failure (e.g. path vanished) must not 500 the dashboard.
    try:
        import shutil
        du = shutil.disk_usage(config.DB_PATH.parent if config.DB_PATH else ROOT)
        stats["disk"] = {
            "used_gb": round(du.used / 1e9, 1),
            "total_gb": round(du.total / 1e9, 1),
            "pct": round(du.used / du.total * 100) if du.total else 0,
        }
    except Exception:
        stats["disk"] = None
    # Screen quality-defect tags here too (Codex review P2) — index.html and any
    # stats-driven cloud read top_tags. Over-fetch then filter so we still get 10
    # real tags even if some of the top entries were noise.
    stats["top_tags"] = tag_quality.filter_tag_records(db.get_top_tags(40))[:10]
    # Real project name (basename of PROJECT_ROOT) so the UI shows the loaded
    # library instead of a hardcoded demo name. Multi-library installs (one .arkiv
    # per project) each report their own name.
    stats["project"] = config.PROJECT_ROOT.name if config.PROJECT_ROOT else None
    return stats


@app.get("/api/tags")
def get_all_tags(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """All unique tag names with counts. Quality-defect tags (模糊/低解析度…)
    are screened out, variant-char spellings (人群/人羣) merged (tag_quality), then
    near-synonyms folded to their preferred label via the reviewed alias map
    (tag_aliases) — a no-op until `ingest --propose-aliases`/`--apply-aliases`."""
    return tag_aliases.fold_records(tag_quality.merge_tag_records(db.get_all_tag_names()))


def _thumb_url(thumbnail_path):
    """Absolute fs thumbnail_path → served /thumbnails/<basename> URL (or None)."""
    if not thumbnail_path:
        return None
    base = str(thumbnail_path).replace("\\", "/").rsplit("/", 1)[-1]
    return "/thumbnails/{0}".format(base)


@app.get("/api/collections")
def list_collections(
    _tok: dict = Depends(require_scopes("collections_read")),
):
    """Smart Collections — classify every media item against the Tier-1
    definitions (smart_collections.DEFAULT_COLLECTIONS) and group the results.

    Rule-driven (not ML clustering): see smart_collections.py. Returns one entry
    per collection that has >=1 member, each with its member media (id/filename/
    thumb/duration/score), sorted by score desc. Membership is non-exclusive.
    """
    defs = smart_collections.DEFAULT_COLLECTIONS
    buckets = {c.key: {"key": c.key, "title": c.title, "category": c.category, "items": []} for c in defs}

    # audit L13: classify reads only these columns (frame_tags + media-level
    # aggregates + gps + duration/audio) — get_all_records() was SELECT *,
    # hauling words_json/segments_json/transcript for the entire library.
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, thumbnail_path, duration_s, has_audio, "
            "frame_tags, content_type, atmosphere, energy, gps_lat, gps_lon, "
            "rating, processed_at "
            "FROM media ORDER BY id"
        ).fetchall()
    for rec in (dict(r) for r in rows):
        for hit in smart_collections.classify(rec, defs):
            buckets[hit["key"]]["items"].append({
                "id": rec["id"],
                "filename": rec.get("filename"),
                "thumb": _thumb_url(rec.get("thumbnail_path")),
                "duration_s": rec.get("duration_s"),
                "score": hit["score"],
            })

    out = []
    for b in buckets.values():
        if not b["items"]:
            continue
        b["items"].sort(key=lambda r: r["score"], reverse=True)
        b["count"] = len(b["items"])
        out.append(b)
    out.sort(key=lambda c: c["count"], reverse=True)
    return {"collections": out, "total": len(out)}


@app.get("/api/duration-by-lang")
def duration_by_lang(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT lang, SUM(duration_s) as total_s, COUNT(*) as count "
            "FROM media WHERE lang IS NOT NULL GROUP BY lang ORDER BY total_s DESC"
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/size-by-ext")
def size_by_ext(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT ext, SUM(size_mb) as total_mb, COUNT(*) as count "
            "FROM media GROUP BY ext ORDER BY total_mb DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Metadata Export (Phase 7.6) ──────────────────────────────────────────────

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
def _allowed_export_roots() -> list:
    """Approved roots for export-to endpoints. User can override with
    ARKIV_EXPORT_ROOTS env (colon-separated list of abs paths)."""
    custom = os.environ.get("ARKIV_EXPORT_ROOTS", "").strip()
    if custom:
        return [Path(p).expanduser().resolve() for p in custom.split(":") if p.strip()]
    home = Path.home()
    return [
        (home / "Desktop").resolve(),
        (home / "Documents").resolve(),
        (home / "Downloads").resolve(),
        (home / "Movies").resolve(),
        # Cross-platform tmp + project root for tests / scripted exports
        Path("/tmp").resolve(),
        Path(os.environ.get("TMPDIR", "/tmp")).resolve(),
        (Path.cwd() / "temp").resolve(),
        (config.PROJECT_ROOT / "temp").resolve(),
    ]


_ALLOWED_EXPORT_EXTS = {
    ".csv", ".srt", ".vtt", ".edl", ".fcpxml", ".xml", ".txt", ".json",
}


def _assert_export_dest_safe(dest: Path) -> None:
    """Reject writes outside approved user export roots.

    Codex Round-2 audit Critical fix: 舊版 denylist 只擋 6 個系統 dir，能寫
    `~/.ssh/authorized_keys` / `/Library/LaunchAgents/*.plist` / `/var/log`
    等敏感位置。Tailscale 共享 + 無 auth 場景下任何 collaborator 直接 RCE。

    新策略：allowlist — dest 的 canonical path 必須落在 ALLOWED 之一底下；
    副檔名也限定在常見匯出格式（.csv/.srt/.vtt/.edl/.fcpxml/.xml/.txt/.json），
    防止寫 .plist / .pem / .ssh-config 之類執行/憑證檔。
    """
    canonical = dest.resolve()
    if canonical.suffix.lower() not in _ALLOWED_EXPORT_EXTS:
        raise HTTPException(403, f"不允許的匯出副檔名：{canonical.suffix}")
    roots = _allowed_export_roots()
    for root in roots:
        try:
            canonical.relative_to(root)
            return  # under approved root
        except ValueError:
            continue
    raise HTTPException(403, f"匯出路徑必須在批准的目錄下：{[str(r) for r in roots]}")


def _csv_safe(value: str) -> str:
    """Defuse CSV formula injection (Excel/Sheets execute leading =/+/-/@/TAB/CR).
    DaVinci 不執行公式，但 user 在 Excel preview 會中招。Prefix 一個 single quote
    是 Excel/Sheets 標準 escape — DaVinci import 時會把整段當成字串收進 metadata。
    """
    if not value:
        return value
    if value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def _parse_frame_tags(frame_tags_value):
    """Decode frame_tags JSON list into structured fields used by CSV export.

    Real-world DB（example: 恬馨庫 427 rows）每筆 frame_tags 是 vision pipeline
    寫入的 JSON list，每個 frame 都是 dict：
      {description, tags, content_type, focus_score, exposure, stability,
       audio_quality, atmosphere, energy, edit_position, edit_reason}

    7.6b 第一版用 .split('\\n')[0] 把整段 JSON 當 plain text，DaVinci
    Description 就會看到 raw JSON 字串（audit Batch E F1 critical fix）。

    Returns (first_description, all_descriptions, vision_tags, content_type,
             atmosphere, energy, edit_position) — 後 4 個欄是「第一個非空 frame
    的值」用來補 media-level columns 為 NULL 的庫（Phase 8.2 hoist 還沒跑）。
    Legacy 純文字 frame_tags（早期 schema）→ 退化成 ('first line', [first line], [], …Nones)。
    """
    if not frame_tags_value:
        return ("", [], [], None, None, None, None)
    try:
        ft = json.loads(frame_tags_value)
    except (ValueError, TypeError):
        # legacy plain-text frame_tags — split on newlines so Scene retains 全段，
        # Description 取第一行（保持 7.6b 第一版的 plain-text 行為）
        lines = [ln.strip() for ln in str(frame_tags_value).splitlines() if ln.strip()]
        first = lines[0] if lines else ""
        return (first[:200], lines, [], None, None, None, None)
    if not isinstance(ft, list):
        return ("", [], [], None, None, None, None)

    descriptions = []
    tags_set = []
    seen_tags = set()
    content_type = None
    atmosphere = None
    energy = None
    edit_position = None
    for frame in ft:
        if not isinstance(frame, dict):
            continue
        d = frame.get("description")
        if isinstance(d, str) and d.strip():
            descriptions.append(d.strip())
        t = frame.get("tags")
        if isinstance(t, list):
            for tag in t:
                if isinstance(tag, str) and tag.strip() and tag not in seen_tags:
                    tags_set.append(tag.strip())
                    seen_tags.add(tag)
        # First non-empty wins for these — frames after the first usually agree
        if not content_type and isinstance(frame.get("content_type"), str):
            content_type = frame["content_type"]
        if not atmosphere and isinstance(frame.get("atmosphere"), str):
            atmosphere = frame["atmosphere"]
        if not energy and isinstance(frame.get("energy"), str):
            energy = frame["energy"]
        if not edit_position and isinstance(frame.get("edit_position"), str):
            edit_position = frame["edit_position"]

    first_desc = descriptions[0][:200] if descriptions else ""
    return (first_desc, descriptions, tags_set, content_type, atmosphere, energy, edit_position)


def _build_metadata_csv(media_ids=None) -> str:
    """Build the DaVinci Resolve metadata CSV body. Shared by GET (blob download
    for browser) and POST -to (Tauri native save dialog).

    media_ids: Optional iterable of media ids — when provided, only those rows
    are exported (audit Batch E F5: plugin import 後 download CSV 應該只含剛
    import 的 N 個 clip，不是整庫 dump 出去 — 既改善 UX 也避免不相關 transcript
    被一起 share 出去)。None = 整庫匯出（既有 Web UI 行為）。"""
    import csv
    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["File Name", "Description", "Keywords", "Comments", "Scene"])

    sql = ("SELECT id, filename, transcript, frame_tags, content_type, "
           "atmosphere, energy, edit_position FROM media")
    params: list = []
    if media_ids is not None:
        ids = [int(i) for i in media_ids]
        if not ids:
            return buf.getvalue()  # empty → header only, no rows
        sql += " WHERE id IN (" + ",".join("?" * len(ids)) + ")"
        params.extend(ids)
    sql += " ORDER BY id"

    with db.get_conn() as conn:
        media_rows = conn.execute(sql, params).fetchall()
        for row in media_rows:
            tag_rows = conn.execute(
                "SELECT name FROM tags WHERE media_id=? ORDER BY name", (row["id"],)
            ).fetchall()
            tags = [t["name"] for t in tag_rows]

            # Vision JSON parsing — real source for Description / Scene + fallback for
            # media-level NULL content_type / atmosphere / energy / edit_position.
            (vision_desc, all_descs, vision_tags,
             ct_json, atmo_json, energy_json, edit_json) = _parse_frame_tags(row["frame_tags"])

            # Description: vision first-frame description, fallback transcript prefix
            desc = vision_desc or (row["transcript"].strip()[:200] if row["transcript"] else "")

            # Keywords: manual tags + vision tags + content_type. Dedup case-insensitively
            # because tags 強制 lower (db.py:340) 但 vision/content_type 帶大寫（"B-Roll"）。
            keywords = list(tags)
            seen_lower = {k.lower() for k in keywords}
            for vt in vision_tags:
                if vt.lower() not in seen_lower:
                    keywords.append(vt)
                    seen_lower.add(vt.lower())
            ct_value = row["content_type"] or ct_json
            if ct_value and ct_value.lower() not in seen_lower:
                keywords.append(ct_value)
            keyword_str = "; ".join(keywords)

            # Comments: media-level cols win, else fallback to JSON-derived
            atmo = row["atmosphere"] or atmo_json
            energy = row["energy"] or energy_json
            edit_pos = row["edit_position"] or edit_json
            comment_parts = []
            if atmo:
                comment_parts.append(f"atmosphere:{atmo}")
            if energy:
                comment_parts.append(f"energy:{energy}")
            if edit_pos:
                comment_parts.append(f"edit:{edit_pos}")
            comments = " | ".join(comment_parts)

            # Scene: 把所有 frame description 合成一段（DaVinci Smart Bin 可 contains 搜）
            scene = " | ".join(all_descs)

            writer.writerow([
                _csv_safe(row["filename"]),
                _csv_safe(desc),
                _csv_safe(keyword_str),
                _csv_safe(comments),
                _csv_safe(scene),
            ])

    return buf.getvalue()


def _parse_ids_query(ids_query):
    """Decode ?ids=1,2,3 query string → [int]. Returns None when no filter requested."""
    if not ids_query:
        return None
    parsed = []
    for raw in ids_query.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed.append(int(raw))
        except ValueError:
            raise HTTPException(400, f"無效的 media id: {raw!r}")
    return parsed  # may be [] if all entries were blank → caller treats as filter-with-no-rows


@app.get("/api/export/metadata-csv")
def export_metadata_csv(
    ids: Optional[str] = None,
    _tok: dict = Depends(require_scopes("media_read")),
):
    """DaVinci Resolve metadata CSV — File Name as match key.

    Import in Resolve: File → Import Metadata from CSV.
    Browser path: returns CSV body for blob download.

    ids query param (CSV of integers): batch-scoped export — plugin import 後
    呼叫時只想拿剛 import 的 N 個 clip 對應的 row，不要把整庫 transcript 一起
    塞給協作者（audit Batch E F5）。
    """
    media_ids = _parse_ids_query(ids)
    return Response(
        content=_build_metadata_csv(media_ids=media_ids),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="arkiv_davinci_metadata.csv"',
        },
    )


class MetadataCsvExportRequest(BaseModel):
    # dest optional: when omitted/blank, fall back to the persisted
    # export.default_dir setting (Phase 9.7 G5③) + a default filename.
    dest: Optional[str] = None
    ids: Optional[list] = None  # batch-scoped variant; None = full library


@app.post("/api/export/metadata-csv-to")
def export_metadata_csv_to(
    body: MetadataCsvExportRequest,
    # writes a file to a caller-chosen local path — gate on write, not read, so a
    # read-only token can't drop files in the operator's home dir (audit H10).
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Tauri WKWebView path: server writes CSV directly to user-picked dest.

    WKWebView 對 <a download> blob 觸發下載不可靠（Tauri docs 也建議走 fs API），
    所以 Tauri front-end 用 dialog.save 拿 path 後 POST 來這裡，由 server 直接寫
    檔；browser 端則繼續用 GET + blob download。
    body.ids 給時為 batch-scoped；不給為整庫匯出。"""
    # Phase 9.7 G5③: resolve dest from the request, else the persisted
    # export.default_dir setting (+ a default filename). A bare directory also
    # gets the default filename appended.
    raw_dest = (body.dest or "").strip()
    if not raw_dest:
        default_dir = settings_store.effective("export.default_dir")
        if not default_dir:
            raise HTTPException(400, "no dest provided and export.default_dir is unset")
        raw_dest = str(Path(default_dir) / "arkiv-metadata.csv")
    dest = Path(raw_dest).expanduser().resolve()
    if dest.is_dir() or raw_dest.endswith(("/", "\\")):
        dest = dest / "arkiv-metadata.csv"
    _assert_export_dest_safe(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    media_ids = None
    if body.ids is not None:
        try:
            media_ids = [int(i) for i in body.ids]
        except (TypeError, ValueError):
            raise HTTPException(400, "ids 必須是整數 list")
    csv_body = _build_metadata_csv(media_ids=media_ids)
    with dest.open("w", encoding="utf-8", newline="") as fh:
        fh.write(csv_body)
    return {"ok": True, "path": str(dest), "size": dest.stat().st_size, "rows": len(media_ids) if media_ids is not None else None}


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


# brick 4 — whisper language codes the setup picker offers (whisper supports many
# more; this is the curated UI set). Omit / None = auto-detect or the preset hint.
_INGEST_LANGUAGES = [
    {"code": "zh", "label": "中文"},
    {"code": "en", "label": "English"},
    {"code": "ja", "label": "日本語"},
    {"code": "ko", "label": "한국어"},
]
_INGEST_LANGUAGE_CODES = {lang["code"] for lang in _INGEST_LANGUAGES}


def _ingest_cmd_opts(body: "IngestRequest") -> list:
    """Translate IngestRequest options into ingest.py CLI flags. Shared by the
    REST (/api/ingest) and WebSocket (/api/ingest/ws) triggers so the two never
    drift. --dir / --limit stay with the callers; this is only the extra knobs."""
    opts: list = []
    if body.skip_vision:
        opts.append("--skip-vision")
    if body.refresh:
        opts.append("--refresh")
    if body.recursive:
        opts.append("--recursive")
    if body.max_failures and body.max_failures > 0:
        opts += ["--max-failures", str(int(body.max_failures))]
    if body.skip_failed:
        opts.append("--skip-failed")
    if body.no_embed:
        opts.append("--no-embed")
    # brick 4 — only emit when explicitly set (and valid) so defaults stay untouched.
    if body.whisper_guard is not None and body.whisper_guard in config.WHISPER_GUARD_LAYERS:
        opts += ["--whisper-guard", str(int(body.whisper_guard))]
    if body.language and body.language in _INGEST_LANGUAGE_CODES:
        opts += ["--language", body.language]
    return opts


class ScanRequest(BaseModel):
    path: str

MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".insv", ".360", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".insv", ".360"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}
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

def _allowed_ingest_roots() -> list:
    """Approved roots for ingest scan / ingest endpoints.

    Default: PROJECT_ROOT (where arkiv 's own DB lives) + standard user media
    locations. Override with ARKIV_INGEST_ROOTS env (colon-separated).

    Codex Round-2 audit (J1): without bounds, /api/ingest/scan walked any path
    a Tailscale collaborator could supply, returning size + abs path of every
    media file — full filesystem inventory leak.
    """
    custom = os.environ.get("ARKIV_INGEST_ROOTS", "").strip()
    if custom:
        # os.pathsep, not ':' — Windows uses ';' AND ':' appears in drive letters
        # (C:\...), so splitting on ':' shredded every Windows path.
        return [Path(p).expanduser().resolve() for p in custom.split(os.pathsep) if p.strip()]
    home = Path.home()
    roots = [
        config.PROJECT_ROOT.resolve() if config.PROJECT_ROOT else None,
        (home / "Desktop").resolve(),
        (home / "Documents").resolve(),
        (home / "Movies").resolve(),
        (home / "Pictures").resolve(),
    ]
    # /Volumes/* (Mac SMB mounts of NAS shares) — allow each top-level mount
    volumes = Path("/Volumes")
    if volumes.exists():
        try:
            for vol in volumes.iterdir():
                if vol.is_dir():
                    resolved = vol.resolve()
                    # Skip a volume that resolves to the filesystem root (e.g.
                    # /Volumes/Macintosh HD → '/'): allowing '/' makes the J1
                    # bound a no-op — every path is then "under an approved root".
                    if str(resolved) != resolved.anchor:
                        roots.append(resolved)
        except OSError:
            pass
    # Final guard: never allow a bare filesystem/drive root through (defeats J1).
    return [r for r in roots if r is not None and str(r) != r.anchor]


def _assert_ingest_path_safe(target: Path) -> None:
    roots = _allowed_ingest_roots()
    canonical = target.resolve()
    for root in roots:
        try:
            canonical.relative_to(root)
            return
        except ValueError:
            continue
    raise HTTPException(
        403,
        f"ingest 路徑必須在批准的目錄底下：{[str(r) for r in roots]} (override via ARKIV_INGEST_ROOTS env)",
    )


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
    # Phase 9.7 G5③: the dialog's defaults come from the persisted settings
    # (library default), falling back to config. These are genuinely consumed —
    # IngestSetup pre-fills its pickers from them, and the vision model/num_ctx
    # are what an ingest run actually uses (ingest.py reads settings.effective).
    return {
        "whisper_modes": modes,
        "default_mode": settings_store.effective("transcription.default_mode"),
        "default_language": settings_store.effective("transcription.default_language"),
        "default_recursive": settings_store.effective("ingest.recursive"),
        "vision_model": settings_store.effective("vision.model"),
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
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

class OffloadPreviewRequest(BaseModel):
    src: str
    organize: Optional[str] = None
    include_heic: bool = False
    limit: int = 200  # cap the preview; a full card can be hundreds of files


class OffloadRequest(BaseModel):
    src: str
    dst: List[str]
    organize: Optional[str] = None
    include_heic: bool = False


@app.post("/api/offload/preview")
def offload_preview(
    body: OffloadPreviewRequest,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Read-only layout preview for the DIT Offload UI — shows source→dest mapping
    under the --organize template without copying anything."""
    import offload as _offload
    src = Path(body.src).expanduser().resolve()
    if not src.exists():
        raise HTTPException(400, "來源路徑不存在")
    # Clamp the limit server-side: it's client-controlled and a 0 / negative / huge
    # value would otherwise disable the cap and force full enumeration + exiftool +
    # a giant JSON on a large card (Codex). Always (0, 200].
    safe_limit = body.limit if 0 < body.limit <= 200 else 200
    try:
        return _offload.preview_layout(
            str(src), organize=body.organize, include_heic=body.include_heic,
            limit=safe_limit)
    except ValueError as exc:  # bad --organize template
        raise HTTPException(400, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/offload")
def offload_run(
    body: OffloadRequest,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Run a DIT offload (copy + hash-verify + MHL, never deletes source) with the
    --organize naming policy. Mirrors /api/ingest's subprocess pattern. Source/dest
    are arbitrary by design (card → backup drives); gated by videos_write (loopback
    is token-free, a remote read token is refused)."""
    import subprocess, sys
    src = Path(body.src).expanduser().resolve()
    if not src.exists():
        raise HTTPException(400, "來源路徑不存在")
    if not body.dst:
        raise HTTPException(400, "至少需要一個目的地 (dst)")
    cmd = [sys.executable, str(ROOT / "offload.py"), "--src", str(src), "--progress", "json"]
    for d in body.dst:
        cmd += ["--dst", d]
    if body.organize:
        cmd += ["--organize", body.organize]
    if body.include_heic:
        cmd += ["--include-heic"]
    # offload writes offload-state.json into its cwd; keep that out of the install
    # ROOT (it would dirty the repo / install dir on every UI run). Use the
    # project's .arkiv dir so the state is scoped + resumable per project.
    state_cwd = config.THUMBNAILS_DIR.parent
    state_cwd.mkdir(parents=True, exist_ok=True)

    def _stream():
        # Stream the offload's --progress json events line-by-line (ndjson) so the UI
        # shows live per-file progress instead of blocking on one giant request.
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1, cwd=str(state_cwd))
        saw_done = False
        import json as _json
        try:
            for line in proc.stdout:
                try:  # JSON parse (not substring) so a filename can't false-positive
                    if _json.loads(line).get("type") == "done":
                        saw_done = True
                except (ValueError, AttributeError):
                    pass
                yield line if line.endswith("\n") else line + "\n"
            proc.wait()
            if not saw_done:
                # offload exited before emitting its terminal event (e.g. a
                # ValueError/RuntimeError exit path) — synthesize one so the UI's
                # done-handler always fires instead of hanging on "running" (Codex).
                yield _json.dumps({"type": "done", "code": proc.returncode if proc.returncode is not None else 1,
                                   "summary": {}}, ensure_ascii=False) + "\n"
        except GeneratorExit:
            # client disconnected — stop the (resumable) offload and bound the wait
            # so a stalled child can't pin the worker forever (Codex).
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()  # reap to avoid a zombie if terminate timed out
            raise
        finally:
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@app.get("/dit")
def serve_dit():
    """Legacy DIT path — the standalone dit-offload.html island was ported into
    the SPA (Svelte cutover Phase 3). Redirect old bookmarks to the SPA route."""
    return RedirectResponse(url="/#/offload", status_code=308)


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
        raise HTTPException(400, f"找不到媒體檔案：{media_path}")
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
    return {"ok": True, "transcript_length": len(text), "language": active_lang}


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
        # audit M9: hold the lock across save/swap/restore so a concurrent
        # retry-vision can't snapshot the fallback as "original" and restore it
        # as the permanent primary model.
        with _vision_fallback_lock:
            original_model = vis.VISION_MODEL
            try:
                vis.VISION_MODEL = fallback_model
                retry_paths = [frame_paths[i] for i in failed]
                retry_results = vis.describe_frames(retry_paths)
                for idx, retry_r in zip(failed, retry_results):
                    if retry_r.get("description") and not retry_r.get("error"):
                        results[idx] = retry_r
            finally:
                vis.VISION_MODEL = original_model

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
        raise HTTPException(400, f"找不到媒體檔案：{media_path}")
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
    import subprocess, sys
    if not _acquire_ingest_slot():  # audit H3 — don't run concurrently with another ingest
        raise HTTPException(409, "已有匯入任務進行中，請稍候")
    try:
        # Single-file mode (ingest.py handles a file path as --dir). The old
        # `--dir <parent> --limit 1` re-processed the alphabetically-first file of
        # the folder, not this media — silently refreshing the WRONG row (audit H4).
        result = subprocess.run(
            [sys.executable, str(ROOT / "ingest.py"), "--dir", media_path, "--refresh"],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT)
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

def _dir_size_mb(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            # Skip un-stattable entries so a cache-size estimate never 500s. On
            # Windows the HF cache's snapshots/ are symlinks into blobs/ that
            # raise WinError 448 when symlink support is off — one bad link must
            # not crash /api/cache/info.
            continue
    return round(total / 1048576)


@app.get("/api/cache/info")
def cache_info(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """Show cache sizes.

    Phase 16.2: the absolute `path` of each cache dir is intentionally NOT
    returned — it leaked the operator's home/install layout to any videos_read
    token. The dict key labels each cache; sizes/counts are what the UI needs.
    """
    caches = {}
    # HuggingFace model cache
    hf_cache = Path.home() / ".cache" / "huggingface"
    if hf_cache.exists():
        caches["huggingface"] = {"size_mb": _dir_size_mb(hf_cache)}
    # Ollama models
    ollama_dir = Path.home() / ".ollama" / "models"
    if ollama_dir.exists():
        caches["ollama"] = {"size_mb": _dir_size_mb(ollama_dir)}
    # ChromaDB
    if config.CHROMA_PATH.exists():
        caches["chromadb"] = {"size_mb": _dir_size_mb(config.CHROMA_PATH)}
    # Thumbnails
    if config.THUMBNAILS_DIR.exists():
        count = sum(1 for _ in config.THUMBNAILS_DIR.glob("*"))
        caches["thumbnails"] = {"count": count, "size_mb": _dir_size_mb(config.THUMBNAILS_DIR)}
    # Browser-playback proxies (generated by ingest for HEVC/ProRes sources)
    if config.PROXIES_DIR.exists():
        count = sum(1 for _ in config.PROXIES_DIR.glob("*.mp4"))
        caches["proxies"] = {"count": count, "size_mb": _dir_size_mb(config.PROXIES_DIR)}
    # Waveform peak cache
    waveforms_dir = ROOT / "waveforms"
    if waveforms_dir.exists():
        count = sum(1 for _ in waveforms_dir.glob("*.json"))
        caches["waveforms"] = {"count": count, "size_mb": _dir_size_mb(waveforms_dir)}
    # Python __pycache__
    pycache = ROOT / "__pycache__"
    if pycache.exists():
        caches["pycache"] = {"size_mb": _dir_size_mb(pycache)}
    # Total
    total_mb = sum(c.get("size_mb", 0) for c in caches.values())
    return {"caches": caches, "total_mb": total_mb}


@app.post("/api/cache/clear")
def clear_cache(
    target: str = Query("app", description="app|thumbnails|chromadb|waveforms|all"),
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Clear caches. target: app (pycache+thumbnails+waveforms), thumbnails, chromadb, waveforms, all."""
    import shutil
    cleared = []
    if target in ("app", "thumbnails", "all"):
        thumbs = config.THUMBNAILS_DIR
        if thumbs.exists():
            files = list(thumbs.iterdir())
            for f in files:
                f.unlink(missing_ok=True)
            cleared.append(f"thumbnails ({len(files)} removed)")
    if target in ("app", "waveforms", "all"):
        waveforms_dir = ROOT / "waveforms"
        if waveforms_dir.exists():
            files = list(waveforms_dir.glob("*.json"))
            for f in files:
                f.unlink(missing_ok=True)
            cleared.append(f"waveforms ({len(files)} removed)")
    if target in ("app", "all"):
        pycache = ROOT / "__pycache__"
        if pycache.exists():
            shutil.rmtree(pycache, ignore_errors=True)
            cleared.append("__pycache__")
    if target in ("chromadb", "all"):
        if config.CHROMA_PATH.exists():
            shutil.rmtree(config.CHROMA_PATH, ignore_errors=True)
            cleared.append("chromadb")
    return {"ok": True, "cleared": cleared}


# ── Export ────────────────────────────────────────────────────────────────────

def _edl_reel(rec, stem):
    # CMX3600 reel: ASCII only, 8 chars, no control chars (would inject EDL lines).
    # Treat blank/whitespace-only reel_name as missing → stem fallback.
    raw = rec.get("reel_name")
    value = raw.strip() if isinstance(raw, str) and raw.strip() else stem
    # Strip ASCII control chars (0x00-0x1F + 0x7F) BEFORE ASCII conversion —
    # encode("ascii", "replace") would happily pass \r\n through, letting a
    # poisoned reel_name like "A001\r\nFCM: NONAME" inject EDL header lines.
    value = "".join(c for c in value if 0x20 <= ord(c) < 0x7F or ord(c) >= 0x80)
    value = value.encode("ascii", "replace").decode("ascii")
    return value[:8].ljust(8)


def _media_streams(rec: dict):
    """(has_video, has_audio) for a record, from its probed streams. A clip with
    width/height is video; has_audio flags an audio track. Used so timeline
    exports describe audio-only clips correctly instead of claiming video."""
    has_video = bool(rec.get("width") or rec.get("height"))
    has_audio = bool(rec.get("has_audio"))
    # Degenerate row with neither flag → assume video so we still emit something.
    if not has_video and not has_audio:
        has_video = True
    return has_video, has_audio


def _edl_comment(text: str) -> str:
    """Sanitize a string for an EDL comment line. Strips ASCII control chars
    (incl. CR/LF) so a filename like "shot\\nFCM: ..." can't inject extra EDL
    lines, while keeping printable ASCII and non-ASCII (CJK filenames)."""
    if not text:
        return ""
    return "".join(c for c in text if 0x20 <= ord(c) < 0x7F or ord(c) >= 0x80)


def _log_safe(text: str, limit: int) -> str:
    """Strip control chars (newlines, ANSI/terminal escapes) and truncate, so a
    value printed to the server terminal/log can't forge lines or fill disk."""
    if not text:
        return ""
    cleaned = "".join(c for c in text if c == " " or (0x20 <= ord(c) < 0x7F) or ord(c) >= 0x80)
    return cleaned[:limit]


def _subtitle_ts(seconds: float, sep: str = ",") -> str:
    """Subtitle timecode (SRT/VTT): HH:MM:SS,mmm (sep ',' for SRT, '.' for VTT)."""
    seconds = max(0.0, seconds)  # negative TC would render garbage frames
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _subtitle_text(text: str) -> str:
    """Sanitize a subtitle cue body: collapse newlines/blank lines (which would
    start a new cue) onto one line and neutralize a literal `-->` so transcript
    text can't inject a fake cue boundary/timecode."""
    if not text:
        return ""
    one_line = " ".join(text.split())  # collapses any \n / \r / blank runs
    return one_line.replace("-->", "->")


def _edl_timecode(seconds: float, fps: float, drop_frame: bool = False) -> str:
    """EDL timecode: HH:MM:SS:FF (NDF) or HH:MM:SS;FF (DF)."""
    if fps <= 0:
        fps = 30.0
    int_fps = round(fps)
    total_frames = round(seconds * fps)

    if drop_frame and int_fps in (30, 60):
        # Drop-frame: skip frame 0,1 (30p) or 0,1,2,3 (60p) each minute except every 10th
        d = 2 if int_fps == 30 else 4
        frames_per_min = int_fps * 60 - d
        frames_per_10min = frames_per_min * 10 + d

        tens = total_frames // frames_per_10min
        rem = total_frames % frames_per_10min

        if rem < int_fps * 60:
            adjusted = total_frames + d * 9 * tens
        else:
            adjusted = total_frames + d * 9 * tens + d * ((rem - int_fps * 60) // frames_per_min + 1)

        ff = adjusted % int_fps
        ss = (adjusted // int_fps) % 60
        mm = (adjusted // (int_fps * 60)) % 60
        hh = adjusted // (int_fps * 3600)
    else:
        ff = total_frames % int_fps
        remaining = total_frames // int_fps
        ss = remaining % 60
        remaining //= 60
        mm = remaining % 60
        hh = remaining // 60

    sep = ";" if drop_frame else ":"
    return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


def _start_tc_seconds(rec: dict, clip_fps: float) -> float:
    """Parse a record's camera body start timecode (HH:MM:SS:FF) into seconds."""
    start_tc_str = rec.get("start_tc") or ""
    if not start_tc_str:
        return 0.0
    _tc = start_tc_str.replace(";", ":").split(":")
    if len(_tc) == 4:
        try:
            _h, _m, _s, _f = int(_tc[0]), int(_tc[1]), int(_tc[2]), int(_tc[3])
            return _h * 3600 + _m * 60 + _s + _f / clip_fps
        except (ValueError, ZeroDivisionError):
            return 0.0
    return 0.0


def _edl_fps_warning(recs: list, tl_fps: float) -> "str | None":
    """B13: EDL comment warning when clips carry differing frame rates.

    A timeline's record/sequence TC assumes a single rate (tl_fps = the first
    clip's), so mixing rates drifts the record TC against clips that aren't at
    tl_fps (per-clip SOURCE TC stays exact — it's computed in each clip's own
    rate). Return an EDL comment line so the editor sees this on import, or None
    when every clip shares one rate. Pure (no I/O) so it's unit-testable."""
    rates = {round(float(r.get("fps") or tl_fps), 3) for r in recs}
    if len(rates) <= 1:
        return None
    listed = ", ".join(f"{r:g}" for r in sorted(rates))
    return (
        f"* WARNING: mixed frame rates ({listed}) — timeline record TC assumes "
        f"{float(tl_fps):g}; per-clip source TC preserved."
    )


def _attachment_headers(stem: str, ext: str) -> dict:
    """Content-Disposition for a download, safe for non-ASCII (e.g. CJK) filenames.

    Starlette encodes response headers as latin-1, so a raw
    f'attachment; filename="{stem}.{ext}"' whose stem contains CJK characters
    raises UnicodeEncodeError → 500 (broke batch + single-clip export for every
    中日韓-named clip). Per RFC 6266/5987 we emit an ASCII-only `filename`
    fallback (non-ASCII + quote/backslash → "_") plus a percent-encoded
    `filename*` carrying the real UTF-8 name, which every modern client — and the
    Tauri WKWebView — prefers."""
    from urllib.parse import quote

    name = f"{stem}.{ext}"
    ascii_fallback = _re.sub(r'[^\x20-\x7e]|["\\]', "_", name)
    # safe="" so a "/" in the name is percent-encoded too — RFC 5987 ext-value
    # forbids a raw "/" (not an attr-char), and quote()'s default leaves it bare.
    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_fallback}"; '
            f"filename*=UTF-8''{quote(name, safe='')}"
        )
    }


@app.get("/api/media/{media_id}/export/{fmt}")
def export_media(
    media_id: int,
    fmt: str,
    in_s: Optional[float] = None,
    out_s: Optional[float] = None,
    _tok: dict = Depends(require_scopes("media_read")),
):
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    transcript = rec.get("transcript", "") or ""
    filename = rec.get("filename", f"media_{media_id}")
    stem = filename.rsplit(".", 1)[0]
    full_duration = rec.get("duration_s", 0) or 0

    # Normalize trim window: [trim_in, trim_out] in seconds, duration = trim_out - trim_in
    trim_in = max(0.0, float(in_s)) if in_s is not None else 0.0
    trim_out = min(full_duration, float(out_s)) if out_s is not None else full_duration
    if trim_out <= trim_in:
        trim_in, trim_out = 0.0, full_duration
    has_trim = trim_in > 0.05 or trim_out < full_duration - 0.05
    duration = trim_out - trim_in

    # TC helpers are module-level (shared with the batch-timeline endpoint).
    _ts = _subtitle_ts
    _edl_tc = _edl_timecode

    # Try to use segment-aligned timestamps if available
    import json as _json
    _seg_json = rec.get("segments_json")
    _segments = []
    if _seg_json:
        try:
            _segments = _json.loads(_seg_json)
        except Exception:
            pass

    # When trimmed, keep only segments that overlap [trim_in, trim_out] and
    # rebase their timestamps so the output starts at 0.
    if has_trim and _segments:
        trimmed = []
        for seg in _segments:
            s, e = seg.get("start", 0), seg.get("end", 0)
            if e <= trim_in or s >= trim_out:
                continue
            trimmed.append({
                **seg,
                "start": max(0.0, s - trim_in),
                "end": min(duration, e - trim_in),
            })
        _segments = trimmed

    if fmt == "txt":
        if has_trim:
            # Only text from segments within the trim window. With no segment data
            # we can't trim plain text by time, so the export is empty by design.
            content = "\n".join(seg.get("text", "").strip() for seg in _segments if seg.get("text"))
        else:
            content = transcript
        return HTMLResponse(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers=_attachment_headers(stem, "txt"),
        )

    if fmt == "srt":
        srt = ""
        if _segments:
            # Segment-aligned timestamps (precise). .get() tolerates legacy
            # segment dicts missing keys; _subtitle_text blocks cue injection.
            i = 1
            for seg in _segments:
                text = _subtitle_text(seg.get("text") or "")
                if not text:
                    continue
                srt += f"{i}\n{_ts(seg.get('start', 0) or 0)} --> {_ts(seg.get('end', 0) or 0)}\n{text}\n\n"
                i += 1
        else:
            # Fallback: evenly distributed
            lines = [l.strip() for l in transcript.split("\n") if l.strip()]
            for i, line in enumerate(lines, 1):
                t_start = (i - 1) * (duration / max(len(lines), 1))
                t_end = i * (duration / max(len(lines), 1))
                srt += f"{i}\n{_ts(t_start)} --> {_ts(t_end)}\n{_subtitle_text(line)}\n\n"
        return HTMLResponse(
            content=srt,
            media_type="text/plain; charset=utf-8",
            headers=_attachment_headers(stem, "srt"),
        )

    if fmt == "vtt":
        vtt = "WEBVTT\n\n"
        if _segments:
            for seg in _segments:
                text = _subtitle_text(seg.get("text") or "")
                if not text:
                    continue
                vtt += f"{_ts(seg.get('start', 0) or 0, '.')} --> {_ts(seg.get('end', 0) or 0, '.')}\n{text}\n\n"
        else:
            lines = [l.strip() for l in transcript.split("\n") if l.strip()]
            for i, line in enumerate(lines, 1):
                t_start = (i - 1) * (duration / max(len(lines), 1))
                t_end = i * (duration / max(len(lines), 1))
                vtt += f"{_ts(t_start, '.')} --> {_ts(t_end, '.')}\n{_subtitle_text(line)}\n\n"
        return HTMLResponse(
            content=vtt,
            media_type="text/plain; charset=utf-8",
            headers=_attachment_headers(stem, "vtt"),
        )

    if fmt in ("edl", "edl-markers"):
        # CMX3600 EDL — full clip + optional frame markers
        clip_fps = rec.get("fps") or 30.0
        # 29.97/59.94 are drop-frame by convention
        is_df = round(clip_fps, 2) in (29.97, 59.94)
        fcm = "DROP FRAME" if is_df else "NON-DROP FRAME"

        # Camera body start timecode (may not be 00:00:00:00)
        start_tc_str = rec.get("start_tc") or ""
        start_tc_offset = 0.0
        if start_tc_str:
            # Parse HH:MM:SS:FF or HH:MM:SS;FF into seconds
            _tc = start_tc_str.replace(";", ":").split(":")
            if len(_tc) == 4:
                try:
                    _h, _m, _s, _f = int(_tc[0]), int(_tc[1]), int(_tc[2]), int(_tc[3])
                    start_tc_offset = _h * 3600 + _m * 60 + _s + _f / clip_fps
                except (ValueError, ZeroDivisionError):
                    start_tc_offset = 0.0

        # Source TC = camera start TC + offset into clip (shifted by trim_in when trimmed)
        src_start = _edl_tc(start_tc_offset + trim_in, clip_fps, is_df)
        src_end = _edl_tc(start_tc_offset + trim_in + duration, clip_fps, is_df)
        # Record TC = timeline position (starts at 01:00:00:00 by convention)
        rec_base = 3600.0  # 01:00:00:00
        rec_start = _edl_tc(rec_base, clip_fps, is_df)
        rec_end = _edl_tc(rec_base + duration, clip_fps, is_df)

        edl = f"TITLE: {_edl_comment(stem)}\nFCM: {fcm}\n\n"
        reel = _edl_reel(rec, stem)
        edl += f"001  {reel} V     C        {src_start} {src_end} {rec_start} {rec_end}\n"
        edl += f"* FROM CLIP NAME: {_edl_comment(filename)}\n"
        if start_tc_str:
            edl += f"* SOURCE START TC: {_edl_comment(start_tc_str)}\n"
        edl += "\n"

        if fmt == "edl-markers":
            # LOC comments — DaVinci reads these via "Import > Timeline Markers from EDL"
            colors = ["RED", "BLUE", "GREEN", "CYAN", "MAGENTA", "YELLOW", "WHITE"]
            frames = db.get_frames(media_id)
            kept = 0
            for fr in frames:
                marker_offset = fr["timestamp_s"]
                if marker_offset < trim_in or marker_offset > trim_out:
                    continue
                rtc = _edl_tc(rec_base + (marker_offset - trim_in), clip_fps, is_df)
                # Strip non-ASCII for DaVinci compatibility (no UTF-8 in EDL markers)
                desc = (fr.get("description") or f"Frame {fr['frame_index']+1}")
                desc = desc.encode("ascii", "replace").decode("ascii")[:60]
                color = colors[kept % len(colors)]
                edl += f"* LOC: {rtc} {color} {desc}\n"
                kept += 1

        return HTMLResponse(
            content=edl,
            media_type="text/plain; charset=utf-8",
            headers=_attachment_headers(stem, "edl"),
        )

    if fmt == "fcpxml":
        # FCPXML 1.8 — max compatibility: FCPX 10.4+, DaVinci 17+, Premiere via XtoCC
        clip_fps = rec.get("fps") or 30.0

        # Rational frame duration for FCPXML (must be exact, not rounded)
        _fps_map = {
            23.98: ("1001", "24000"), 23.976: ("1001", "24000"),
            29.97: ("1001", "30000"), 59.94: ("1001", "60000"),
        }
        rounded_fps = round(clip_fps, 2)
        if rounded_fps in _fps_map:
            _num, _den = _fps_map[rounded_fps]
        else:
            _num, _den = "1", str(round(clip_fps))

        # Drop frame for NTSC rates
        is_df = rounded_fps in (29.97, 59.94)
        tc_fmt = "DF" if is_df else "NDF"

        # Asset references the full file on disk; the timeline clip uses the trim window.
        asset_dur_frames = round(full_duration * clip_fps)
        clip_dur_frames = round(duration * clip_fps)

        # Camera body start timecode
        start_tc_str = rec.get("start_tc") or "00:00:00:00"
        start_tc_offset = 0.0
        _tc = start_tc_str.replace(";", ":").split(":")
        if len(_tc) == 4:
            try:
                _h, _m, _s, _f = int(_tc[0]), int(_tc[1]), int(_tc[2]), int(_tc[3])
                start_tc_offset = _h * 3600 + _m * 60 + _s + _f / clip_fps
            except (ValueError, ZeroDivisionError):
                pass

        from xml.sax.saxutils import escape as xml_esc
        import pathlib
        # Attribute escaping must also cover the double quote (xml_esc leaves it
        # alone by default), or a filename like `cam "A".mp4` breaks name="..." /
        # src="..." — same protection the batch timeline path uses.
        _attr = lambda s: xml_esc(s, {'"': "&quot;"})

        # Build file URI with proper file:/// prefix
        raw_path = _resolve_media_path(rec.get("path", ""))
        file_uri = pathlib.PurePosixPath(raw_path.replace("\\", "/"))
        if not str(file_uri).startswith("/"):
            file_uri = pathlib.PurePosixPath("/" + str(file_uri))
        file_uri_str = _attr(f"file://{file_uri}")

        # Build marker elements from frame analysis (filter to trim window, rebase to clip start)
        markers_xml = ""
        frames = db.get_frames(media_id)
        colors = ["Blue", "Red", "Green", "Cyan", "Magenta", "Yellow", "White"]
        kept = 0
        for fr in frames:
            ts = fr["timestamp_s"]
            if ts < trim_in or ts > trim_out:
                continue
            offset_frames = round((ts - trim_in) * clip_fps)
            desc = xml_esc((fr.get("description") or f"Frame {fr['frame_index']+1}")[:60],
                           {'"': '&quot;'})
            color = colors[kept % len(colors)]
            markers_xml += f'                <marker start="{offset_frames * int(_num)}/{_den}s" duration="{_num}/{_den}s" value="{desc}" />\n'
            kept += 1

        # asset-clip start = where in the asset to begin reading (camera TC + trim_in)
        clip_start_frames = round((start_tc_offset + trim_in) * clip_fps)

        fcpxml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
    <resources>
        <format id="r1" frameDuration="{_num}/{_den}s" width="{rec.get('width') or 1920}" height="{rec.get('height') or 1080}" />
        <asset id="r2" name="{_attr(stem)}" src="{file_uri_str}" start="0s" duration="{asset_dur_frames * int(_num)}/{_den}s" format="r1" hasAudio="1" hasVideo="1" />
    </resources>
    <library>
        <event name="arkiv Export">
            <project name="{_attr(stem)}">
                <sequence format="r1" tcStart="0s" tcFormat="{tc_fmt}" duration="{clip_dur_frames * int(_num)}/{_den}s">
                    <spine>
                        <asset-clip ref="r2" name="{_attr(filename)}" offset="0s" duration="{clip_dur_frames * int(_num)}/{_den}s" start="{clip_start_frames * int(_num)}/{_den}s" tcFormat="{tc_fmt}">
{markers_xml}                        </asset-clip>
                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>"""

        return HTMLResponse(
            content=fcpxml,
            media_type="application/xml; charset=utf-8",
            headers=_attachment_headers(stem, "fcpxml"),
        )

    raise HTTPException(400, f"不支援的格式：{fmt}。請使用 srt/vtt/txt/edl/edl-markers/fcpxml")


class ExportToRequest(BaseModel):
    fmt: str
    dest: str
    in_s: Optional[float] = None
    out_s: Optional[float] = None

@app.post("/api/media/{media_id}/export-to")
def export_to_file(
    media_id: int,
    body: ExportToRequest,
    # writes a file to a caller-chosen local path → require write scope (audit H10).
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Export and write directly to a local path (for Tauri native save dialog).

    Codex Round-2 Critical fix：原本內聯一份過時的 _blocked denylist（漏掉
    ~/.ssh / ~/Library/LaunchAgents 等敏感位置），改走 _assert_export_dest_safe
    共用 helper（allowlist of approved user export roots + 副檔名白名單）。
    """
    resp = export_media(media_id, body.fmt, in_s=body.in_s, out_s=body.out_s)
    content = resp.body.decode("utf-8")
    dest = Path(body.dest).expanduser().resolve()
    _assert_export_dest_safe(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return {"ok": True, "path": str(dest), "size": dest.stat().st_size}


def _fcpxml_rational(fps: float):
    """FCPXML frameDuration numerator/denominator for a frame rate (exact for NTSC)."""
    _fps_map = {
        23.98: ("1001", "24000"), 23.976: ("1001", "24000"),
        29.97: ("1001", "30000"), 59.94: ("1001", "60000"),
    }
    rounded = round(fps, 2)
    if rounded in _fps_map:
        return _fps_map[rounded]
    return "1", str(round(fps))


class BatchExportRequest(BaseModel):
    # Phase 12.4: zip several clips' per-clip exports (one subtitle/transcript
    # file each). For a single stitched timeline use /api/export/timeline.
    ids: List[int]
    fmt: str = "srt"


@app.post("/api/export/batch")
def export_batch(
    body: BatchExportRequest,
    _tok: dict = Depends(require_scopes("media_read")),
):
    """Bundle the per-clip export (`/api/media/{id}/export/{fmt}`) of many clips
    into one .zip — one file per clip. Reuses the single-clip builder verbatim so
    the formats + content stay identical. Missing ids are skipped."""
    import io
    import zipfile

    fmt = (body.fmt or "").lower()
    allowed = {"txt", "srt", "vtt", "edl", "edl-markers", "fcpxml"}
    if fmt not in allowed:
        raise HTTPException(422, "unsupported fmt: {0} (use {1})".format(fmt, "/".join(sorted(allowed))))
    if not body.ids:
        raise HTTPException(422, "ids must be a non-empty list")

    ext = "edl" if fmt == "edl-markers" else fmt
    buf = io.BytesIO()
    used: dict = {}
    written = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for mid in body.ids:
            rec = db.get_record_by_id(mid)
            if not rec:
                continue
            resp = export_media(media_id=mid, fmt=fmt, _tok=_tok)  # _tok is gate-only
            content = resp.body if isinstance(resp.body, (bytes, bytearray)) else str(resp.body).encode("utf-8")
            stem = (rec.get("filename") or "media_{0}".format(mid)).rsplit(".", 1)[0]
            arcname = "{0}.{1}".format(stem, ext)
            # de-collide duplicate stems so no file is silently overwritten
            n = used.get(arcname, 0)
            used[arcname] = n + 1
            if n:
                arcname = "{0}_{1}.{2}".format(stem, n, ext)
            zf.writestr(arcname, content)
            written += 1
    if written == 0:
        raise HTTPException(404, "none of the requested ids exist")
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="arkiv-export.zip"'},
    )


@app.get("/api/export/timeline/{fmt}")
def export_timeline(
    fmt: str,
    ids: str,
    _tok: dict = Depends(require_scopes("media_read")),
):
    """Lay several clips end-to-end on ONE timeline and export it.

    Unlike /api/media/{id}/export/{fmt} (single clip), this sequences the given
    clips in the order supplied so a filmmaker can multi-select in the grid and
    drop a single EDL / FCPXML / SRT into Resolve.

    ids: comma-separated media ids, e.g. ?ids=3,1,7 — order is preserved, and a
    repeated id places the same clip twice. Mixed frame rates: the timeline uses
    the FIRST clip's rate for record/sequence timecode (EDL source TC stays in
    each clip's own rate); same-camera footage (the common case) is exact.
    """
    fmt = (fmt or "").lower()
    if fmt not in ("edl", "srt", "fcpxml"):
        raise HTTPException(400, f"批次匯出僅支援 edl / srt / fcpxml，收到：{fmt}")

    try:
        id_list = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "ids 必須是逗號分隔的整數，例如 ids=3,1,7")
    if not id_list:
        raise HTTPException(400, "ids 不可為空")
    if len(id_list) > 500:
        raise HTTPException(400, "一次最多 500 支")

    recs = []
    missing = []
    for mid in id_list:
        rec = db.get_record_by_id(mid)
        if rec:
            recs.append(rec)
        else:
            missing.append(mid)
    # Fail loud on ANY missing id rather than silently shipping a short timeline.
    # A clip deleted/moved between selection and export would otherwise produce a
    # timeline missing a shot with no warning (Codex review P2).
    if missing:
        raise HTTPException(404, f"找不到素材：{','.join(str(m) for m in missing)}")

    tl_fps = recs[0].get("fps") or 30.0
    tl_is_df = round(tl_fps, 2) in (29.97, 59.94)

    if fmt == "edl":
        fcm = "DROP FRAME" if tl_is_df else "NON-DROP FRAME"
        edl = f"TITLE: arkiv timeline\nFCM: {fcm}\n"
        fps_warn = _edl_fps_warning(recs, tl_fps)  # B13
        if fps_warn:
            edl += fps_warn + "\n"
        edl += "\n"
        rec_pos = 3600.0  # timeline starts at 01:00:00:00 by convention
        for i, rec in enumerate(recs, 1):
            filename = rec.get("filename", f"media_{rec.get('id')}")
            stem = filename.rsplit(".", 1)[0]
            dur = rec.get("duration_s", 0) or 0
            clip_fps = rec.get("fps") or tl_fps
            clip_is_df = round(clip_fps, 2) in (29.97, 59.94)
            src_off = _start_tc_seconds(rec, clip_fps)
            src_start = _edl_timecode(src_off, clip_fps, clip_is_df)
            src_end = _edl_timecode(src_off + dur, clip_fps, clip_is_df)
            rec_start = _edl_timecode(rec_pos, tl_fps, tl_is_df)
            rec_end = _edl_timecode(rec_pos + dur, tl_fps, tl_is_df)
            reel = _edl_reel(rec, stem)
            has_vid, _ = _media_streams(rec)
            chan = "V" if has_vid else "A"  # audio-only clip → audio channel
            edl += f"{i:03d}  {reel} {chan}     C        {src_start} {src_end} {rec_start} {rec_end}\n"
            edl += f"* FROM CLIP NAME: {_edl_comment(filename)}\n"
            if rec.get("start_tc"):
                edl += f"* SOURCE START TC: {_edl_comment(rec['start_tc'])}\n"
            edl += "\n"
            rec_pos += dur
        return HTMLResponse(
            content=edl,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="arkiv-timeline.edl"'},
        )

    if fmt == "srt":
        import json as _json
        srt = ""
        idx = 1
        offset = 0.0  # cumulative timeline position in seconds
        for rec in recs:
            dur = rec.get("duration_s", 0) or 0
            segs = []
            if rec.get("segments_json"):
                try:
                    segs = _json.loads(rec["segments_json"])
                except Exception:
                    segs = []
            if segs:
                for seg in segs:
                    s = offset + (seg.get("start", 0) or 0)
                    e = offset + (seg.get("end", 0) or 0)
                    text = _subtitle_text(seg.get("text") or "")
                    if not text:
                        continue
                    srt += f"{idx}\n{_subtitle_ts(s)} --> {_subtitle_ts(e)}\n{text}\n\n"
                    idx += 1
            else:
                # No segment timestamps (legacy rows / segmentless transcription):
                # mirror the single-clip /export/srt fallback and distribute the
                # transcript lines evenly across the clip, offset onto the timeline
                # (Codex review P2 — otherwise transcript-only clips vanish).
                lines = [l.strip() for l in (rec.get("transcript") or "").split("\n") if l.strip()]
                n = max(len(lines), 1)
                for li, line in enumerate(lines):
                    s = offset + li * (dur / n)
                    e = offset + (li + 1) * (dur / n)
                    srt += f"{idx}\n{_subtitle_ts(s)} --> {_subtitle_ts(e)}\n{_subtitle_text(line)}\n\n"
                    idx += 1
            offset += dur
        return HTMLResponse(
            content=srt,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="arkiv-timeline.srt"'},
        )

    # fmt == "fcpxml"
    from xml.sax.saxutils import escape as xml_esc
    import pathlib
    # Attribute escaping must also cover the double quote, or a filename like
    # `cam "A".mp4` breaks the name="..." attribute → malformed XML (Codex P2).
    _attr = lambda s: xml_esc(s, {'"': "&quot;"})
    _num, _den = _fcpxml_rational(tl_fps)
    tc_fmt = "DF" if tl_is_df else "NDF"

    assets_xml = ""
    spine_xml = ""
    offset_frames = 0
    total_frames = 0
    for i, rec in enumerate(recs):
        ref = f"r{i + 2}"  # r1 is the format
        filename = rec.get("filename", f"media_{rec.get('id')}")
        stem = filename.rsplit(".", 1)[0]
        dur = rec.get("duration_s", 0) or 0
        clip_fps = rec.get("fps") or tl_fps
        # Every asset references format r1 (the timeline rate), so ALL durations
        # and offsets must be expressed in the timeline timebase — otherwise a
        # mixed-rate clip's frame count would be serialized against the wrong
        # frameDuration and decode to the wrong seconds (Codex review P2). The
        # start TC string is parsed with the clip's own fps (correct), then
        # converted to timeline frames. asset duration == clip duration so the
        # asset is never shorter than the span the asset-clip reads.
        dur_frames = round(dur * tl_fps)
        asset_dur_frames = dur_frames
        src_off_frames = round(_start_tc_seconds(rec, clip_fps) * tl_fps)

        raw_path = _resolve_media_path(rec.get("path", ""))
        file_uri = pathlib.PurePosixPath(raw_path.replace("\\", "/"))
        if not str(file_uri).startswith("/"):
            file_uri = pathlib.PurePosixPath("/" + str(file_uri))
        file_uri_str = _attr(f"file://{file_uri}")

        # asset.start = the media's own start timecode (camera TC). The asset
        # therefore spans [src_off, src_off + duration], so the asset-clip's
        # start=src_off below sits at the head of that range rather than hours
        # past the end of a 0s-anchored asset (Codex review P2).
        has_vid, has_aud = _media_streams(rec)
        assets_xml += (
            f'        <asset id="{ref}" name="{_attr(stem)}" src="{file_uri_str}" '
            f'start="{src_off_frames * int(_num)}/{_den}s" '
            f'duration="{asset_dur_frames * int(_num)}/{_den}s" '
            f'format="r1" hasAudio="{1 if has_aud else 0}" hasVideo="{1 if has_vid else 0}" />\n'
        )
        spine_xml += (
            f'                    <asset-clip ref="{ref}" name="{_attr(filename)}" '
            f'offset="{offset_frames * int(_num)}/{_den}s" '
            f'duration="{dur_frames * int(_num)}/{_den}s" '
            f'start="{src_off_frames * int(_num)}/{_den}s" tcFormat="{tc_fmt}" />\n'
        )
        offset_frames += dur_frames
        total_frames += dur_frames

    w = recs[0].get("width") or 1920
    h = recs[0].get("height") or 1080
    fcpxml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
    <resources>
        <format id="r1" frameDuration="{_num}/{_den}s" width="{w}" height="{h}" />
{assets_xml}    </resources>
    <library>
        <event name="arkiv Export">
            <project name="arkiv timeline">
                <sequence format="r1" tcStart="0s" tcFormat="{tc_fmt}" duration="{total_frames * int(_num)}/{_den}s">
                    <spine>
{spine_xml}                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>"""
    return HTMLResponse(
        content=fcpxml,
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="arkiv-timeline.fcpxml"'},
    )


# ── Admin Tokens ─────────────────────────────────────────────────────────────


@app.post("/api/admin/tokens")
def admin_create_token(
    req: CreateTokenRequest,
    _tok: dict = Depends(require_scopes("admin")),
):
    try:
        return admin.create_token(
            name=req.name,
            scopes=req.scopes,
            description=req.description,
            expires_in_days=req.expires_in_days,
            allowed_ips=req.allowed_ips,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/admin/tokens")
def admin_list_tokens(
    _tok: dict = Depends(require_scopes("admin")),
):
    return {"tokens": admin.list_tokens()}


@app.get("/api/admin/tokens/{token_id}")
def admin_get_token(
    token_id: str,
    _tok: dict = Depends(require_scopes("admin")),
):
    token = admin.get_token(token_id)
    if not token:
        raise HTTPException(404, "Token not found")
    return token


@app.delete("/api/admin/tokens/{token_id}")
def admin_revoke_token(
    token_id: str,
    _tok: dict = Depends(require_scopes("admin")),
):
    if not admin.revoke_token(token_id):
        raise HTTPException(404, "Token not found")
    return {"ok": True, "deleted": token_id}


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

import mimetypes

@app.get("/api/stream/{media_id}")
def stream_media(media_id: int, _tok: dict = Depends(require_scopes("videos_read"))):
    """Stream a media file with range request support for seeking.
    Serves H.264 proxy if available (for browser-incompatible codecs like ProRes/HEVC).
    """
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到媒體")
    # Proxy filename is hash-scoped by absolute source path so that a
    # proxies/ directory copied between installations cannot serve another
    # user's content under the same media_id.
    resolved_src = _resolve_media_path(rec["path"])
    proxy_path = config.proxy_path_for(media_id, resolved_src)
    if proxy_path.exists():
        return FileResponse(
            path=str(proxy_path),
            media_type="video/mp4",
            filename=Path(resolved_src).stem + "_proxy.mp4",
        )
    file_path = Path(resolved_src)
    if not file_path.exists():
        raise HTTPException(404, "找不到檔案")
    # Only serve known media extensions
    if file_path.suffix.lower() not in MEDIA_EXTS:
        raise HTTPException(403, "不是媒體檔案")

    # Phase 7.7g: HEVC/ProRes 沒對應 proxy 時不要 silently 送原檔（Chrome/WKWebView
    # 都播不出來，使用者只看到「無法播放」），改回 409 + JSON，前端可 surface
    # 「需先建 proxy」的引導 + POST /api/proxy/build 觸發背景生成。
    # tri-state: NEEDED → 409；NOT_NEEDED / UNKNOWN（ffprobe 失敗、binary 缺、
    # NAS unreachable）→ fall through，維持送原檔的舊 fallback 行為。
    # audit M24: use the codec persisted at ingest instead of re-running ffprobe
    # on EVERY playback; only probe when the column is NULL (legacy rows) and
    # backfill so the next play is probe-free. None (probe failed / audio-only)
    # keeps the UNKNOWN fall-through behavior.
    stored_codec = (rec.get("codec") or "").strip().lower() or None
    if stored_codec is None:
        stored_codec = codec.probe_codec(str(file_path))
        if stored_codec:
            try:
                with db.get_conn() as conn:
                    conn.execute("UPDATE media SET codec=? WHERE id=?", (stored_codec, media_id))
            except Exception:
                pass  # backfill is best-effort; playback must not fail on it
    if stored_codec and stored_codec in codec.PROXY_CODECS:
        return JSONResponse(
            status_code=409,
            content={
                "need_proxy": True,
                "media_id": media_id,
                "filename": rec.get("filename"),
                "reason": "browser-incompatible codec (HEVC/ProRes); proxy required for playback",
                "hint": "POST /api/proxy/build to queue proxy generation",
            },
        )

    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        mime = "video/mp4"
    return FileResponse(
        path=str(file_path),
        media_type=mime,
        filename=file_path.name,
    )


# ── Proxy Management ─────────────────────────────────────────────────────────

# PROXY_CODECS lives in codec.py — single source of truth.

def _assert_same_site(request: Request) -> None:
    """audit M14: the no-body POSTs below are CORS 'simple requests' — a
    malicious page can fire them cross-site WITHOUT a preflight, and
    loopback-trust then authorizes them (whole-library rebuild / proxy-build
    DoS). Browsers attach Sec-Fetch-Site and/or Origin on cross-site POSTs;
    non-browser clients (curl, scripts) send neither and pass through."""
    sfs = request.headers.get("sec-fetch-site")
    if sfs and sfs not in ("same-origin", "same-site", "none"):
        raise HTTPException(403, "cross-site request rejected")
    origin = request.headers.get("origin")
    if not origin:
        return  # non-browser client
    if origin in _ALLOWED_ORIGINS:
        return
    if origin != "null" and origin.split("://", 1)[-1] == request.headers.get("host", ""):
        return  # same-origin for whatever host/port this deployment uses
    raise HTTPException(403, "cross-site request rejected")

@app.get("/api/proxy/status")
def proxy_status(_tok: dict = Depends(require_scopes("videos_read"))):
    """Check proxy status for all media files."""
    proxy_dir = config.PROXIES_DIR
    proxy_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM media").fetchall()
    proxied = sum(
        1 for r in rows
        if config.proxy_path_for(r["id"], _resolve_media_path(r["path"])).exists()
    )
    size_mb = round(sum(p.stat().st_size for p in proxy_dir.glob("*.mp4")) / 1048576, 1)
    return {"total": len(rows), "proxied": proxied, "size_mb": size_mb}


@app.post("/api/proxy/build")
def proxy_build(request: Request, background_tasks: BackgroundTasks, _tok: dict = Depends(require_scopes("ingest_write"))):
    """Queue proxy generation for all HEVC/ProRes files without proxy."""
    _assert_same_site(request)  # audit M14
    proxy_dir = config.PROXIES_DIR
    proxy_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM media").fetchall()
    to_build = [
        dict(r) for r in rows
        if not config.proxy_path_for(r["id"], _resolve_media_path(r["path"])).exists()
    ]
    if not to_build:
        return {"message": "全部 proxy 已存在", "queued": 0}
    background_tasks.add_task(_build_proxies, to_build)
    return {"message": f"開始生成 {len(to_build)} 個 proxy（背景執行）", "queued": len(to_build)}


@app.post("/api/proxy/build/{media_id}")
def proxy_build_one(media_id: int, request: Request, background_tasks: BackgroundTasks, _tok: dict = Depends(require_scopes("ingest_write"))):
    """Per-id proxy build — surface 自 7.7g 409 「生成 proxy」按鈕，使用者點到
    哪個 HEVC 就只建那個，避免 build all 整庫拖時間。"""
    _assert_same_site(request)  # audit M14
    proxy_dir = config.PROXIES_DIR
    proxy_dir.mkdir(parents=True, exist_ok=True)
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到媒體")
    src = _resolve_media_path(rec["path"])
    if config.proxy_path_for(media_id, src).exists():
        return {"message": "proxy 已存在", "queued": 0, "media_id": media_id}
    background_tasks.add_task(_build_proxies, [{"id": media_id, "path": rec["path"]}])
    return {
        "message": f"開始生成 proxy（背景執行）",
        "queued": 1,
        "media_id": media_id,
        "filename": rec.get("filename"),
    }


def _build_proxies(items: list):
    """Background task: generate H.264 proxy for each file."""
    import ingest
    for item in items:
        src = _resolve_media_path(item["path"])
        try:
            result = ingest.generate_proxy(item["id"], src)
            if not result:
                print(f"[proxy] Failed {item['id']}")
        except Exception as e:
            print(f"[proxy] Failed {item['id']}: {e}")


@app.post("/api/embed/rebuild")
def embed_rebuild(request: Request, background_tasks: BackgroundTasks, _tok: dict = Depends(require_scopes("ingest_write"))):
    """Drop + rebuild the ChromaDB semantic index from all media.
    Wired to 進階設定 → 搜尋引擎 → 「重建向量索引」button."""
    _assert_same_site(request)  # audit M14
    global _embed_rebuild_active
    with db.get_conn() as conn:
        total = conn.execute("SELECT count(*) FROM media").fetchone()[0]
    if not total:
        return {"message": "尚無素材可建立索引", "queued": 0}
    with _embed_rebuild_lock:  # audit M8: refuse concurrent rebuilds
        if _embed_rebuild_active:
            raise HTTPException(409, "向量索引重建已在進行中，請稍候")
        _embed_rebuild_active = True
    background_tasks.add_task(_rebuild_embeddings)
    return {"message": f"開始重建向量索引（{total} 筆素材，背景執行）", "queued": total}


def _rebuild_embeddings():
    """Background task: full embedding rebuild via subprocess. Runs embed.py in a
    child process to isolate its sys.exit() guard and use sys.executable per the
    platform Python-concurrency rule (not in-process — sys.exit would kill server)."""
    global _embed_rebuild_active
    import subprocess
    import sys
    try:
        subprocess.run([sys.executable, str(ROOT / "embed.py"), "--rebuild"], check=False)
    except Exception as e:
        print(f"[embed] rebuild failed: {e}")
    finally:
        with _embed_rebuild_lock:  # audit M8: always free the single-flight slot
            _embed_rebuild_active = False


# ── Phase 9.6b: per-project correction dictionary ────────────────────────────

class CorrectionsBody(BaseModel):
    # raw dicts; corrections._clean_rule validates (sidesteps `from` keyword).
    rules: List[dict] = []


class RevertBody(BaseModel):
    backup: Optional[str] = None


@app.get("/api/corrections")
def get_corrections(_tok: dict = Depends(require_scopes("projects_read"))):
    """The active project's correction dictionary (.arkiv/corrections.json)."""
    return {"rules": corrections.load_rules()}


@app.put("/api/corrections")
def put_corrections(
    body: CorrectionsBody,
    request: Request,
    _tok: dict = Depends(require_scopes("projects_write")),
):
    """Replace the dictionary. Returns the cleaned rules actually persisted."""
    _assert_same_site(request)
    saved = corrections.save_rules(body.rules)
    return {"ok": True, "rules": saved, "count": len(saved)}


@app.post("/api/recorrect")
def recorrect(
    request: Request,
    background_tasks: BackgroundTasks,
    dry_run: int = Query(1, description="1 = preview only (default, writes nothing)"),
    rebuild: int = Query(0, description="1 = rebuild embeddings after applying"),
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Batch-apply the dictionary's post-rules to stored transcripts.

    Defaults to dry-run (RP-4): a bare POST previews hits and writes nothing.
    dry_run=0 applies (transcript + segments_json synced, backup written first);
    rebuild=1 then chains the existing single-flight embedding rebuild so search
    reflects the corrected text."""
    if dry_run:
        return {"dry_run": True, **corrections.scan()}
    _assert_same_site(request)  # mutating — same-origin only (audit M14 pattern)
    result = corrections.apply()
    embed_started = False
    if rebuild and result.get("media_updated"):
        global _embed_rebuild_active
        with _embed_rebuild_lock:  # audit M8: refuse concurrent rebuilds
            if not _embed_rebuild_active:
                _embed_rebuild_active = True
                background_tasks.add_task(_rebuild_embeddings)
                embed_started = True
    return {"dry_run": False, **result, "embed_rebuild_started": embed_started}


@app.get("/api/recorrect/backups")
def recorrect_backups(_tok: dict = Depends(require_scopes("projects_read"))):
    """Reversible recorrect backups, newest first (for the revert picker)."""
    return {"backups": corrections.list_backups()}


@app.post("/api/recorrect/revert")
def recorrect_revert(
    body: RevertBody,
    request: Request,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Restore transcripts from a recorrect backup (latest if unspecified)."""
    _assert_same_site(request)
    return corrections.revert(body.backup)


# ── Phase 9.6d: project-wide batch retranscribe (2a) ─────────────────────────

class RetranscribeAllRequest(BaseModel):
    language: Optional[str] = None
    backup: bool = True


@app.post("/api/retranscribe-all")
def retranscribe_all(
    body: RetranscribeAllRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Re-run Whisper across the whole project (the 2a upgrade path: only needed
    when a term was mis-heard so badly that find→replace can't recover it). Each
    clip's transcribe hot-reads the project vocabulary + correction-dictionary
    pre-terms, so the new hotwords take effect. Long-running → single-flight
    background task; poll GET /api/retranscribe-all/status. Snapshots transcripts
    to the shared correction-backups first (RP-4 — restorable via the same
    revert)."""
    _assert_same_site(request)
    global _retranscribe_active
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM media WHERE has_audio=1").fetchall()
    targets = [(r["id"], r["path"]) for r in rows]
    if not targets:
        return {"queued": 0, "message": "沒有含音訊的素材可重轉錄"}
    with _retranscribe_lock:  # refuse concurrent batch runs (mirrors embed M8)
        if _retranscribe_active:
            raise HTTPException(409, "批次重轉錄已在進行中，請稍候")
        _retranscribe_active = True
        _retranscribe_progress.update({
            "total": len(targets), "done": 0, "failed": 0,
            "current": None, "running": True, "backup": None,
        })
    background_tasks.add_task(_run_retranscribe_all, targets, body.language, body.backup)
    return {"queued": len(targets)}


def _run_retranscribe_all(targets, language, backup):
    """Background worker: re-transcribe each target, preserving the single-clip
    guard (never blank a good transcript on an empty/failed decode — audit H1)."""
    global _retranscribe_active
    import transcribe as tr
    try:
        if backup:
            rows = []
            with db.get_conn() as conn:
                for mid, _ in targets:
                    r = conn.execute(
                        "SELECT id, transcript, segments_json, words_json FROM media WHERE id=?",
                        (mid,),
                    ).fetchone()
                    if r:
                        rows.append(dict(r))
            if rows:
                _retranscribe_progress["backup"] = corrections._write_backup(
                    rows, [{"op": "retranscribe-all", "language": language}]
                )
        for mid, path in targets:
            _retranscribe_progress["current"] = mid
            media_path = _resolve_media_path(path or "")
            if not Path(media_path).exists():
                _retranscribe_progress["failed"] += 1
                _retranscribe_progress["done"] += 1
                continue
            try:
                text, lang, segments, words = tr.transcribe(media_path, language=language)
            except Exception:
                _retranscribe_progress["failed"] += 1
                _retranscribe_progress["done"] += 1
                continue
            rec = db.get_record_by_id(mid) or {}
            # refuse to overwrite a good transcript with nothing (H1)
            if not (text or "").strip() and (rec.get("transcript") or "").strip():
                _retranscribe_progress["failed"] += 1
                _retranscribe_progress["done"] += 1
                continue
            _al = lang or language
            _sj = json.dumps(segments, ensure_ascii=False) if segments else None
            _wj = json.dumps(words, ensure_ascii=False) if words else None
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE media SET transcript=?, lang=?, segments_json=?, words_json=? WHERE id=?",
                    (
                        text,
                        _al,
                        _sj,
                        _wj,
                        mid,
                    ),
                )
                db.upsert_transcript(mid, _al, text, _sj, _wj, _conn=conn)  # G2 archive
            _retranscribe_progress["done"] += 1
    finally:
        _retranscribe_progress["running"] = False
        _retranscribe_progress["current"] = None
        with _retranscribe_lock:
            _retranscribe_active = False


@app.get("/api/retranscribe-all/status")
def retranscribe_all_status(_tok: dict = Depends(require_scopes("projects_read"))):
    """Poll batch-retranscribe progress {total, done, failed, current, running, backup}."""
    return dict(_retranscribe_progress)


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

class OpenFileRequest(BaseModel):  # audit M22: malformed JSON → clean 422, not a raw 500
    path: str
    reveal: bool = False


@app.post("/api/open-file")
def open_file(
    body: OpenFileRequest,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Open file in OS default app or reveal in file manager. Only allows known media files from DB.

    Requires videos_write: launching an OS app / revealing a file is a privileged
    side effect on the host. Loopback is token-free (full scope); a remote
    read-only browse token is correctly refused.
    """
    import subprocess, platform
    file_path = body.path
    reveal = body.reveal
    # Validate: only allow paths that exist in our database
    if not db.is_processed(file_path):
        raise HTTPException(403, "只能開啟已索引的媒體檔案")
    resolved_path = _resolve_media_path(file_path)
    if not Path(resolved_path).exists():
        raise HTTPException(404, "找不到檔案")
    system = platform.system()
    if reveal:
        if system == "Darwin":
            subprocess.Popen(["open", "-R", resolved_path])
        elif system == "Windows":
            subprocess.Popen(["explorer", "/select,", resolved_path])
        else:
            subprocess.Popen(["xdg-open", str(Path(resolved_path).parent)])
    else:
        if system == "Darwin":
            subprocess.Popen(["open", resolved_path])
        elif system == "Windows":
            os.startfile(resolved_path)
        else:
            subprocess.Popen(["xdg-open", resolved_path])
    return {"ok": True}


class ClientLogRequest(BaseModel):
    # audit M22: the model's job is turning malformed JSON into a 422 instead of
    # a raw 500. Fields stay Any (the WebView occasionally logs non-string
    # payloads); the handler stringifies + sanitizes as before.
    level: Any = "info"
    msg: Any = ""


@app.post("/api/client-log")
def client_log(body: ClientLogRequest):
    """Receive client-side logs (errors, info) and print to server terminal.

    Stays unauthenticated (the WebView logs diagnostics, sometimes before a token
    is wired), but the attacker-controlled fields are sanitized + truncated:
    control chars (newlines / ANSI terminal escapes) are stripped so a remote
    caller can't forge log lines or corrupt the operator's terminal, and lengths
    are capped so it can't be used to fill a redirected logfile.
    """
    level = _log_safe(str(body.level if body.level is not None else "info").upper(), 16)
    msg = _log_safe(str(body.msg if body.msg is not None else ""), 2000)
    print(f"[WebView {level}] {msg}", flush=True)
    return {"ok": True}


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
