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
