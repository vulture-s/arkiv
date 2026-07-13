"""Chat routes (R5-25 / round-5 #51 router split).

The /api/chat group: dispatch a prompt (creating or appending to a conversation),
read a conversation's history, and list conversations. Thin wrappers over the
already-extracted `chat` module; the request/response models and the ownership
SQL-filter helper (`_chat_owner_filter` — remote tokens only see their own
conversations) are chat-local and move here. Imports auth + chat + db — no server
import, no cycle.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

import chat
import db
from auth import require_scopes

router = APIRouter()


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


@router.post("/api/chat", response_model=ChatResponse)
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


@router.get("/api/chat/history/{conv_id}")
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


@router.get("/api/chat/conversations")
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
