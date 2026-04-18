from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config as _config
from config import DB_PATH

from contextlib import contextmanager


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def to_relative(abs_path: str) -> str:
    """Absolute path -> relative to PROJECT_ROOT. Idempotent."""
    if not abs_path:
        return abs_path
    try:
        return str(Path(abs_path).relative_to(_config.PROJECT_ROOT))
    except ValueError:
        return abs_path


def resolve_path(rel_path: str) -> str:
    """Relative path -> absolute under PROJECT_ROOT. Idempotent."""
    if not rel_path:
        return rel_path
    path_obj = Path(rel_path)
    if path_obj.is_absolute():
        return str(path_obj)
    return str(_config.PROJECT_ROOT / path_obj)


def init_db():
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
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
            # Phase 10: WhisperX word-level timestamps for Remotion
            ("words_json", "TEXT"),
            # Phase 8.2: Smart Frame Analysis + Quality Assessment
            ("focus_score", "INTEGER"),
            ("exposure", "TEXT"),
            ("stability", "TEXT"),
            ("audio_quality", "TEXT"),
            ("atmosphere", "TEXT"),
            ("energy", "TEXT"),
            ("edit_position", "TEXT"),
            ("edit_reason", "TEXT"),
            ("editability_score", "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE media ADD COLUMN {col} {typ}")
            except Exception:
                pass
        for col, typ in [
            ("content_type", "TEXT"),
            ("focus_score", "INTEGER"),
            ("exposure", "TEXT"),
            ("stability", "TEXT"),
            ("audio_quality", "TEXT"),
            ("atmosphere", "TEXT"),
            ("energy", "TEXT"),
            ("edit_position", "TEXT"),
            ("edit_reason", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE frames ADD COLUMN {col} {typ}")
            except Exception:
                pass


def migrate_to_relative():
    """Convert DB path fields from absolute to relative paths."""
    media_count = 0
    frame_count = 0
    with get_conn() as conn:
        rows = conn.execute("SELECT id, path, thumbnail_path FROM media").fetchall()
        for row in rows:
            new_path = to_relative(row["path"]) if row["path"] else row["path"]
            new_thumb = to_relative(row["thumbnail_path"]) if row["thumbnail_path"] else row["thumbnail_path"]
            if new_path != row["path"] or new_thumb != row["thumbnail_path"]:
                try:
                    conn.execute(
                        "UPDATE media SET path=?, thumbnail_path=? WHERE id=?",
                        (new_path, new_thumb, row["id"]),
                    )
                    media_count += 1
                except sqlite3.IntegrityError as exc:
                    print(f"[migrate] warning: media id={row['id']} skipped due to UNIQUE conflict: {exc}")
        frame_rows = conn.execute(
            "SELECT id, thumbnail_path FROM frames WHERE thumbnail_path IS NOT NULL"
        ).fetchall()
        for row in frame_rows:
            new_thumb = to_relative(row["thumbnail_path"])
            if new_thumb != row["thumbnail_path"]:
                conn.execute(
                    "UPDATE frames SET thumbnail_path=? WHERE id=?",
                    (new_thumb, row["id"]),
                )
                frame_count += 1
    print(
        "[migrate] 完成。{0}/{1} media + {2}/{3} frames 路徑已轉為相對。".format(
            media_count, len(rows), frame_count, len(frame_rows)
        )
    )


def is_processed(path: str) -> bool:
    rel = to_relative(str(path))
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM media WHERE path=? OR path=?",
            (str(path), rel),
        ).fetchone()
        return row is not None


_ALLOWED_COLS = {
    "path", "filename", "ext", "duration_s", "size_mb", "width", "height",
    "fps", "has_audio", "transcript", "lang", "frame_tags", "thumbnail_path",
    "processed_at", "rating", "rating_note", "camera_make", "camera_model",
    "lens_model", "gps_lat", "gps_lon", "color_space", "iso", "shutter_speed",
    "aperture", "focal_length", "creation_date", "content_type",
    "start_tc",
    "segments_json",
    "words_json",
    "focus_score", "exposure", "stability", "audio_quality",
    "atmosphere", "energy", "edit_position", "edit_reason",
    "editability_score",
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
    "width, height, fps, has_audio, lang, thumbnail_path, processed_at, rating, "
    "editability_score"
)


def get_media_list(
    offset: int = 0,
    limit: int = 50,
    min_duration: float = 0,
    max_duration: float = 99999,
    lang: Optional[str] = None,
) -> List[Dict]:
    """Lightweight media records (no transcript/frame_tags) with pagination."""
    sql = f"SELECT {LIGHT_COLS} FROM media WHERE duration_s >= ? AND duration_s <= ?"
    params: List = [min_duration, max_duration]
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
    lang: Optional[str] = None,
) -> int:
    """Total count matching filters (for pagination)."""
    sql = "SELECT COUNT(*) FROM media WHERE duration_s >= ? AND duration_s <= ?"
    params: List = [min_duration, max_duration]
    if lang:
        sql += " AND lang = ?"
        params.append(lang)
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()[0]


def get_record_by_id(media_id: int) -> Optional[Dict]:
    """Full record including transcript and frame_tags."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
        return dict(row) if row else None


def get_stats() -> Dict:
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


def get_all_records() -> List[Dict]:
    """Full records (for embed rebuild)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM media ORDER BY id").fetchall()
        return [dict(r) for r in rows]


# ── Rating Operations ────────────────────────────────────────────────────────

def set_rating(media_id: int, rating: Optional[str], note: Optional[str] = None):
    """Set rating for a media asset. rating: 'good'/'ng'/'review'/None."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE media SET rating = ?, rating_note = ? WHERE id = ?",
            (rating, note, media_id),
        )


# ── Tag Operations ────────────────────────────────────────────────────────────

def get_tags(media_id: int) -> List[Dict]:
    """Get all tags for a media asset."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, source FROM tags WHERE media_id = ? ORDER BY name",
            (media_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_tag(media_id: int, name: str, source: str = "manual", _conn=None):
    """Add a tag (idempotent via UNIQUE constraint)."""
    def _do(c):
        c.execute(
            "INSERT OR IGNORE INTO tags (media_id, name, source) VALUES (?, ?, ?)",
            (media_id, name.strip().lower(), source),
        )
    if _conn is not None:
        _do(_conn)
    else:
        with get_conn() as conn:
            _do(conn)


def remove_tag(media_id: int, name: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM tags WHERE media_id = ? AND name = ?",
            (media_id, name.strip().lower()),
        )


def get_all_tag_names() -> List[Dict]:
    """All unique tag names with usage count, for autocomplete."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, source, COUNT(*) as count FROM tags "
            "GROUP BY name ORDER BY count DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_top_tags(limit: int = 10) -> List[Dict]:
    """Top N most used tags."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, COUNT(*) as count FROM tags "
            "GROUP BY name ORDER BY count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Frame Operations ─────────────────────────────────────────────────────────

def compute_editability(rec: Dict) -> float:
    """0-100 quality score based on focus, exposure, stability, audio, and rating."""
    score = 50.0
    focus_score = rec.get("focus_score")
    if focus_score is not None:
        try:
            score += (int(focus_score) - 3) * 10
        except (ValueError, TypeError):
            pass
    if rec.get("exposure") == "normal":
        score += 10
    elif rec.get("exposure") in ("dark", "over"):
        score -= 10
    if rec.get("stability") == "穩定":
        score += 10
    elif rec.get("stability") == "嚴重晃動":
        score -= 15
    if rec.get("audio_quality") == "清晰":
        score += 10
    elif rec.get("audio_quality") == "嘈雜":
        score -= 5
    rating = rec.get("rating")
    if rating == "good":
        score += 10
    elif rating == "ng":
        score -= 15
    return max(0.0, min(100.0, round(score, 1)))


def upsert_frame(
    media_id: int,
    frame_index: int,
    timestamp_s: float,
    thumbnail_path: Optional[str] = None,
    description: str = "",
    tags: str = "",
    content_type: Optional[str] = None,
    focus_score: Optional[int] = None,
    exposure: Optional[str] = None,
    stability: Optional[str] = None,
    audio_quality: Optional[str] = None,
    atmosphere: Optional[str] = None,
    energy: Optional[str] = None,
    edit_position: Optional[str] = None,
    edit_reason: Optional[str] = None,
    _conn=None,
):
    def _do(c):
        c.execute("""
            INSERT INTO frames (
                media_id, frame_index, timestamp_s, thumbnail_path, description, tags,
                content_type, focus_score, exposure, stability, audio_quality,
                atmosphere, energy, edit_position, edit_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_id, frame_index) DO UPDATE SET
                timestamp_s=excluded.timestamp_s, thumbnail_path=excluded.thumbnail_path,
                description=excluded.description, tags=excluded.tags,
                content_type=excluded.content_type, focus_score=excluded.focus_score,
                exposure=excluded.exposure, stability=excluded.stability,
                audio_quality=excluded.audio_quality, atmosphere=excluded.atmosphere,
                energy=excluded.energy, edit_position=excluded.edit_position,
                edit_reason=excluded.edit_reason
        """, (
            media_id, frame_index, timestamp_s, thumbnail_path, description, tags,
            content_type, focus_score, exposure, stability, audio_quality,
            atmosphere, energy, edit_position, edit_reason,
        ))
    if _conn is not None:
        _do(_conn)
    else:
        with get_conn() as conn:
            _do(conn)


def get_frames(media_id: int) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM frames WHERE media_id = ? ORDER BY frame_index",
            (media_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_frames(media_id: int, _conn=None):
    def _do(c):
        c.execute("DELETE FROM frames WHERE media_id = ?", (media_id,))
    if _conn is not None:
        _do(_conn)
    else:
        with get_conn() as conn:
            _do(conn)


# ── Enhanced Queries (Phase 4 UI) ─────────────────────────────────────────────

def _build_filter_clause(
    min_duration: float = 0,
    max_duration: float = 99999,
    lang: Optional[str] = None,
    rating: Optional[str] = None,
    media_type: Optional[str] = None,
) -> Tuple[str, List]:
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
        clauses.append("ext IN ('.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.mts')")
    elif media_type == "audio":
        clauses.append("ext IN ('.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg')")
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
) -> Tuple[List[Dict], int]:
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
