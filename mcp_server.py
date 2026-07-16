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
import scenes
import vectordb as vdb
from mcp.server.fastmcp import FastMCP
from pathres import _display_path

# Logging goes to stderr (never stdout — stdout is the MCP stdio channel).
LOGGER = logging.getLogger(__name__)

mcp = FastMCP("arkiv")


# ── path safety ───────────────────────────────────────────────────────────────
def _safe_path(p: Optional[str]) -> Optional[str]:
    """Return a PROJECT_ROOT-relative path, never an absolute one.

    Thin alias for the HTTP API's `pathres._display_path` — deliberately the SAME
    guard object, not a same-looking copy. Both do: `db.to_relative`, and if the
    result is still absolute (out-of-root legacy row) fall back to a
    separator-agnostic basename, so a response can never expose the operator's
    directory tree — POSIX, Windows **or** UNC. Red line for this server (see the
    module docstring).

    This module carried its own copy from 2026-06-08 (a9c0838) because the logic
    lived in `server.py`, and importing that here would drag in the whole FastAPI
    app — there was nowhere to share from. R5-25 / round-5 #51 extracted it to the
    `pathres` leaf (70490fa, 2026-07-13), which is what finally makes one guard
    possible. And the copy had already drifted: it kept Codex round-1's reading
    that `C:/x` is a POSIX relative path under a drive-letter-named dir, while
    round-2 (fc35b8f, four hours later the same day) overruled that — "a Unix
    media dir literally named 'C:' is pathological… no-leak wins" — and treated
    `C:/` as a Windows absolute. That correction landed in `server.py` only and
    never reached here, so `C:/Users/me/x.mov` leaked whole over MCP for 38 days:
    on the one surface whose stated red line is "no absolute-path leak", and the
    only one facing untrusted downstream agents.
    """
    return _display_path(p)


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


def get_scenes_impl(media_id: int) -> Optional[Dict[str, Any]]:
    """Per-scene breakdown for one media item, or None if the id is unknown.

    The rec lookup is not redundant with get_frames: db.get_frames on an unknown
    id returns [], which is indistinguishable from a real clip that has no vision
    analysis yet. Fetch the record first so "no such media" (None) and "no scenes"
    (total: 0) stay different answers — and it carries duration_s anyway.
    """
    rec = db.get_record_by_id(int(media_id))
    if not rec:
        return None
    frames = db.get_frames(int(media_id))
    return scenes._scenes_for_mcp(int(media_id), rec, frames)


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
def get_scenes(media_id: int) -> str:
    """Timecoded scene breakdown for one media item — what happens, and when.

    Use this to decide WHICH PART of a clip to use: search_media/get_media answer
    "which clip", this answers "which seconds of it". Each scene is one
    scene-detect boundary with vision analysis of its keyframe.

    Returns a JSON object, or null if the id is unknown:
      {media_id, media_duration_s, total, scenes: [...]}

    Each scene:
      scene_index    int    0-based, in playback order
      start_s        float  scene start, seconds from clip start
      end_s          float  = next scene's start_s; the last scene ends at
                            media_duration_s
      duration_s     float  end_s - start_s
      description    str    what the keyframe shows (may be "" if not analysed)
      content_type   str    e.g. Establishing / B-Roll / A-Roll / Talking-Head
      focus_score    int    1-5, higher = sharper
      atmosphere     str
      energy         str
      edit_position  str    where this shot would sit in a cut
      edit_reason    str
      stability      str
      exposure       str
      audio_quality  str
      keyframe_path  str    PROJECT_ROOT-relative path to the keyframe JPEG.
                            Present only when a keyframe exists.

    Every vision field is present on every scene, but is null when that clip has
    not been analysed — check for null rather than for the key. Values are in the
    library's own language (typically Chinese).
    """
    return _j(get_scenes_impl(media_id))


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
