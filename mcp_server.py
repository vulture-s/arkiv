"""arkiv MCP server (Phase 14).

Exposes arkiv's local media knowledge layer — semantic search, metadata,
transcripts, stats — to MCP clients (Claude, OpenClaw) over stdio.

Design contract:
- **Read-only.** No ingest, no delete, no mutation. The server only reads the
  existing DB / vector index.
- **No absolute-path leak.** Every filesystem path in a response is
  PROJECT_ROOT-relative, with a basename fallback for out-of-root legacy rows
  (`db.to_relative` passes those through as absolute). Mirrors the Phase 16.2
  path-leak hardening on the HTTP API — a red line for this server.
- **Reuse, don't fork.** Backed by `db` + `vectordb`; deliberately does NOT
  import `server` (that would pull in the whole FastAPI app + its startup cost).

Run:  python mcp_server.py            # stdio MCP server
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import db
import vectordb as vdb
from mcp.server.fastmcp import FastMCP

# Logging goes to stderr (never stdout — stdout is the MCP stdio channel).
LOGGER = logging.getLogger(__name__)

mcp = FastMCP("arkiv")


# ── path safety ───────────────────────────────────────────────────────────────
def _looks_absolute(p: str) -> bool:
    """True for POSIX (`/x`), Windows drive (`C:\\x`), or UNC (`\\\\host`) absolutes.
    `os.path.isabs` on a POSIX host misses the Windows forms, which would let a
    legacy/cross-platform row leak its full path. The Windows drive form requires
    a backslash after the colon (`C:\\`); a forward-slash `C:/x` is a POSIX
    relative path under a dir named like a drive letter."""
    if not p:
        return False
    return (
        p.startswith("/")
        or p.startswith("\\\\")
        or (len(p) >= 3 and p[0].isalpha() and p[1] == ":" and p[2] == "\\")
    )


def _safe_path(p: Optional[str]) -> Optional[str]:
    """Return a PROJECT_ROOT-relative path, never an absolute one.

    `db.to_relative` relativizes paths under PROJECT_ROOT but passes out-of-root
    absolute paths through unchanged — which would leak the operator's directory
    tree. Fall back to a separator-agnostic basename in that case so a response
    can never expose an absolute path, POSIX **or** Windows/UNC (red line,
    mirrors the HTTP API's Phase 16.2 `_display_path`).
    """
    if not p:
        return p
    rel = db.to_relative(p)
    if _looks_absolute(rel):
        return rel.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return rel


# ── shaping ───────────────────────────────────────────────────────────────────
_LIGHT_FIELDS = ("id", "filename", "lang", "duration_s", "size_mb", "rating", "created_at")


def _light(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Lightweight record for list/search results (no transcript / frame_tags)."""
    out = {k: rec.get(k) for k in _LIGHT_FIELDS if k in rec}
    if rec.get("path"):
        out["path"] = _safe_path(rec["path"])
    return out


def _full(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Full record with every path field sanitized."""
    out = dict(rec)
    out["path"] = _safe_path(rec.get("path"))
    if "thumbnail_path" in out:
        out["thumbnail_path"] = _safe_path(rec.get("thumbnail_path"))
    return out


def _tag_names(media_id: int) -> List[str]:
    return [t["name"] for t in db.get_tags(media_id) if t.get("name")]


# ── impl (unit-testable; no MCP/stdio coupling) ───────────────────────────────
def search_media_impl(
    query: str,
    limit: int = 20,
    _warnings: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Semantic search with SQL text fallback. Returns lightweight records.

    ``_warnings``: optional out-param list; degraded-search messages (e.g.
    embedding dimension mismatch) are appended so the tool layer can surface
    them without changing this function's list return contract (audit M17).
    """
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(100, limit))

    enriched: List[Dict[str, Any]] = []
    seen: set = set()

    # 1. Semantic (vector) search — preferred.
    try:
        for r in vdb.search(query, n_results=limit * 3):
            mid = int(r["media_id"])
            if mid in seen:
                continue
            rec = db.get_record_by_id(mid)
            if not rec:
                continue
            seen.add(mid)
            item = _light(rec)
            item["score"] = round(float(r.get("score", 0)), 4)
            if r.get("excerpt"):
                item["excerpt"] = r["excerpt"]
            item["tags"] = _tag_names(mid)
            enriched.append(item)
            if len(enriched) >= limit:
                break
    # audit M17: split the dim-mismatch branch out of the blanket except — log
    # it and surface a degraded hint (mirrors server.py's search_degraded fix)
    # instead of silently SQL-degrading. SQL fallback still runs below.
    except vdb.EmbeddingDimensionMismatch as exc:
        LOGGER.warning("mcp semantic search degraded: %s", exc)
        if _warnings is not None:
            _warnings.append(str(exc))
        enriched, seen = [], set()
    except Exception:
        # Vector index unavailable/empty — degrade to SQL.
        enriched, seen = [], set()

    # 2. SQL text fallback (filename / transcript) when semantic found nothing.
    if not enriched:
        like = f"%{query}%"
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM media WHERE filename LIKE ? OR transcript LIKE ? "
                "ORDER BY id LIMIT ?",
                (like, like, limit),
            ).fetchall()
            for row in rows:
                rec = dict(row)
                mid = rec["id"]
                if mid in seen:
                    continue
                seen.add(mid)
                item = _light(rec)
                item["tags"] = _tag_names(mid)
                enriched.append(item)

    return enriched[:limit]


def get_media_impl(media_id: int) -> Optional[Dict[str, Any]]:
    """Full metadata for one media item (sanitized paths + tags), or None."""
    rec = db.get_record_by_id(int(media_id))
    if not rec:
        return None
    out = _full(rec)
    out["tags"] = _tag_names(int(media_id))
    return out


def get_transcript_impl(media_id: int) -> Optional[Dict[str, Any]]:
    """Transcript text for one media item, or None."""
    rec = db.get_record_by_id(int(media_id))
    if not rec:
        return None
    return {
        "id": rec.get("id"),
        "filename": rec.get("filename"),
        "lang": rec.get("lang"),
        "transcript": rec.get("transcript"),
    }


def list_recent_impl(limit: int = 20) -> List[Dict[str, Any]]:
    """Most recent media (lightweight), newest first.

    `db.get_media_list` paginates by `id` ASC (oldest first), so reusing it would
    return the OLDEST ingests — the opposite of this tool's contract. Query
    descending instead.
    """
    limit = max(1, min(100, limit))
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT {db.LIGHT_COLS} FROM media ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_light(dict(r)) for r in rows]


def library_stats_impl() -> Dict[str, Any]:
    """Library aggregate stats."""
    return db.get_stats()


def list_tags_impl(limit: int = 30) -> List[Dict[str, Any]]:
    """Top tags by frequency."""
    limit = max(1, min(200, limit))
    return db.get_top_tags(limit)


# ── MCP tools ─────────────────────────────────────────────────────────────────
# Tools return JSON strings (ensure_ascii=False keeps Chinese readable, matching
# export.py) — an unambiguous, version-stable contract across MCP SDK releases.
def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


@mcp.tool()
def search_media(query: str, limit: int = 20) -> str:
    """Search the local media library by natural-language query.

    Semantic search over transcripts + vision tags, falling back to
    filename/transcript text match. Returns a JSON list of lightweight records:
    {id, filename, path, score, excerpt, tags, lang, duration_s}.
    If semantic search is degraded (e.g. embedding index needs a rebuild), the
    response is instead {items, search_degraded: true, warning}.
    """
    # audit M17: surface degraded-search hint instead of silently falling back.
    warnings: List[str] = []
    items = search_media_impl(query, limit, _warnings=warnings)
    if warnings:
        return _j({"items": items, "search_degraded": True, "warning": warnings[0]})
    return _j(items)


@mcp.tool()
def get_media(media_id: int) -> str:
    """Full metadata for one media item (relative paths, tags, vision fields).

    Returns a JSON object, or null if the id is unknown.
    """
    return _j(get_media_impl(media_id))


@mcp.tool()
def get_transcript(media_id: int) -> str:
    """Full transcript text for one media item.

    Returns JSON {id, filename, lang, transcript}, or null if unknown.
    """
    return _j(get_transcript_impl(media_id))


@mcp.tool()
def list_recent(limit: int = 20) -> str:
    """Most recently ingested media (lightweight). Returns a JSON list."""
    return _j(list_recent_impl(limit))


@mcp.tool()
def library_stats() -> str:
    """Library totals: count, transcribed, total duration/size, languages.

    Returns a JSON object.
    """
    return _j(library_stats_impl())


@mcp.tool()
def list_tags(limit: int = 30) -> str:
    """Top tags in the library by frequency. Returns a JSON list of {name, count}."""
    return _j(list_tags_impl(limit))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
