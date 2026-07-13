"""Search routes (R5-25 / round-5 #51 router split).

Two search surfaces:
  * GET  /api/search/all   — cross-project federated semantic search (federation.py),
    with the API-boundary path sanitisation that stops a videos_read client from
    mapping the operator's cross-project directory layout (Phase 16.2);
  * POST /api/search/query — structured query: typed field conditions AND/OR-combined
    (query_builder), with an optional semantic (vector) leg, returning the same
    {items,total,search} shape as /api/media.

The bulk-fetch helpers (_get_light_records_by_ids / _get_tags_bulk) shared with the
media group live in mediarecords.py; path sanitisation in pathres.py — imported
directly. `_split_csv` (only search_all uses it) moves here. No server import, no cycle.
"""
import logging as _logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import db
import federation
import projects as project_registry
from auth import require_scopes
from mediarecords import _get_light_records_by_ids, _get_tags_bulk
from pathres import _basename_safe, _display_path, _resolve_record

router = APIRouter()


def _split_csv(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    parts = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts or None


@router.get("/api/search/all")
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


@router.post("/api/search/query")
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
