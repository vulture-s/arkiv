from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config as _config
from config import DB_PATH

from contextlib import contextmanager


# audit L14: once-only init — the parent-dir check + double chmod used to run
# on EVERY connection, multiplying syscalls on N+1-heavy call paths. Keyed on
# the DB path (not a plain bool) so tests / `--db` that rebind db.DB_PATH at
# runtime still re-init for the new location.
_init_done_for: Optional[str] = None


@contextmanager
def get_conn():
    global _init_done_for
    first_open = _init_done_for != str(DB_PATH)
    if first_open:
        # Ensure the DB's parent dir exists. On a fresh clone the .arkiv/ data dir
        # doesn't exist yet, and server.py calls init_db() at import → sqlite would
        # raise "unable to open database file". Covers every DB-opening path
        # (server / ingest / embed / tests), not just server startup.
        parent = Path(DB_PATH).expanduser().parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
            # Only tighten a dir WE just created — never an existing (possibly shared)
            # parent, which could strip access from unrelated files.
            try:
                os.chmod(parent, 0o700)
            except OSError:
                pass
    conn = sqlite3.connect(DB_PATH, timeout=30)
    # audit M6: SQLite ships with foreign_keys OFF per-connection, so every
    # ON DELETE CASCADE in the schema was dead — revoking a token or deleting
    # media left orphan child rows. init_db() clears pre-existing orphans via
    # foreign_key_check before enforcement can bite on legacy data.
    conn.execute("PRAGMA foreign_keys=ON")
    if first_open:
        # Our own token-hash DB file — keep it owner-only on shared hosts.
        # Best-effort (no-op / may fail on Windows).
        try:
            os.chmod(DB_PATH, 0o600)
        except OSError:
            pass
        _init_done_for = str(DB_PATH)
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
    """Absolute path -> relative to PROJECT_ROOT, stored POSIX-style. Idempotent.

    Cross-platform: relative paths are persisted with forward slashes on EVERY
    OS (`.as_posix()`), so a DB written on Windows and a DB written on mac/NAS
    agree. Without this, str(WindowsPath('media/clip.mp4')) == 'media\\clip.mp4',
    and a PC opening a NAS DB that mac wrote saw every file as unprocessed
    (backslash rel != stored forward-slash rel) → whole-library re-ingest. No-op
    on POSIX (str == as_posix there)."""
    if not abs_path:
        return abs_path
    try:
        return Path(abs_path).relative_to(_config.PROJECT_ROOT).as_posix()
    except ValueError:
        return abs_path


def resolve_path(rel_path: str) -> str:
    """Relative path -> absolute under PROJECT_ROOT. Idempotent.

    Codex Round-2 audit (J2): poisoned DB row with `../../../etc/passwd` was
    joined naively, letting /api/stream/{id} serve out-of-root files. Now the
    canonical form is checked against PROJECT_ROOT — relative paths that escape
    the root raise instead of silently expanding.

    Absolute paths are still passed through as-is (some legacy rows store
    absolutes, and they came in via trusted ingest paths). The 8.0c per-project
    storage migration will eventually flip everything to relative.
    """
    if not rel_path:
        return rel_path
    # Defense-in-depth: a row written by a pre-fix Windows ingest may hold
    # backslashes ('media\\clip.mp4'). On POSIX that's a literal filename, not a
    # path — normalize so such legacy rows still resolve cross-OS. New writes are
    # already forward-slash (to_relative.as_posix). Skip if it's a real absolute
    # Windows path (drive-letter), which Path handles natively.
    path_obj = Path(rel_path)
    if not path_obj.is_absolute() and "\\" in rel_path:
        path_obj = Path(rel_path.replace("\\", "/"))
    if path_obj.is_absolute():
        return str(path_obj)
    project_root = _config.PROJECT_ROOT.resolve()
    joined = (project_root / path_obj).resolve()
    try:
        joined.relative_to(project_root)
    except ValueError:
        raise ValueError(
            f"DB rel_path 解析後逃出 PROJECT_ROOT 邊界：{rel_path!r} → {joined!s}"
        )
    return str(joined)


# Identifiers that _add_column_if_missing is allowed to interpolate into DDL.
# sqlite can't bind table/column names, so the ALTER below is an f-string — every
# caller passes a hardcoded literal from the migration list, never user input.
# fable-audit 2026-07-12 (#db.py:124): assert that invariant so a future caller
# can't silently turn this helper into a SQL-injection sink.
_MIGRATION_TABLES = frozenset({"media", "frames", "access_tokens"})
_MIGRATION_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _add_column_if_missing(conn, table: str, col: str, typ: str):
    """ALTER TABLE ... ADD COLUMN that only swallows the expected
    "duplicate column name" error.

    audit L10: the old bare `except Exception: pass` here also ate
    database-locked / disk-I/O errors, silently skipping schema migrations —
    those must surface, only the idempotent re-run case is benign."""
    if table not in _MIGRATION_TABLES or not _MIGRATION_IDENT_RE.match(col):
        raise ValueError(
            "refusing unsafe migration identifier: "
            "table={0!r} col={1!r}".format(table, col)
        )
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


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
                reel_name      TEXT,
                white_balance  TEXT,
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
            ("file_hash", "TEXT"),
            ("hash_algo", "TEXT DEFAULT 'xxh3-128'"),
            ("hash_verified_at", "TEXT"),
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
            ("reel_name", "TEXT"),
            ("white_balance", "TEXT"),
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
            # Phase: persist ffprobe codec so Phase 3 proxy decisions don't
            # re-probe the whole library each ingest (H1).
            ("codec", "TEXT"),
            # Optional LLM-canonicalized media tag list (JSON array). Stored
            # SEPARATELY from the raw vision tags (never overwrites them) so the
            # UI can toggle raw ↔ canonical; populated on demand by the re-tag
            # command, NULL until then.
            ("canonical_tags", "TEXT"),
        ]:
            _add_column_if_missing(conn, "media", col, typ)  # audit L10
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
            _add_column_if_missing(conn, "frames", col, typ)  # audit L10
        conn.execute("""
            CREATE TABLE IF NOT EXISTS access_tokens (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                token_hash TEXT UNIQUE NOT NULL,
                hash_algo TEXT NOT NULL DEFAULT 'sha256',
                expires_at TEXT,
                allowed_ips_json TEXT NOT NULL DEFAULT '["*"]',
                last_used_at TEXT,
                last_used_ip TEXT,
                last_used_user_agent TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Phase 16.1: hash_algo on pre-existing token tables (sha256 legacy).
        _add_column_if_missing(  # audit L10
            conn, "access_tokens", "hash_algo", "TEXT NOT NULL DEFAULT 'sha256'"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS access_token_scopes (
                token_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                PRIMARY KEY (token_id, scope),
                FOREIGN KEY (token_id) REFERENCES access_tokens(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_access_tokens_hash ON access_tokens(token_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_access_token_scopes_token_id ON access_token_scopes(token_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_conversations (
                id TEXT PRIMARY KEY,
                user_token_id TEXT,
                title TEXT,
                project_scope_json TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                intent TEXT,
                scene_ids_json TEXT,
                tokens_used INTEGER DEFAULT 0,
                stage TEXT,
                latency_ms INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_msg_conv ON chat_messages(conversation_id, created_at)"
        )
        # Phase 11.5c: SQLite-backed ingest job queue (no Redis/Celery — per
        # roadmap 11.5c). priority is derived from type; lower runs first.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id          INTEGER PRIMARY KEY,
                type        TEXT NOT NULL,
                target      TEXT,
                priority    INTEGER NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                started_at  TEXT,
                finished_at TEXT,
                error       TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_priority "
            "ON jobs(status, priority, created_at)"
        )
        # Phase 9.7 G2: per-language transcript archive. media.transcript/lang/
        # segments_json/words_json stay the ACTIVE transcript (what search indexes
        # + exports use); this table keeps every transcribed language so a
        # retranscribe in another language no longer destroys the previous one.
        # One row per (media_id, lang); the active language's row mirrors media.*.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transcripts (
                id            INTEGER PRIMARY KEY,
                media_id      INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                lang          TEXT NOT NULL,
                transcript    TEXT,
                segments_json TEXT,
                words_json    TEXT,
                updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(media_id, lang)
            )
        """)
        # Phase 9.7 G5②: persisted settings overrides. config.py holds the
        # baked-in defaults; this table stores only what the operator has
        # explicitly changed. scope='global' is the library-wide default; a
        # scope set to a PROJECT_ROOT path overrides global for that project.
        # The effective value for a key = default ← global row ← project row.
        # Only curated keys (settings.SETTINGS_SCHEMA) are ever written here.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                scope      TEXT NOT NULL DEFAULT 'global',
                key        TEXT NOT NULL,
                value      TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(scope, key)
            )
        """)
        # audit M6: PRAGMA foreign_keys was never enabled before, so orphan
        # child rows accumulated (e.g. scopes of revoked tokens, tags/frames of
        # deleted media). Clear them once here so the now-active enforcement in
        # get_conn() doesn't start failing writes against legacy inconsistency.
        _known_child_tables = {"tags", "frames", "access_token_scopes", "chat_messages", "transcripts"}
        try:
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        except sqlite3.Error:
            violations = []
        removed = 0
        for v in violations:
            tbl, rowid = v[0], v[1]
            # Only touch tables we know are pure child rows — never auto-delete
            # from anything unexpected.
            if tbl in _known_child_tables and rowid is not None:
                conn.execute(
                    "DELETE FROM {0} WHERE rowid=?".format(tbl), (rowid,)
                )
                removed += 1
        if removed:
            print(
                "[init_db] foreign_key_check: removed {0} orphan child row(s)"
                " left from pre-enforcement era (audit M6)".format(removed)
            )


def migrate_to_relative():
    """Convert DB path fields from absolute to relative paths."""
    media_count = 0
    frame_count = 0
    merged_count = 0
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
                except sqlite3.IntegrityError:
                    # audit H5: another row already holds the relative form
                    # (abs/rel duplicate pair — upsert's ON CONFLICT(path) never
                    # fires across the two forms). Merge instead of skip: move
                    # over child rows the survivor doesn't already have, then
                    # drop the duplicate. UPDATE OR IGNORE skips children that
                    # would violate UNIQUE(media_id, frame_index/name) — the
                    # survivor's own copy wins there.
                    survivor = conn.execute(
                        "SELECT id FROM media WHERE path=? AND id<>?",
                        (new_path, row["id"]),
                    ).fetchone()
                    if survivor is None:
                        print(f"[migrate] warning: media id={row['id']} UNIQUE conflict without locatable survivor — skipped")
                        continue
                    sid = survivor["id"]
                    conn.execute("UPDATE OR IGNORE frames SET media_id=? WHERE media_id=?", (sid, row["id"]))
                    conn.execute("DELETE FROM frames WHERE media_id=?", (row["id"],))
                    conn.execute("UPDATE OR IGNORE tags SET media_id=? WHERE media_id=?", (sid, row["id"]))
                    conn.execute("DELETE FROM tags WHERE media_id=?", (row["id"],))
                    # fable-audit round-5 #7: transcripts.media_id is ON DELETE
                    # CASCADE, so the media DELETE below would wipe the loser's
                    # per-language transcript archive. These abs/rel rows are the
                    # SAME physical file, so re-parent every language the survivor
                    # LACKS (UPDATE OR IGNORE); a shared-language conflict keeps the
                    # survivor's authoritative copy (identical content) and the
                    # cascade drops the redundant loser row. Without this, a language
                    # present only on the loser (survivor zh, loser en) was silently
                    # destroyed by the cascade.
                    conn.execute("UPDATE OR IGNORE transcripts SET media_id=? WHERE media_id=?", (sid, row["id"]))
                    conn.execute("DELETE FROM transcripts WHERE media_id=?", (row["id"],))
                    conn.execute("DELETE FROM media WHERE id=?", (row["id"],))
                    merged_count += 1
                    print(f"[migrate] merged duplicate media id={row['id']} into id={sid} ({new_path})")
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
    if merged_count:
        print("[migrate] 另合併 {0} 組 abs/rel 重複 row（audit H5）。".format(merged_count))


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
    "processed_at", "rating", "rating_note", "file_hash", "hash_algo",
    "hash_verified_at", "camera_make", "camera_model",
    "lens_model", "gps_lat", "gps_lon", "color_space", "iso", "shutter_speed",
    "aperture", "focal_length", "creation_date", "white_balance", "content_type",
    "reel_name",
    "start_tc",
    "segments_json",
    "words_json",
    "focus_score", "exposure", "stability", "audio_quality",
    "atmosphere", "energy", "edit_position", "edit_reason",
    "editability_score",
    "codec",
}


def upsert(record: dict, _conn=None):
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
    # Accept an external connection so callers can write the media row and its
    # frame rows in one transaction (H7: a crash between the two used to leave a
    # frame-less row that is_processed then skipped forever).
    if _conn is not None:
        _conn.execute(sql, list(safe.values()))
    else:
        with get_conn() as conn:
            conn.execute(sql, list(safe.values()))


def update_media_by_id(media_id: int, record: dict, _conn=None):
    """UPDATE an existing media row by id (column-subset, same key filter as
    upsert).

    audit H5: refresh used to go through upsert's ON CONFLICT(path), which
    never fires when the stored path is absolute and the incoming one relative
    (or vice versa) — silently INSERTing a duplicate row that frames/tags then
    attached to arbitrarily. Callers that already resolved the row id (via
    abs-OR-rel lookup) update in place instead, which also normalizes a legacy
    absolute path to the incoming relative form."""
    safe = {k: v for k, v in record.items() if k in _ALLOWED_COLS}
    if not safe:
        return
    sets = ", ".join(f"{k}=?" for k in safe)
    sql = f"UPDATE media SET {sets} WHERE id=?"
    params = list(safe.values()) + [media_id]
    if _conn is not None:
        _conn.execute(sql, params)
    else:
        with get_conn() as conn:
            conn.execute(sql, params)


# ── Lightweight queries (Phase 4) ────────────────────────────────────────────

LIGHT_COLS = (
    "id, path, filename, ext, duration_s, size_mb, "
    "width, height, fps, has_audio, lang, thumbnail_path, processed_at, rating, "
    "editability_score, "
    # so the grid/inspector can show camera provenance without a per-clip detail
    # fetch (these live in the DB but were absent from the list shape).
    "camera_make, camera_model, lens_model, reel_name, start_tc, codec"
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


# ── Phase 9.7 G2: per-language transcript archive ────────────────────────────

def upsert_transcript(media_id, lang, transcript, segments_json, words_json, _conn=None):
    """Archive a transcript for (media_id, lang). Overwrites that language's row
    only — other languages are untouched. Pass _conn to join an open transaction."""
    if not lang:
        return
    sql = (
        "INSERT INTO transcripts (media_id, lang, transcript, segments_json, words_json, updated_at) "
        "VALUES (?,?,?,?,?, datetime('now')) "
        "ON CONFLICT(media_id, lang) DO UPDATE SET "
        "transcript=excluded.transcript, segments_json=excluded.segments_json, "
        "words_json=excluded.words_json, updated_at=excluded.updated_at"
    )
    params = (media_id, lang, transcript, segments_json, words_json)
    if _conn is not None:
        _conn.execute(sql, params)
    else:
        with get_conn() as conn:
            conn.execute(sql, params)


def get_transcripts(media_id) -> List[Dict]:
    """All archived language versions for a media, newest-updated first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT lang, transcript, segments_json, words_json, updated_at "
            "FROM transcripts WHERE media_id=? ORDER BY updated_at DESC, lang",
            (media_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_transcript(media_id, lang) -> Optional[Dict]:
    """One archived language version, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT lang, transcript, segments_json, words_json, updated_at "
            "FROM transcripts WHERE media_id=? AND lang=?",
            (media_id, lang),
        ).fetchone()
    return dict(row) if row else None


def set_canonical_tags(media_id: int, tags: list) -> None:
    """Store the LLM-canonicalized tag list (JSON) for a media. Separate from the
    raw vision tags — never touches frame_tags / the tags table."""
    import json as _json
    with get_conn() as conn:
        conn.execute(
            "UPDATE media SET canonical_tags=? WHERE id=?",
            (_json.dumps(tags, ensure_ascii=False), media_id),
        )


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
    """Add a tag (idempotent via UNIQUE(media_id, name)).

    A manual add PROMOTES an existing auto row to source='manual' so that a later
    re-ingest's auto-tag clear (delete_auto_tags) won't remove a tag the user has
    confirmed by hand. An auto add never downgrades a manual row. (Codex review
    P2 — relying on the original source alone lost user-confirmed tags.)"""
    def _do(c):
        c.execute(
            "INSERT INTO tags (media_id, name, source) VALUES (?, ?, ?) "
            "ON CONFLICT(media_id, name) DO UPDATE SET source='manual' "
            "WHERE excluded.source='manual'",
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


def delete_auto_tags(media_id: int, _conn=None):
    """Drop a clip's machine-generated tags (source='auto'), preserving any
    manual ones. Used on re-ingest so stale/incorrect auto tags (e.g. a fixed
    vision mislabel) don't linger as a union with the freshly generated set."""
    def _do(c):
        c.execute(
            "DELETE FROM tags WHERE media_id = ? AND source = 'auto'",
            (media_id,),
        )
    if _conn is not None:
        _do(_conn)
    else:
        with get_conn() as conn:
            _do(conn)


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


def get_frames(media_id: int, _conn=None) -> List[Dict]:
    def _do(c):
        rows = c.execute(
            "SELECT * FROM frames WHERE media_id = ? ORDER BY frame_index",
            (media_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    # _conn lets a caller read frames inside its own open write txn — without it
    # a second connection can't see the txn's uncommitted UPDATEs (stale read,
    # audit M1) and on SQLite would block on the writer lock (audit C1).
    if _conn is not None:
        return _do(_conn)
    with get_conn() as conn:
        return _do(conn)


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


# fable-audit round-5 #12: every sort has a unique `, id` tiebreaker. Without it,
# non-unique sort keys (hundreds of C0001.MP4 filenames, equal durations, NULL
# processed_at) leave row order undefined, so LIMIT/OFFSET pagination can repeat or
# drop rows across page fetches — and media_position (the "next/prev in this sort")
# can disagree with the grid. id is the PK, so it's a total order.
SORT_MAP = {
    "date": "processed_at DESC, id DESC",
    "name": "filename ASC, id ASC",
    "duration": "duration_s DESC, id DESC",
    "size": "size_mb DESC, id DESC",
    "rating": "CASE rating WHEN 'good' THEN 1 WHEN 'review' THEN 2 WHEN 'ng' THEN 3 ELSE 4 END, id DESC",
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
