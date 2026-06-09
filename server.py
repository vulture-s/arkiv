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
import urllib.request
from pathlib import Path
from typing import List, Literal, Optional, Set

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import admin
import chat
import codec
import auth
from auth import require_scopes
import config
import db
import federation
import projects as project_registry
import smart_collections
import tag_quality


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
        self.connections.add(ws)
        return True

    def disconnect(self, ws: WebSocket):
        self.connections.discard(ws)

    async def broadcast(self, data: dict):
        dead = set()
        for ws in self.connections:
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

# Serve thumbnails as static files (create dir if missing so mount always works).
# Honor ARKIV_THUMBNAILS_DIR (via config.THUMBNAILS_DIR) instead of hardcoding
# ROOT / "thumbnails" — otherwise any deployment that points thumbnails elsewhere
# (test rig, docker, worktree QA) silently 404s every /thumbnails/*.jpg.
thumbs_dir = config.THUMBNAILS_DIR
thumbs_dir.mkdir(parents=True, exist_ok=True)
app.mount("/thumbnails", StaticFiles(directory=str(thumbs_dir)), name="thumbnails")


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
    limit: int = 50,
    per_project_limit: int = 20,
    projects: Optional[str] = None,
    tag: Optional[str] = None,
    timeout: float = 10.0,
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
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT id FROM media WHERE {where} ORDER BY {order}", params
        ).fetchall()
        for idx, row in enumerate(rows):
            if row["id"] == media_id:
                return {"id": media_id, "offset": idx}
    return {"id": media_id, "offset": 0}


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
        # Try semantic search first (requires vectordb with embeddings)
        try:
            raw = vdb.search(q, n_results=limit * 3)
            results = []
            for r in raw:
                if lang and r.get("lang") != lang:
                    continue
                if rating == "unrated" and r.get("rating") is not None:
                    continue
                elif rating and rating != "unrated" and r.get("rating") != rating:
                    continue
                results.append(r)
            results = results[:limit]
            seen = set()
            for r in results:
                mid = int(r["media_id"])
                if mid in seen:
                    continue
                seen.add(mid)
                rec = db.get_record_by_id(mid)
                if rec:
                    _resolve_record(rec)
                    rec["score"] = r.get("score", 0)
                    rec["excerpt"] = r.get("excerpt", "")
                    rec["tags"] = db.get_tags(mid)
                    enriched.append(rec)
        except vdb.EmbeddingDimensionMismatch as exc:
            # Don't silently SQL-degrade a dim mismatch — log it and surface a hint
            # so the operator knows semantic search is off until they rebuild.
            _logging.getLogger(__name__).warning("semantic search degraded: %s", exc)
            search_warning = str(exc)
        except Exception:
            pass

        # Fallback: SQL text search (filename, transcript, tags)
        if not enriched:
            seen_ids = set()
            like = f"%{q}%"
            with db.get_conn() as conn:
                rows = conn.execute(
                    f"SELECT {db.LIGHT_COLS} FROM media "
                    "WHERE filename LIKE ? OR transcript LIKE ? "
                    "ORDER BY id",
                    (like, like),
                ).fetchall()
                for r in rows:
                    rec = dict(r)
                    _resolve_record(rec)
                    rec["tags"] = db.get_tags(rec["id"])
                    enriched.append(rec)
                    seen_ids.add(rec["id"])

                # Also search by tag name
                tag_rows = conn.execute(
                    "SELECT DISTINCT media_id FROM tags WHERE name LIKE ?",
                    (like,),
                ).fetchall()
                for tr in tag_rows:
                    mid = tr["media_id"]
                    if mid in seen_ids:
                        continue
                    rec = db.get_record_by_id(mid)
                    if rec:
                        _resolve_record(rec)
                        rec["tags"] = db.get_tags(mid)
                        enriched.append(rec)
                        seen_ids.add(mid)

        resp = {"items": enriched[:limit], "total": len(enriched), "search": True}
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
    # Attach tags to each record
    for rec in records:
        _resolve_record(rec)
        rec["tags"] = db.get_tags(rec["id"])

    return {"items": records, "total": total, "search": False}


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
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
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
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    frames = db.get_frames(media_id)
    scenes = []
    for frame in frames:
        scene = {
            "frame_index": frame["frame_index"],
            "timestamp_s": frame["timestamp_s"],
            "description": frame.get("description", ""),
            "content_type": frame.get("content_type"),
            "focus_score": frame.get("focus_score"),
            "atmosphere": frame.get("atmosphere"),
            "energy": frame.get("energy"),
            "edit_position": frame.get("edit_position"),
            "edit_reason": frame.get("edit_reason"),
        }
        if frame.get("thumbnail_path"):
            scene["thumbnail_url"] = "/thumbnails/{0}".format(
                Path(_resolve_media_path(frame["thumbnail_path"])).name
            )
        scenes.append(scene)
    return {"media_id": media_id, "scenes": scenes, "total": len(scenes)}


@app.patch("/api/media/{media_id}/rating")
def update_rating(
    media_id: int,
    body: RatingUpdate,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Set or clear rating for a media asset."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    db.set_rating(media_id, body.rating, body.note)
    return {"ok": True, "rating": body.rating, "note": body.note}


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
    # Screen quality-defect tags here too (Codex review P2) — index.html and any
    # stats-driven cloud read top_tags. Over-fetch then filter so we still get 10
    # real tags even if some of the top entries were noise.
    stats["top_tags"] = tag_quality.filter_tag_records(db.get_top_tags(40))[:10]
    return stats


@app.get("/api/tags")
def get_all_tags(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """All unique tag names with counts. Quality-defect tags (模糊/低解析度…)
    are screened out — see tag_quality. Pass include_noise=1 to bypass."""
    return tag_quality.filter_tag_records(db.get_all_tag_names())


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

    for rec in db.get_all_records():
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
    dest: str
    ids: Optional[list] = None  # batch-scoped variant; None = full library


@app.post("/api/export/metadata-csv-to")
def export_metadata_csv_to(
    body: MetadataCsvExportRequest,
    _tok: dict = Depends(require_scopes("media_read")),
):
    """Tauri WKWebView path: server writes CSV directly to user-picked dest.

    WKWebView 對 <a download> blob 觸發下載不可靠（Tauri docs 也建議走 fs API），
    所以 Tauri front-end 用 dialog.save 拿 path 後 POST 來這裡，由 server 直接寫
    檔；browser 端則繼續用 GET + blob download。
    body.ids 給時為 batch-scoped；不給為整庫匯出。"""
    dest = Path(body.dest).expanduser().resolve()
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

class ScanRequest(BaseModel):
    path: str

MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}

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
        return [Path(p).expanduser().resolve() for p in custom.split(":") if p.strip()]
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
                    roots.append(vol.resolve())
        except OSError:
            pass
    return [r for r in roots if r is not None]


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
    for f in sorted(target.rglob("*")):
        if f.suffix.lower() in MEDIA_EXTS:
            already = db.is_processed(str(f)) if hasattr(db, 'is_processed') else False
            files.append({"name": f.name, "size_mb": round(f.stat().st_size / 1048576, 1), "path": str(f), "already": already})
    return {"total": len(files), "new": sum(1 for f in files if not f["already"]), "files": files}

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
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "匯入逾時（>30 分鐘）"}


# ── Re-transcribe ─────────────────────────────────────────────────────────────

class RetranscribeRequest(BaseModel):
    language: str = "zh"

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
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE media SET transcript=?, lang=?, segments_json=?, words_json=? WHERE id=?",
                (
                    text,
                    body.language,
                    json.dumps(segments, ensure_ascii=False) if segments else None,
                    json.dumps(words, ensure_ascii=False) if words else None,
                    media_id,
                )
            )
        return {"ok": True, "transcript_length": len(text), "language": body.language}
    except Exception as e:
        raise HTTPException(500, str(e))


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

    # Phase 2: fallback to lighter model for failed frames
    if failed:
        fallback_model = "moondream2:latest"
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
                        db.add_tag(media_id, tag_name, source="auto")
                patched += 1
        # Update legacy frame_tags
        all_frames = db.get_frames(media_id)
        frame_tags = [{"description": f.get("description", ""), "tags": f.get("tags", "").split(",") if f.get("tags") else []} for f in all_frames]
        frame_tags_json = json.dumps(frame_tags, ensure_ascii=False)
        editability_score = None
        for frame in all_frames:
            if frame.get("focus_score") is not None:
                editability_score = db.compute_editability(frame)
                break
        conn.execute(
            "UPDATE media SET frame_tags=?, editability_score=? WHERE id=?",
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
    import subprocess, sys
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "ingest.py"), "--dir", str(Path(media_path).parent),
             "--limit", "1", "--refresh"],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT)
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout[-1000:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "重新處理逾時（>10 分鐘）"}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Cache Management ──────────────────────────────────────────────────────────

def _dir_size_mb(p: Path) -> int:
    return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1048576)


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
            headers={"Content-Disposition": f'attachment; filename="{stem}.txt"'},
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
            headers={"Content-Disposition": f'attachment; filename="{stem}.srt"'},
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
            headers={"Content-Disposition": f'attachment; filename="{stem}.vtt"'},
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
            headers={"Content-Disposition": f'attachment; filename="{stem}.edl"'},
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
            headers={"Content-Disposition": f'attachment; filename="{stem}.fcpxml"'},
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
    _tok: dict = Depends(require_scopes("media_read")),
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
        edl = f"TITLE: arkiv timeline\nFCM: {fcm}\n\n"
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


async def _run_ingest_with_ws(target: Path, limit: int):
    """Run ingest as a single subprocess, parse stdout for progress."""
    import re, sys

    cmd = [sys.executable, str(ROOT / "ingest.py"), "--dir", str(target)]
    if limit > 0:
        cmd += ["--limit", str(limit)]

    await ingest_ws.broadcast({"type": "start", "total": limit or 0})

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=str(ROOT)
    )

    ok, skipped, failed = 0, 0, 0
    file_re = re.compile(r"\[(\d+)/(\d+)\]\s+(SKIP\s+)?(\S+)\s+>")
    done_re = re.compile(r"\[(\d+)/(\d+)\]\s+(\S+)\s+.+\[OK\]")

    async for line in proc.stdout:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        print(f"[ingest-ws] {text}", flush=True)

        # Parse progress lines like "[1/3] FX30.5365.MP4 >probe >whisper..."
        m = file_re.match(text)
        if m:
            idx, total, skip, fname = m.group(1), m.group(2), m.group(3), m.group(4)
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
            await ingest_ws.broadcast({
                "type": "file", "index": int(d.group(1)), "total": int(d.group(2)),
                "filename": d.group(3).strip(), "status": "done"
            })

        # Parse "Found N media files"
        if text.startswith("Found "):
            fm = re.search(r"Processing (\d+)", text)
            if fm:
                await ingest_ws.broadcast({"type": "start", "total": int(fm.group(1))})

    await proc.wait()
    failed = (limit or 0) - ok - skipped if limit else 0
    print(f"[ingest-ws] COMPLETE ok={ok} skipped={skipped} failed={failed}", flush=True)

    await ingest_ws.broadcast({
        "type": "complete", "ok": ok, "skipped": skipped, "failed": failed
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
    asyncio.create_task(_run_ingest_with_ws(target, body.limit))
    return {"ok": True, "message": "已開始匯入 — 連線 /ws/ingest 取得進度"}


# ── Tailwind CDN proxy (cached locally so Tauri WKWebView never blocks) ────────
_TAILWIND_CDN_URL = "https://cdn.tailwindcss.com"
_tailwind_js: Optional[bytes] = None

def _fetch_tailwind() -> bytes:
    """Download Tailwind CDN JS once and cache on disk. Skip empty cache files."""
    cache_path = ROOT / "tailwind.cdn.js"
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        return cache_path.read_bytes()
    try:
        req = urllib.request.Request(_TAILWIND_CDN_URL,
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        if len(data) > 1000:
            cache_path.write_bytes(data)
            return data
    except Exception as e:
        print(f"[arkiv] Tailwind CDN download failed: {e}")
    return b"/* tailwind cdn unavailable */"

# Pre-fetch at import time (runs before uvicorn starts serving)
_tailwind_js = _fetch_tailwind()

@app.get("/tailwind.cdn.js")
def serve_tailwind():
    return Response(content=_tailwind_js, media_type="text/javascript",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/tailwind-static.css")
def serve_tailwind_static():
    css_path = ROOT / "tailwind-static.css"
    if css_path.exists():
        return Response(content=css_path.read_bytes(), media_type="text/css",
                        headers={"Cache-Control": "no-cache"})
    return Response(content=b"/* tailwind-static.css not found */", media_type="text/css")


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
    if codec.needs_proxy(str(file_path)) == codec.NEEDED:
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
def proxy_build(background_tasks: BackgroundTasks, _tok: dict = Depends(require_scopes("ingest_write"))):
    """Queue proxy generation for all HEVC/ProRes files without proxy."""
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
def proxy_build_one(media_id: int, background_tasks: BackgroundTasks, _tok: dict = Depends(require_scopes("ingest_write"))):
    """Per-id proxy build — surface 自 7.7g 409 「生成 proxy」按鈕，使用者點到
    哪個 HEVC 就只建那個，避免 build all 整庫拖時間。"""
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
def embed_rebuild(background_tasks: BackgroundTasks, _tok: dict = Depends(require_scopes("ingest_write"))):
    """Drop + rebuild the ChromaDB semantic index from all media.
    Wired to 進階設定 → 搜尋引擎 → 「重建向量索引」button."""
    with db.get_conn() as conn:
        total = conn.execute("SELECT count(*) FROM media").fetchone()[0]
    if not total:
        return {"message": "尚無素材可建立索引", "queued": 0}
    background_tasks.add_task(_rebuild_embeddings)
    return {"message": f"開始重建向量索引（{total} 筆素材，背景執行）", "queued": total}


def _rebuild_embeddings():
    """Background task: full embedding rebuild via subprocess. Runs embed.py in a
    child process to isolate its sys.exit() guard and use sys.executable per the
    platform Python-concurrency rule (not in-process — sys.exit would kill server)."""
    import subprocess
    import sys
    try:
        subprocess.run([sys.executable, str(ROOT / "embed.py"), "--rebuild"], check=False)
    except Exception as e:
        print(f"[embed] rebuild failed: {e}")


# ── Serve Frontend ───────────────────────────────────────────────────────────

# Dev mode: always read fresh index.html (no cache)
def _load_index() -> str:
    index = ROOT / "index.html"
    if index.exists():
        return index.read_text(encoding="utf-8")
    return "<h1>arkiv</h1><p>index.html not found</p>"

@app.post("/api/open-file")
async def open_file(
    request: __import__('starlette.requests', fromlist=['Request']).Request,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Open file in OS default app or reveal in file manager. Only allows known media files from DB.

    Requires videos_write: launching an OS app / revealing a file is a privileged
    side effect on the host. Loopback is token-free (full scope); a remote
    read-only browse token is correctly refused.
    """
    import subprocess, platform
    body = await request.json()
    file_path = body.get("path", "")
    reveal = body.get("reveal", False)
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


@app.post("/api/client-log")
async def client_log(request: __import__('starlette.requests', fromlist=['Request']).Request):
    """Receive client-side logs (errors, info) and print to server terminal.

    Stays unauthenticated (the WebView logs diagnostics, sometimes before a token
    is wired), but the attacker-controlled fields are sanitized + truncated:
    control chars (newlines / ANSI terminal escapes) are stripped so a remote
    caller can't forge log lines or corrupt the operator's terminal, and lengths
    are capped so it can't be used to fill a redirected logfile.
    """
    body = await request.json()
    level = _log_safe(str(body.get("level", "info")).upper(), 16)
    msg = _log_safe(str(body.get("msg", "")), 2000)
    print(f"[WebView {level}] {msg}", flush=True)
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def serve_index():
    return _load_index()
