from __future__ import annotations

import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import chromadb

import config
from health import HealthStatus, project_health
from projects import ProjectMeta


LOGGER = logging.getLogger(__name__)


def embed_query(query: str):
    import vectordb

    return vectordb.embed(query)


@dataclass
class ProjectQueryResult(object):
    project_name: str
    project_path: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    latency_ms: int = 0
    status: str = "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "project_path": self.project_path,
            "items": list(self.items),
            "error": self.error,
            "latency_ms": self.latency_ms,
            "status": self.status,
        }


def _project_meta(project: Any) -> ProjectMeta:
    if isinstance(project, ProjectMeta):
        return project
    if isinstance(project, dict):
        return ProjectMeta.from_mapping(project, source=str(project.get("source", "registry")))
    raise TypeError("project must be ProjectMeta or mapping")


def _project_root(project: ProjectMeta) -> Path:
    return project.path.expanduser()


def _project_key(project: ProjectMeta, media_id: Any) -> Tuple[str, str]:
    return (str(_project_root(project).resolve(strict=False)).casefold(), str(media_id))


def _resolve_paths(project: ProjectMeta, stored_path: str) -> Tuple[str, str]:
    if not stored_path:
        absolute = str(_project_root(project).resolve(strict=False))
        return "", absolute

    stored = Path(stored_path)
    if stored.is_absolute():
        absolute = str(stored)
        try:
            relative = str(stored.relative_to(_project_root(project)))
        except Exception:
            relative = str(stored)
        return relative, absolute

    absolute = str((_project_root(project) / stored).resolve(strict=False))
    return str(stored), absolute


def _connect_project_db(project: ProjectMeta) -> sqlite3.Connection:
    db_path = _project_root(project) / ".arkiv" / "project.db"
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_media_row(conn: sqlite3.Connection, media_id: Any) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT id, path, filename, duration_s, rating, lang, ext, transcript FROM media WHERE id = ?",
        (media_id,),
    ).fetchone()
    return dict(row) if row else None


def _score_from_distance(distance: Any) -> float:
    try:
        return round(1 - float(distance), 4)
    except Exception:
        return 0.0


def _query_chroma(project: ProjectMeta, query_embeddings, limit: int) -> List[Dict[str, Any]]:
    client = chromadb.PersistentClient(path=str(_project_root(project) / ".arkiv" / "chroma_db"))
    collection = client.get_collection(config.COLLECTION_NAME)
    try:
        raw = collection.query(
            query_embeddings=[query_embeddings],
            n_results=max(limit * 3, limit),
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        import vectordb
        vectordb._reraise_dim_error(exc)  # dim mismatch -> EmbeddingDimensionMismatch
    documents = raw.get("documents", [[]])[0] if raw.get("documents") else []
    metadatas = raw.get("metadatas", [[]])[0] if raw.get("metadatas") else []
    distances = raw.get("distances", [[]])[0] if raw.get("distances") else []
    seen = set()
    hits = []
    for index, (document, meta, distance) in enumerate(zip(documents, metadatas, distances)):
        media_id = meta.get("media_id")
        if media_id in seen:
            continue
        seen.add(media_id)
        hits.append({
            "media_id": str(media_id),
            "filename": meta.get("filename") or "",
            "path": meta.get("path") or "",
            "duration_s": meta.get("duration_s"),
            "lang": meta.get("lang") or "",
            "excerpt": (document or "")[:300],
            "score": _score_from_distance(distance),
            "chunk_type": meta.get("chunk_type") or "",
            "chunk_idx": meta.get("chunk_idx", index),
        })
        if len(hits) >= limit:
            break
    return hits


def _sql_like_search(conn: sqlite3.Connection, project: ProjectMeta, query: str, limit: int) -> List[Dict[str, Any]]:
    like = "%%%s%%" % query
    rows = conn.execute(
        "SELECT id, path, filename, duration_s, rating, lang, ext, transcript "
        "FROM media WHERE filename LIKE ? OR transcript LIKE ? ORDER BY id",
        (like, like),
    ).fetchall()
    seen = set()
    results = []
    for row in rows:
        media_id = str(row["id"])
        if media_id in seen:
            continue
        seen.add(media_id)
        stored_path = row["path"] or ""
        relative_path, absolute_path = _resolve_paths(project, stored_path)
        excerpt = row["transcript"] or row["filename"] or query
        results.append({
            "project_name": project.name,
            "project_path": str(_project_root(project)),
            "media_id": media_id,
            "filename": row["filename"] or "",
            "relative_path": relative_path,
            "absolute_path": absolute_path,
            "path": absolute_path,
            "duration_s": row["duration_s"],
            "rating": row["rating"],
            "lang": row["lang"] or "",
            "excerpt": excerpt[:300],
            "score": 0.0,
            "chunk_type": "sql",
        })
        if len(results) >= limit:
            break
    return results


def query_single_project(
    project: ProjectMeta,
    query: str,
    limit: int = 20,
    q_embed=None,
    fallback_sql: bool = True,
) -> ProjectQueryResult:
    project = _project_meta(project)
    started = time.perf_counter()
    conn = None
    error = None
    items: List[Dict[str, Any]] = []
    status = "ok"

    try:
        health = project_health(project)
        if health != HealthStatus.OK:
            status = health.value
            error = health.value
            LOGGER.warning("project preflight failed for %s: %s", project.name, error)
            return ProjectQueryResult(
                project_name=project.name,
                project_path=str(_project_root(project)),
                items=[],
                error=error,
                latency_ms=int((time.perf_counter() - started) * 1000),
                status=status,
            )

        conn = _connect_project_db(project)
        if q_embed is None and fallback_sql:
            items = _sql_like_search(conn, project, query, limit)
        else:
            try:
                if q_embed is None:
                    q_embed = embed_query(query)
                chroma_hits = _query_chroma(project, q_embed, limit)
                if chroma_hits:
                    for hit in chroma_hits:
                        row = _fetch_media_row(conn, hit["media_id"])
                        if row:
                            stored_path = row["path"] or hit["path"]
                            relative_path, absolute_path = _resolve_paths(project, stored_path)
                            hit.update({
                                "project_name": project.name,
                                "project_path": str(_project_root(project)),
                                "filename": row.get("filename") or hit.get("filename") or "",
                                "relative_path": relative_path,
                                "absolute_path": absolute_path,
                                "path": absolute_path,
                                "duration_s": row.get("duration_s", hit.get("duration_s")),
                                "rating": row.get("rating"),
                                "lang": row.get("lang") or hit.get("lang") or "",
                            })
                        else:
                            relative_path, absolute_path = _resolve_paths(project, hit["path"])
                            hit.update({
                                "project_name": project.name,
                                "project_path": str(_project_root(project)),
                                "relative_path": relative_path,
                                "absolute_path": absolute_path,
                                "path": absolute_path,
                            })
                    items = chroma_hits
                elif fallback_sql:
                    items = _sql_like_search(conn, project, query, limit)
            except Exception as exc:
                import vectordb
                if isinstance(exc, vectordb.EmbeddingDimensionMismatch):
                    raise  # don't SQL-degrade a dim mismatch — surface as project error
                LOGGER.warning("project query failed for %s: %s", project.name, exc)
                if fallback_sql:
                    items = _sql_like_search(conn, project, query, limit)
                else:
                    raise
    except Exception as exc:
        error = str(exc)
        status = "error"
        items = []
        LOGGER.warning("project query failed for %s: %s", project.name, exc)
    finally:
        if conn is not None:
            conn.close()

    latency_ms = int((time.perf_counter() - started) * 1000)
    return ProjectQueryResult(
        project_name=project.name,
        project_path=str(_project_root(project)),
        items=items[:limit],
        error=error,
        latency_ms=latency_ms,
        status=status,
    )


def _filter_projects(
    projects: Iterable[ProjectMeta],
    project_names: Optional[List[str]] = None,
    tag: Optional[str] = None,
) -> List[ProjectMeta]:
    selected = []
    name_filters = set(project_names or [])
    for project in projects:
        if name_filters and project.name not in name_filters and str(project.path) not in name_filters:
            continue
        if tag and tag not in project.tags:
            continue
        selected.append(project)
    return selected


def search_all_projects(
    query: str,
    limit: int = 50,
    per_project_limit: int = 20,
    project_names: Optional[List[str]] = None,
    tag: Optional[str] = None,
    timeout: float = 10.0,
    fallback_sql: bool = True,
) -> Dict[str, Any]:
    projects = _filter_projects(config.discover_projects(), project_names=project_names, tag=tag)
    response = {
        "query": query,
        "total_results": 0,
        "projects_queried": len(projects),
        "projects_failed": 0,
        "items": [],
        "errors": [],
    }
    if not projects:
        response["errors"].append({
            "project_name": None,
            "project_path": None,
            "error": "no matching projects",
            "stage": "preflight",
        })
        return response

    try:
        q_embed = embed_query(query)
    except Exception as exc:
        LOGGER.warning("shared query embed failed: %s", exc)
        q_embed = None

    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    # Manual executor (not a `with` block): the context manager's __exit__ calls
    # shutdown(wait=True), which blocks until every worker returns — so a worker
    # hung on a stale NAS mount would make this function hang for far longer than
    # `timeout`, despite the per-future timeout below. We instead bound total
    # wall-clock with a shared deadline and abandon stragglers via
    # shutdown(wait=False, cancel_futures=True).
    executor = ThreadPoolExecutor(max_workers=min(len(projects), 8))
    try:
        futures = []
        for project in projects:
            futures.append((project, executor.submit(
                query_single_project,
                project,
                query,
                per_project_limit,
                q_embed,
                fallback_sql,
            )))

        deadline = time.monotonic() + timeout
        for project, future in futures:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                remaining = 0
            try:
                result = future.result(timeout=remaining)
            except FuturesTimeoutError:
                LOGGER.warning("project query timeout for %s after %ss", project.name, timeout)
                response["projects_failed"] += 1
                response["errors"].append({
                    "project_name": project.name,
                    "project_path": str(project.path),
                    "error": "timeout after {0}s".format(timeout),
                    "stage": "timeout",
                })
                continue

            if result.error:
                response["projects_failed"] += 1
                response["errors"].append({
                    "project_name": result.project_name,
                    "project_path": result.project_path,
                    "error": result.error,
                    "stage": result.status,
                })
                continue

            for item in result.items:
                key = _project_key(project, item.get("media_id"))
                existing = merged.get(key)
                if existing is None or float(item.get("score", 0)) > float(existing.get("score", 0)):
                    merged[key] = item
    finally:
        # Do not wait on stragglers; cancel anything not yet started.
        executor.shutdown(wait=False, cancel_futures=True)

    items = sorted(
        merged.values(),
        key=lambda item: (-float(item.get("score", 0)), item.get("project_name", ""), str(item.get("media_id", ""))),
    )[:limit]
    response["items"] = items
    response["total_results"] = len(merged)
    return response
