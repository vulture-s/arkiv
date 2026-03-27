"""
Media Asset Manager — Service Layer
All UI modules (app.py, pages/) import api, not db/vectordb directly.
"""
from __future__ import annotations

import db
import vectordb as vdb


# ── Browse / List ────────────────────────────────────────────────────────────

def list_media(
    offset: int = 0,
    limit: int = 50,
    min_duration: float = 0,
    max_duration: float = 99999,
    lang: str | None = None,
) -> tuple[list[dict], int]:
    """Return (records, total_count) for paginated browse.
    Records are lightweight (no transcript/frame_tags)."""
    records = db.get_media_list(offset, limit, min_duration, max_duration, lang)
    total = db.get_media_count(min_duration, max_duration, lang)
    return records, total


def get_media_detail(media_id: int) -> dict | None:
    """Full record including transcript and frame_tags."""
    return db.get_record_by_id(media_id)


# ── Search ───────────────────────────────────────────────────────────────────

def search_media(
    query: str,
    n_results: int = 10,
    min_duration: float = 0,
    max_duration: float = 99999,
    lang: str | None = None,
) -> list[dict]:
    """Semantic search with post-filtering."""
    raw = vdb.search(query, n_results=n_results * 3)
    results = [
        r for r in raw
        if min_duration <= r.get("duration_s", 0) <= max_duration
        and (lang is None or r.get("lang") == lang)
    ]
    return results[:n_results]


# ── Stats ────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Aggregate stats for sidebar and dashboard KPIs."""
    return db.get_stats()


# ── Dashboard Data ───────────────────────────────────────────────────────────

def get_duration_by_lang() -> list[dict]:
    """Duration breakdown by language for dashboard chart."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT lang, SUM(duration_s) as total_s, COUNT(*) as count "
            "FROM media WHERE lang IS NOT NULL GROUP BY lang ORDER BY total_s DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_size_by_ext() -> list[dict]:
    """Storage breakdown by file extension."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT ext, SUM(size_mb) as total_mb, COUNT(*) as count "
            "FROM media GROUP BY ext ORDER BY total_mb DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Index Operations ─────────────────────────────────────────────────────────

def rebuild_index() -> int:
    """Drop and rebuild ChromaDB index. Returns record count."""
    records = db.get_all_records()
    col = vdb.get_collection(reset=True)
    for rec in records:
        vdb.upsert_record(col, rec)
    return len(records)


def embed_new_records() -> int:
    """Incremental embed: index only records not yet in ChromaDB."""
    col = vdb.get_collection(reset=False)
    result = col.get(include=["metadatas"])
    indexed_ids = {m["media_id"] for m in result["metadatas"]}

    records = db.get_all_records()
    to_process = [r for r in records if str(r["id"]) not in indexed_ids]

    for rec in to_process:
        vdb.upsert_record(col, rec)
    return len(to_process)


# ── Rating ────────────────────────────────────────────────────────────────────

def set_rating(media_id: int, rating: str | None, note: str | None = None):
    db.set_rating(media_id, rating, note)


def get_rating_stats() -> dict:
    return db.get_rating_stats()


# ── Tags ──────────────────────────────────────────────────────────────────────

def get_tags(media_id: int) -> list[dict]:
    return db.get_tags(media_id)


def add_tag(media_id: int, name: str, source: str = "manual"):
    db.add_tag(media_id, name, source)


def remove_tag(media_id: int, name: str):
    db.remove_tag(media_id, name)


def get_all_tag_names() -> list[dict]:
    return db.get_all_tag_names()


def get_top_tags(limit: int = 10) -> list[dict]:
    return db.get_top_tags(limit)


# ── Enhanced List ─────────────────────────────────────────────────────────────

def list_media_filtered(
    offset: int = 0,
    limit: int = 50,
    sort: str = "date",
    **filters,
) -> tuple[list[dict], int]:
    return db.get_media_filtered(offset, limit, sort, **filters)
