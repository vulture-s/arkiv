"""Project-wide batch retranscribe routes (R5-25 / round-5 #51 router split).

Phase 9.6d (2a upgrade path): re-run Whisper across the whole library when a term
was mis-heard too badly for find→replace to recover. Long-running → single-flight
background task (`/api/retranscribe-all` + its `/status` poller) plus the worker
`_run_retranscribe_all`. The two-lock ordering is preserved VERBATIM: acquire the
retranscribe guard first, then the shared H3 ingest slot (a batch runs whisper
in-process, so it must hold the ingest slot too or a concurrent /api/ingest would
load a second whisper → OOM); the worker releases both in its finally. Both live
in state.py so this module imports the ONE shared instance, never a copy. Imports
auth + db + corrections + webguard + pathres + state — no server import, no cycle.
"""
import json
import re as _re
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
)
from pydantic import BaseModel, field_validator

import corrections
import db
from auth import require_scopes
from pathres import _resolve_media_path
from state import (
    retranscribe as _retranscribe_guard,
    _acquire_ingest_slot,
    _release_ingest_slot,
)
from webguard import _assert_same_site

router = APIRouter()


class RetranscribeAllRequest(BaseModel):
    language: Optional[str] = None
    backup: bool = True

    # fable-audit 2026-07-12 (#10): the single-clip RetranscribeRequest got the
    # M23 ISO-639 validator but this whole-library sibling didn't — an unvalidated
    # language flowed into whisper for the ENTIRE project, raising deep in a
    # background batch (swallowed, hours of churn, lock held). Reject up front.
    @field_validator("language")
    @classmethod
    def _check_language(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if not _re.fullmatch(r"[a-z]{2,3}", v):
            raise ValueError("language must be null or a 2-3 letter ISO-639 code (e.g. 'zh', 'en')")
        return v


@router.post("/api/retranscribe-all")
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
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM media WHERE has_audio=1").fetchall()
    targets = [(r["id"], r["path"]) for r in rows]
    if not targets:
        return {"queued": 0, "message": "沒有含音訊的素材可重轉錄"}
    if not _retranscribe_guard.acquire():  # refuse concurrent batch runs (mirrors embed M8)
        raise HTTPException(409, "批次重轉錄已在進行中，請稍候")
    # fable-audit 2026-07-12 (#11): this batch runs whisper IN-PROCESS over the
    # whole library — it must also hold the shared H3 ingest slot, or a concurrent
    # /api/ingest loads a second whisper → double-whisper OOM. The background
    # worker releases both in its finally; if the slot is busy we must free our own
    # guard before bailing so a later retry isn't wrongly rejected.
    if not _acquire_ingest_slot():
        _retranscribe_guard.release()
        raise HTTPException(409, "已有匯入任務進行中，請稍候")
    _retranscribe_guard.reset_progress(
        total=len(targets), done=0, failed=0, current=None, running=True, backup=None,
    )
    background_tasks.add_task(_run_retranscribe_all, targets, body.language, body.backup)
    return {"queued": len(targets)}


def _run_retranscribe_all(targets, language, backup):
    """Background worker: re-transcribe each target, preserving the single-clip
    guard (never blank a good transcript on an empty/failed decode — audit H1)."""
    import transcribe as tr
    # The worker owns its progress lifecycle: seed a clean counter set at the start
    # (the route also seeds it for the poll window before this task is scheduled).
    _retranscribe_guard.reset_progress(
        total=len(targets), done=0, failed=0, current=None, running=True, backup=None,
    )
    progress = _retranscribe_guard.progress  # in-place dict shared with the poller
    try:
        if backup:
            rows = []
            with db.get_conn() as conn:
                for mid, _ in targets:
                    r = conn.execute(
                        # fable-audit round-5 #14: include lang so revert restores the
                        # language too — else a zh→en batch, reverted, leaves zh text
                        # tagged lang='en', which later archives cross-contaminate.
                        "SELECT id, transcript, segments_json, words_json, lang FROM media WHERE id=?",
                        (mid,),
                    ).fetchone()
                    if r:
                        rows.append(dict(r))
            if rows:
                progress["backup"] = corrections._write_backup(
                    rows, [{"op": "retranscribe-all", "language": language}]
                )
        for mid, path in targets:
            progress["current"] = mid
            media_path = _resolve_media_path(path or "")
            if not Path(media_path).exists():
                progress["failed"] += 1
                progress["done"] += 1
                continue
            try:
                text, lang, segments, words = tr.transcribe(media_path, language=language)
            except Exception:
                progress["failed"] += 1
                progress["done"] += 1
                continue
            rec = db.get_record_by_id(mid) or {}
            # refuse to overwrite a good transcript with nothing (H1)
            if not (text or "").strip() and (rec.get("transcript") or "").strip():
                progress["failed"] += 1
                progress["done"] += 1
                continue
            _al = lang or language
            _sj = json.dumps(segments, ensure_ascii=False) if segments else None
            _wj = json.dumps(words, ensure_ascii=False) if words else None
            with db.get_conn() as conn:
                # fable-audit round-5 C2: archive the OUTGOING transcript first, like
                # the single-clip retranscribe does — else a batch zh→en overwrites
                # media's zh with en and, if the per-run backup is off/lost, the prior
                # active text is gone from the archive too.
                if (rec.get("transcript") or "").strip() and rec.get("lang"):
                    db.upsert_transcript(mid, rec["lang"], rec.get("transcript"),
                                         rec.get("segments_json"), rec.get("words_json"), _conn=conn)
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
            progress["done"] += 1
    finally:
        progress["running"] = False
        progress["current"] = None
        _retranscribe_guard.release()
        _release_ingest_slot()  # fable-audit 2026-07-12 (#11): free the shared H3 slot


@router.get("/api/retranscribe-all/status")
def retranscribe_all_status(_tok: dict = Depends(require_scopes("projects_read"))):
    """Poll batch-retranscribe progress {total, done, failed, current, running, backup}."""
    return dict(_retranscribe_guard.progress)
