"""Correction-dictionary + recorrect routes (R5-25 / round-5 #51 router split).

Phase 9.6b: the per-project correction dictionary (GET/PUT /api/corrections) and
the batch recorrect flow — /api/recorrect (dry-run preview by default, RP-4),
/api/recorrect/backups, /api/recorrect/revert. Applying with rebuild=1 chains the
shared embedding rebuild so search reflects the corrected text; that worker
(_rebuild_embeddings) and its single-flight guard (embed_rebuild) live in state.py
and are imported here — the ONE instance shared with /api/embed/rebuild, never a
copy. Imports auth + corrections + webguard + state — no server import, no cycle.
"""
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel

import corrections
from auth import require_scopes
from state import _rebuild_embeddings, embed_rebuild as _embed_guard
from webguard import _assert_same_site

router = APIRouter()


class CorrectionsBody(BaseModel):
    # raw dicts; corrections._clean_rule validates (sidesteps `from` keyword).
    rules: List[dict] = []


class RevertBody(BaseModel):
    backup: Optional[str] = None


@router.get("/api/corrections")
def get_corrections(_tok: dict = Depends(require_scopes("projects_read"))):
    """The active project's correction dictionary (.arkiv/corrections.json)."""
    return {"rules": corrections.load_rules()}


@router.put("/api/corrections")
def put_corrections(
    body: CorrectionsBody,
    request: Request,
    _tok: dict = Depends(require_scopes("projects_write")),
):
    """Replace the dictionary. Returns the cleaned rules actually persisted."""
    _assert_same_site(request)
    saved = corrections.save_rules(body.rules)
    return {"ok": True, "rules": saved, "count": len(saved)}


@router.post("/api/recorrect")
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
        if _embed_guard.acquire():  # audit M8: only start if no rebuild is running
            background_tasks.add_task(_rebuild_embeddings)
            embed_started = True
    return {"dry_run": False, **result, "embed_rebuild_started": embed_started}


@router.get("/api/recorrect/backups")
def recorrect_backups(_tok: dict = Depends(require_scopes("projects_read"))):
    """Reversible recorrect backups, newest first (for the revert picker)."""
    return {"backups": corrections.list_backups()}


@router.post("/api/recorrect/revert")
def recorrect_revert(
    body: RevertBody,
    request: Request,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Restore transcripts from a recorrect backup (latest if unspecified)."""
    _assert_same_site(request)
    return corrections.revert(body.backup)
