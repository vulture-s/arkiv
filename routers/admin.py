"""Admin token-management routes (R5-25 / round-5 #51 router split).

The first router peeled from server.py. These four handlers are thin HTTP
wrappers over the already-extracted `admin` business-logic module; the only
route-local piece is the CreateTokenRequest body model, which moves here with
them. Depends on auth (require_scopes) + admin + fastapi/pydantic — no server
import, so server.py mounts this via app.include_router() with no cycle.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import admin
import config
import db
from auth import require_scopes

router = APIRouter()


class CreateTokenRequest(BaseModel):
    name: str
    scopes: List[str]
    description: Optional[str] = None
    expires_in_days: Optional[int] = None
    allowed_ips: Optional[List[str]] = None


@router.post("/api/admin/tokens")
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


@router.get("/api/admin/tokens")
def admin_list_tokens(
    _tok: dict = Depends(require_scopes("admin")),
):
    return {"tokens": admin.list_tokens()}


@router.get("/api/admin/tokens/{token_id}")
def admin_get_token(
    token_id: str,
    _tok: dict = Depends(require_scopes("admin")),
):
    token = admin.get_token(token_id)
    if not token:
        raise HTTPException(404, "Token not found")
    return token


@router.delete("/api/admin/tokens/{token_id}")
def admin_revoke_token(
    token_id: str,
    _tok: dict = Depends(require_scopes("admin")),
):
    if not admin.revoke_token(token_id):
        raise HTTPException(404, "Token not found")
    return {"ok": True, "deleted": token_id}


# ── Recycle bin (Phase 14.5) ──────────────────────────────────────────────────


@router.get("/api/admin/trash")
def admin_list_trash(
    _tok: dict = Depends(require_scopes("admin")),
):
    return {"trash": db.list_trash()}


class TrashPurgeBody(BaseModel):
    ttl_days: Optional[int] = None


@router.post("/api/admin/trash/purge")
def admin_purge_trash(
    body: TrashPurgeBody = None,
    _tok: dict = Depends(require_scopes("admin")),
):
    if body is not None and body.ttl_days is not None:
        ttl = body.ttl_days
    else:
        ttl = config.TRASH_TTL_DAYS
    try:
        purged = db.purge_trash(ttl)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "purged": purged}


@router.post("/api/admin/trash/restore/{trash_id}")
def admin_restore_trash(
    trash_id: int,
    _tok: dict = Depends(require_scopes("admin")),
):
    """Move a trashed original back next to its original path (or media-in) and
    re-ingest it so it returns to the library with thumbnails / transcript /
    vectors — not just onto disk. Matches the designed "restore triggers an
    ingest" behaviour (devdoc/media-delete-design.md §2). The re-ingest runs in
    the background through the shared single-flight slot (audit H3)."""
    try:
        dest = db.restore_trash(trash_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    ingest_status = "skipped"
    try:
        # Lazy import avoids any router→router load-cycle; routers.ingest is
        # already loaded by server.py for its router.
        from routers.ingest import _bg_ingest
        import threading

        threading.Thread(
            target=_bg_ingest, args=(dest,), daemon=True
        ).start()
        ingest_status = "triggered"
    except Exception:
        # restore already succeeded; a failed re-ingest trigger is non-fatal —
        # the file is back on disk and can be re-ingested manually.
        ingest_status = "trigger_failed"
    return {"ok": True, "restored_to": dest, "ingest": ingest_status}
