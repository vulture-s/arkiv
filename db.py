from __future__ import annotations

import sqlite3

from config import DB_PATH


from contextlib import contextmanager


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS media (
                id             INTEGER PRIMARY KEY,
                path           TEXT UNIQUE,
                filename       TEXT,
                ext            TEXT,
                duration_s     REAL,
                size_mb        REAL,
                width          INTEGER,
                height         INTEGER,
                fps            REAL,
                has_audio      INTEGER DEFAULT 0,
                transcript     TEXT,
                lang           TEXT,
                frame_tags     TEXT,
                thumbnail_path TEXT,
                processed_at   TEXT
            )
        """)
        # tags table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id       INTEGER PRIMARY KEY,
                media_id INTEGER REFERENCES media(id) ON DELETE CASCADE,
                name     TEXT NOT NULL,
                source   TEXT DEFAULT 'manual',
                UNIQUE(media_id, name)
            )
        """)
        # frames table (persistent frame analysis)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS frames (
                id             INTEGER PRIMARY KEY,
                media_id       INTEGER REFERENCES media(id) ON DELETE CASCADE,
                frame_index    INTEGER NOT NULL,
                timestamp_s    REAL NOT NULL,
                thumbnail_path TEXT,
                description    TEXT,
                tags           TEXT,
                UNIQUE(media_id, frame_index)
            )
        """)
        # migrations: add columns if upgrading from older schema
        for col, typ in [
            ("thumbnail_path", "TEXT"),
            ("rating", "TEXT"),
            ("rating_note", "TEXT"),
            # Phase 8: ExifTool metadata + content classification
            ("camera_make", "TEXT"),
            ("camera_model", "TEXT"),
            ("lens_model", "TEXT"),
            ("gps_lat", "REAL"),
            ("gps_lon", "REAL"),
            ("color_space", "TEXT"),
            ("iso", "INTEGER"),
            ("shutter_speed", "TEXT"),
            ("aperture", "REAL"),
            ("focal_length", "REAL"),
            ("creation_date", "TEXT"),
            ("content_type", "TEXT"),
            ("start_tc", "TEXT"),
            # Phase 9.4: Whisper segment timestamps for precise SRT/VTT
            ("segments_json", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE media ADD COLUMN {col} {typ}")
            except Exception:
                pass


def is_processed(path: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM media WHERE path=?", (path,)).fetchone()
        return row is not None


_ALLOWED_COLS = {
    "path", "filename", "ext", "duration_s", "size_mb", "width", "height",
    "fps", "has_audio", "transcript", "lang", "frame_tags", "thumbnail_path",
    "processed_at", "rating", "rating_note", "camera_make", "camera_model",
    "lens_model", "gps_lat", "gps_lon", "color_space", "iso", "shutter_speed",
    "aperture", "focal_length", "creation_date", "content_type",
    "start_tc",
    "segments_json",
}


def upsert(record: dict):
    # Only allow known column names to prevent SQL injection via dict keys
    safe = {k: v for k, v in record.items() if k in _ALLOWED_COLS}
    if not safe:
        return
    cols = ", ".join(safe.keys())
    placeholders = ", ".join(["?"] * len(safe))
    updates = ", ".join(f"{k}=excluded.{k}" for k in safe if k != "path")
    sql = f"""
        INSERT INTO media ({cols}) VALUES ({placeholders})
        ON CONFLICT(path) DO UPDATE SET {updates}
    """
    with get_conn() as conn:
        conn.execute(sql, list(safe.values()))


# ── Lightweight queries (Phase 4) ────────────────────────────────────────────

LIGHT_COLS = (
    "id, path, filename, ext, duration_s, size_mb, "
    "width, height, fps, has_audio, lang, thumbnail_path, processed_at, rating"
)


def get_media_list(
    offset: int = 0,
    limit: int = 50,
    min_duration: float = 0,
    max_duration: float = 99999,
    lang: str | None = None,
) -> list[dict]:
    """Lightweight media records (no transcript/frame_tags) with pagination."""
    sql = f"SELECT {LIGHT_COLS} FROM media WHERE duration_s >= ? AND duration_s <= ?"
    params: list = [min_duration, max_duration]
    if lang:
        sql += " AND lang = ?"
        params.append(lang)
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_media_count(
    min_duration: float = 0,
    max_duration: float = 99999,
    lang: str | None = None,
) -> int:
    """Total count matching filters (for pagination)."""
    sql = "SELECT COUNT(*) FROM media WHERE duration_s >= ? AND duration_s <= ?"
    params: list = [min_duration, max_duration]
    if lang:
        sql += " AND lang = ?"
        params.append(lang)
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()[0]


def get_record_by_id(media_id: int) -> dict | None:
    """Full record including transcript and frame_tags."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
        return dict(row) if row else None


def get_stats() -> dict:
    """Aggregate stats for sidebar and dashboard."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        with_transcript = conn.execute(
            "SELECT COUNT(*) FROM media WHERE transcript IS NOT NULL"
        ).fetchone()[0]
        with_thumb = conn.execute(
            "SELECT COUNT(*) FROM media WHERE thumbnail_path IS NOT NULL"
        ).fetchone()[0]
        total_duration = conn.execute(
            "SELECT COALESCE(SUM(duration_s), 0) FROM media"
        ).fetchone()[0]
        total_size = conn.execute(
            "SELECT COALESCE(SUM(size_mb), 0) FROM media"
        ).fetchone()[0]
        langs = conn.execute(
            "SELECT lang, COUNT(*) as cnt FROM media "
            "WHERE lang IS NOT NULL GROUP BY lang ORDER BY cnt DESC"
        ).fetchall()
        return {
            "total": total,
            "with_transcript": with_transcript,
            "with_thumb": with_thumb,
            "total_duration_s": total_duration,
            "total_size_mb": total_size,
            "langs": {r["lang"]: r["cnt"] for r in langs},
        }


def get_all_records() -> list[dict]:
    """Full records (for embed rebuild)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM media ORDER BY id").fetchall()
        return [dict(r) for r in rows]


# ── Rating Operations ────────────────────────────────────────────────────────

def set_rating(media_id: int, rating: str | None, note: str | None = None):
    """Set rating for a media asset. rating: 'good'/'ng'/'review'/None."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE media SET rating = ?, rating_note = ? WHERE id = ?",
            (rating, note, media_id),
        )


# ── Tag Operations ────────────────────────────────────────────────────────────

def get_tags(media_id: int) -> list[dict]:
    """Get all tags for a media asset."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, source FROM tags WHERE media_id = ? ORDER BY name",
            (media_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_tag(media_id: int, name: str, source: str = "manual"):
    """Add a tag (idempotent via UNIQUE constraint)."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tags (media_id, name, source) VALUES (?, ?, ?)",
            (media_id, name.strip().lower(), source),
        )


def remove_tag(media_id: int, name: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM tags WHERE media_id = ? AND name = ?",
            (media_id, name.strip().lower()),
        )


def get_all_tag_names() -> list[dict]:
    """All unique tag names with usage count, for autocomplete."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, source, COUNT(*) as count FROM tags "
            "GROUP BY name ORDER BY count DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_top_tags(limit: int = 10) -> list[dict]:
    """Top N most used tags."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, COUNT(*) as count FROM tags "
            "GROUP BY name ORDER BY count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Frame Operations ─────────────────────────────────────────────────────────

def upsert_frame(media_id: int, frame_index: int, timestamp_s: float,
                 thumbnail_path: str | None = None, description: str = "",
                 tags: str = ""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO frames (media_id, frame_index, timestamp_s, thumbnail_path, description, tags)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_id, frame_index) DO UPDATE SET
                timestamp_s=excluded.timestamp_s, thumbnail_path=excluded.thumbnail_path,
                description=excluded.description, tags=excluded.tags
        """, (media_id, frame_index, timestamp_s, thumbnail_path, description, tags))


def get_frames(media_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM frames WHERE media_id = ? ORDER BY frame_index",
            (media_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_frames(media_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM frames WHERE media_id = ?", (media_id,))


# ── Enhanced Queries (Phase 4 UI) ─────────────────────────────────────────────

def _build_filter_clause(
    min_duration: float = 0,
    max_duration: float = 99999,
    lang: str | None = None,
    rating: str | None = None,
    media_type: str | None = None,
) -> tuple[str, list]:
    """Build WHERE clause and params for common filters."""
    clauses = ["duration_s >= ?", "duration_s <= ?"]
    params: list = [min_duration, max_duration]
    if lang:
        clauses.append("lang = ?")
        params.append(lang)
    if rating == "unrated":
        clauses.append("rating IS NULL")
    elif rating:
        clauses.append("rating = ?")
        params.append(rating)
    if media_type == "video":
        clauses.append("ext IN ('.mp4', '.mov', '.m4v', '.mts')")
    elif media_type == "audio":
        clauses.append("ext IN ('.wav', '.mp3', '.m4a', '.aac')")
    return " AND ".join(clauses), params


SORT_MAP = {
    "date": "processed_at DESC",
    "name": "filename ASC",
    "duration": "duration_s DESC",
    "size": "size_mb DESC",
    "rating": "CASE rating WHEN 'good' THEN 1 WHEN 'review' THEN 2 WHEN 'ng' THEN 3 ELSE 4 END",
}


def get_media_filtered(
    offset: int = 0,
    limit: int = 50,
    sort: str = "date",
    **filters,
) -> tuple[list[dict], int]:
    """Filtered + sorted media list with total count."""
    where, params = _build_filter_clause(**filters)
    order = SORT_MAP.get(sort, "id")
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM media WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {LIGHT_COLS} FROM media WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total


def get_rating_stats() -> dict:
    """Count by rating for analytics bar."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT rating, COUNT(*) as cnt FROM media GROUP BY rating"
        ).fetchall()
        result = {"good": 0, "ng": 0, "review": 0, "unrated": 0}
        for r in rows:
            key = r["rating"] if r["rating"] else "unrated"
            result[key] = r["cnt"]
        return result
