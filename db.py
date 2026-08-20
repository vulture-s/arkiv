from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config as _config
import mediatypes

from contextlib import contextmanager


# Stamped as `first_seen_version` for a library that already existed before the
# provenance anchor shipped: we know it predates the anchor, but not under which
# build, and a made-up number would claim otherwise. `entitlements` treats an
# unparseable version as exempt by design, so this reads as grandfathered — see
# the long comment at the INSERT in init_db() for why that matters.
PRE_ANCHOR_VERSION = "pre-anchor"


# R5-23 (#54): the DB path is a SINGLE source of truth reached ONLY through
# get_db_path()/set_db_path(). Previously db.py did `from config import DB_PATH`
# (a frozen value copy) while `--db` rebound db.DB_PATH and health/server read
# config.DB_PATH — so a `--db` run preflighted the DEFAULT db while writing the
# backup db (a silent mixed-database run). Now `--db` calls set_db_path() and
# every reader calls get_db_path(); a lint test forbids value imports of DB_PATH.
# The override defaults to None → we follow config.DB_PATH dynamically, so a test
# that monkeypatches config.DB_PATH is honored without a second knob.
_db_path_override: Optional[Path] = None


def get_db_path() -> Path:
    """The one true DB path. An explicit set_db_path()/--db override wins;
    otherwise follow config.DB_PATH so an env/config change is picked up live."""
    return _db_path_override if _db_path_override is not None else _config.DB_PATH


def set_db_path(path) -> Path:
    """Point every db.get_conn() (and every reader routed through get_db_path())
    at `path`. Pass None to fall back to config.DB_PATH."""
    global _db_path_override, _init_done_for
    _db_path_override = Path(path) if path is not None else None
    _init_done_for = None  # force re-init against the new location on next open
    return get_db_path()


# audit L14: once-only init — the parent-dir check + double chmod used to run
# on EVERY connection, multiplying syscalls on N+1-heavy call paths. Keyed on
# the DB path (not a plain bool) so tests / `--db` that switch the path at
# runtime still re-init for the new location.
_init_done_for: Optional[str] = None


@contextmanager
def get_conn():
    global _init_done_for
    db_path = get_db_path()
    first_open = _init_done_for != str(db_path)
    if first_open:
        # Ensure the DB's parent dir exists. On a fresh clone the .arkiv/ data dir
        # doesn't exist yet, and init_db() at startup would otherwise make sqlite
        # raise "unable to open database file". Covers every DB-opening path
        # (server / ingest / embed / tests), not just server startup.
        parent = Path(db_path).expanduser().parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
            # Only tighten a dir WE just created — never an existing (possibly shared)
            # parent, which could strip access from unrelated files.
            try:
                os.chmod(parent, 0o700)
            except OSError:
                pass
    conn = sqlite3.connect(db_path, timeout=30)
    # audit M6: SQLite ships with foreign_keys OFF per-connection, so every
    # ON DELETE CASCADE in the schema was dead — revoking a token or deleting
    # media left orphan child rows. init_db() clears pre-existing orphans via
    # foreign_key_check before enforcement can bite on legacy data.
    conn.execute("PRAGMA foreign_keys=ON")
    if first_open:
        # Our own token-hash DB file — keep it owner-only on shared hosts.
        # Best-effort (no-op / may fail on Windows).
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass
        _init_done_for = str(db_path)
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


def dedup_path_variants(path: str) -> List[str]:
    """Every stored form a file could match on for dedup: its absolute path, the
    forward-slash relative form (what new ingests write), and the BACKSLASH relative
    form a pre-fix Windows ingest wrote (fable-audit round-5 #9). Without the
    backslash form, a NAS DB written by old Windows ingest and opened on mac looked
    entirely unprocessed and re-ingested the whole library as duplicates."""
    abs_path = str(path)
    rel = to_relative(abs_path)
    variants = [abs_path, rel]
    back = rel.replace("/", "\\")
    if back != rel:
        variants.append(back)
    return variants


def canonical_stored_path(stored: str) -> str:
    """Canonicalise a STORED path value to forward-slash relative. to_relative alone
    can't convert a backslash-relative path ('media\\clip.mp4' is neither absolute
    nor under PROJECT_ROOT, so it's returned unchanged), which is why migrate_to_
    relative never reconciled pre-fix Windows rows (fable-audit round-5 #9)."""
    if not stored:
        return stored
    p = Path(stored)
    if not p.is_absolute() and "\\" in stored:
        return stored.replace("\\", "/")
    return to_relative(stored)


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


def _backfill_shot_date(conn):
    """Populate `media.shot_date` for rows written before the column existed.

    Scoped to rows that have a creation_date but no shot_date, so it costs one
    indexless scan on an already-migrated library and nothing after the first run.
    Rows whose date is unreadable stay NULL — that is the `unknown` bucket, and
    re-deriving them every startup is exactly the work this column exists to avoid.

    Deliberately does NOT clear a shot_date whose creation_date is now NULL: writes
    go through `upsert`, which keeps the pair consistent, and a repair pass that also
    deletes is a repair pass that can lose data on a partial row.
    """
    rows = conn.execute(
        "SELECT id, creation_date FROM media "
        "WHERE shot_date IS NULL AND creation_date IS NOT NULL"
    ).fetchall()
    updates = [
        (normalise_shot_date(r["creation_date"]), r["id"])
        for r in rows
    ]
    updates = [u for u in updates if u[0] is not None]
    if updates:
        conn.executemany("UPDATE media SET shot_date = ? WHERE id = ?", updates)


def init_db():
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        # Asked BEFORE any CREATE TABLE below, because that is the only moment
        # the answer still exists: once `media` is created we can no longer tell
        # a library that has been in use for a year from one born on this line.
        # Consumed by the provenance stamp further down — see the long comment
        # there for why the distinction decides a published promise.
        library_predates_this_init = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='media'"
        ).fetchone() is not None
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
            # fable-audit round-5 #8: the default was a bogus 'xxh3-128' — arkiv hashes
            # with xxh3 (xxh3_64, offload.DEFAULT_HASH). ingest now writes hash_algo
            # explicitly, so the column default only labels legacy NULL-hash rows.
            ("hash_algo", "TEXT DEFAULT 'xxh3'"),
            ("hash_verified_at", "TEXT"),
            # Vector-index content-freshness (fix: "向量索引靜默過期"). embed_hash =
            # sha256 of the exact text embedded at last index (vectordb.content_hash,
            # over build_doc_text: filename+transcript+frame descriptions/tags), so
            # embed.py re-embeds a row whose DESCRIPTION changed even though its
            # media_id is already in Chroma. embedded_at = ISO ts of that embed.
            # NULL on legacy rows = "unverified" (never rendered as up-to-date), same
            # spirit as the hash_verified_at label above.
            ("embed_hash", "TEXT"),
            ("embedded_at", "TEXT"),
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
            # FX30 editing (D1): persisted per-clip IN/OUT trim points, in seconds.
            # The inspector marks were otherwise UI-ephemeral (lost on clip-switch);
            # persisting them lets the inspector restore a clip's range and lets the
            # multi-clip timeline export assemble a cut list of the marked sub-clips
            # (D2) instead of laying full clips end-to-end. Written ONLY by the
            # dedicated /api/media/{id}/inout endpoint (kept out of _ALLOWED_COLS so a
            # re-ingest/refresh can never clobber a user's marks), same as canonical_tags.
            ("in_point", "REAL"),
            ("out_point", "REAL"),
            # A-cam (multicam): which physical camera a clip is from (camera_id,
            # e.g. "A"/"B") and its framing (angle, e.g. "wide"/"CU"). arkiv already
            # carries camera *identity* (make/model/reel) but not which angle in a
            # multicam shoot — that is editorial, written ONLY by the dedicated
            # /api/media/{id}/camera endpoint and, like in/out, kept OUT of
            # _ALLOWED_COLS so a re-ingest/refresh can never clobber it. This is the
            # data premise for multicam edit decisions (S-cam).
            ("camera_id", "TEXT"),
            ("angle", "TEXT"),
            # The shoot DAY as ISO `YYYY-MM-DD`, derived from creation_date by
            # normalise_shot_date at write time; NULL when that date can't be read.
            # Stored rather than computed per query because SQL cannot reproduce
            # `datetime.strptime` — an earlier attempt to approximate it with GLOB and
            # range checks disagreed with the Python normaliser in BOTH directions
            # (it admitted "2025-10-03Tgarbage" and "2025:10-03", and rejected
            # " 2025-10-03" and "2025-10-03+08:00"), which made the sidebar advertise
            # counts its own filters could not deliver. One parser, run once, at write.
            ("shot_date", "TEXT"),
        ]:
            _add_column_if_missing(conn, "media", col, typ)  # audit L10
        _backfill_shot_date(conn)
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
        # fable-audit round-5 #20: the gallery/list/search paths sorted + filtered on
        # these media columns with ZERO indexes — every page was a full-table scan +
        # temp B-tree sort, and tag name-lookups scanned the whole tags table. These
        # make the common query patterns index-backed on a 100k+ row library.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_processed_at ON media(processed_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_lang ON media(lang)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_rating ON media(rating)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_filename ON media(filename)")
        # covering index for the tag subquery (WHERE name=? → media_id)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_name_media ON tags(name, media_id)")
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
        # ── library provenance ─────────────────────────────────────────────
        # Written once, on the first init that runs this code, and never
        # updated. Its only job is to answer a question the paid tier will ask
        # later: was this library already in use before the free-tier project
        # cap existed?
        #
        # The key is `first_seen_version`, not `created_with_version`, because
        # that is what it truthfully records. A brand-new library gets stamped
        # at creation; an existing library upgrading into a build that carries
        # this code gets stamped on its next init. Both are the earliest
        # version we can prove the library was in use by, and both answer the
        # grandfathering question the same way — but only one of them is
        # literally a creation date, so the key does not claim to be one.
        #
        # The cap does not exist yet — every build in the wild today allows
        # unlimited projects — and the published terms promise that libraries
        # created before it ships keep that permanently. Keeping that promise
        # needs evidence recorded NOW: once the cap ships there is no way to
        # tell, after the fact, whether a two-project library was started under
        # the old terms or the new ones. Behavioural inference ("already has
        # more than three") only catches libraries that had already exceeded
        # the cap, not the ones that would grow into it.
        #
        # INSERT OR IGNORE, not upsert: an upgrade must never restamp an old
        # library with a new version and silently revoke its exemption.
        #
        # A library with no row at all predates this stamp, which is strictly
        # older, so absence reads as exempt. This is a goodwill promise, not
        # DRM — the row is trivially editable and that is fine; the licence is
        # enforced by its terms, not by the database.
        #
        # ── why the stamped VALUE is conditional ──────────────────────────────
        # The paragraph above says absence reads as exempt. This INSERT is what
        # destroys that absence, so what it writes decides whether the promise
        # survives.
        #
        # Stamping `_config.VERSION` unconditionally is only correct while that
        # version predates the cap. That was true for exactly one release: the
        # anchor shipped in 1.0.0 and the cap armed in 1.1.0, one day apart. A
        # user who upgraded straight from 0.12.x to 1.1.0 — i.e. almost anyone,
        # since people upgrade to the newest release, not to whichever one
        # happened to carry the anchor — would have their long-standing library
        # stamped `1.1.0` on first open and silently lose the exemption the
        # product page promises permanently. Reproduced end to end before this
        # change: stamp 1.1.0 -> grandfathered False -> the 4th project and
        # cross-project search both refused, with a message telling them to buy
        # Pro.
        #
        # `entitlements`' latch cannot save them either: init_db runs when the
        # app opens, long before any entitlement question is asked, so there is
        # never a moment where the pre-cap state is observable to be latched.
        #
        # So the stamp records what is actually known. A `media` table that
        # existed BEFORE this init is proof the library was in use under an
        # earlier build — we just cannot say which one, and `pre-anchor` says
        # exactly that instead of inventing a number. `entitlements.parse_version`
        # returns None for it and `predates_cap(None)` is True, per that module's
        # documented rule that every uncertainty resolves in the user's favour.
        #
        # This is the "behavioural inference" the note above rejects, but for a
        # different question: rejected was "does it already hold more than three
        # projects", which only catches libraries that had already exceeded the
        # cap. "Did this library exist at all before this code ran" has no such
        # blind spot.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS library_meta (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO library_meta (key, value) VALUES ('first_seen_version', ?)",
            (PRE_ANCHOR_VERSION if library_predates_this_init else _config.VERSION,),
        )

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



def get_library_origin():
    """Earliest version known to have used this library, or None if it predates the stamp.

    Not a creation date. See `first_seen_version` in `init_db`: an upgraded
    library is stamped on its next init, not retroactively at its real birth.
    What the value guarantees is an upper bound — the library was in use by
    this version at the latest — which is exactly what grandfathering needs.

    None is the older case, not an error: libraries created before
    `library_meta` existed carry no row, and every build that shipped before it
    allowed unlimited projects. Callers deciding whether the free-tier project
    cap applies must therefore treat None as exempt — the same as an explicitly
    pre-cap version. Defaulting the other way would revoke the exemption from
    exactly the users who have held it longest.

    Missing table is handled rather than raised for the same reason: a
    read-only or partially-migrated legacy database must not turn a licensing
    question into a crash.
    """
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value, created_at FROM library_meta WHERE key='first_seen_version'"
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return {"version": row["value"], "created_at": row["created_at"]}


def migrate_to_relative():
    """Convert DB path fields from absolute to relative paths."""
    media_count = 0
    frame_count = 0
    merged_count = 0
    with get_conn() as conn:
        rows = conn.execute("SELECT id, path, thumbnail_path FROM media").fetchall()
        for row in rows:
            # canonical_stored_path (not to_relative) so a pre-fix Windows
            # backslash-relative row is also normalised, not just abs→rel (#9).
            new_path = canonical_stored_path(row["path"]) if row["path"] else row["path"]
            new_thumb = canonical_stored_path(row["thumbnail_path"]) if row["thumbnail_path"] else row["thumbnail_path"]
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
    variants = dedup_path_variants(str(path))  # abs / forward-rel / backslash-rel (#9)
    placeholders = ",".join("?" * len(variants))
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM media WHERE path IN ({0})".format(placeholders),
            variants,
        ).fetchone()
        return row is not None


def find_moved_row(file_hash: str) -> Optional[Dict]:
    """A media row with this content hash whose stored path NO LONGER exists on disk
    — i.e. the file was moved/renamed, not a genuine second copy (fable-audit
    round-5 #8, move-detection semantics). Returned so ingest can re-point the row
    instead of re-transcribing/re-visioning the whole library after a reorg. A hash
    match whose path STILL exists is a real duplicate and is NOT returned (it gets
    ingested as a new row, so the original is never silently abandoned)."""
    if not file_hash:
        return None
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM media WHERE file_hash=?", (file_hash,)).fetchall()
    for r in rows:
        try:
            stored = resolve_path(r["path"])
        except ValueError:
            continue  # a poisoned/escaping legacy path — skip, don't re-point onto it
        if not Path(stored).exists():
            return dict(r)
    return None


def repoint_media_path(media_id: int, new_abs_path: str, _conn=None) -> None:
    """Point an existing row at a new location (the file moved). Preserves the row's
    ratings / tags / transcript / frames — only the path changes (round-5 #8). Stored
    in the same relative form a fresh ingest would use."""
    new_stored = to_relative(new_abs_path)

    def _do(c):
        c.execute("UPDATE media SET path=? WHERE id=?", (new_stored, media_id))
    if _conn is not None:
        _do(_conn)
    else:
        with get_conn() as conn:
            _do(conn)


def set_embed_state(media_id: int, embed_hash: str, embedded_at: str, _conn=None) -> None:
    """Record what was embedded for this media so embed.py can detect a STALE index
    by CONTENT, not just by presence (fix: 向量索引靜默過期). Written only by the
    embed path after a successful upsert — deliberately kept OUT of _ALLOWED_COLS so
    a re-ingest/refresh can't clobber it (same discipline as in_point/canonical_tags)."""
    def _do(c):
        c.execute(
            "UPDATE media SET embed_hash=?, embedded_at=? WHERE id=?",
            (embed_hash, embedded_at, media_id),
        )
    if _conn is not None:
        _do(_conn)
    else:
        with get_conn() as conn:
            _do(conn)


def set_hash_verified(media_id: int, verified_at: str, _conn=None) -> None:
    """Stamp when a media row's file_hash was confirmed against the file's bytes (audit
    2026-07-30: hash_verified_at was declared + allow-listed but had NO writer → NULL for
    every row). The ingest write-path sets it inline via the record upsert (hash_verified_at
    IS in _ALLOWED_COLS); this is the targeted writer for a one-off integrity backfill or a
    future re-verify pass. Mirrors set_embed_state."""
    def _do(c):
        c.execute(
            "UPDATE media SET hash_verified_at=? WHERE id=?",
            (verified_at, media_id),
        )
    if _conn is not None:
        _do(_conn)
    else:
        with get_conn() as conn:
            _do(conn)


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
    # shot_date is DERIVED, never accepted from the caller — it is not in
    # _ALLOWED_COLS, so a record carrying one is ignored above and recomputed here.
    # Deriving it at the single write path is what makes the facet and the three
    # filter branches agree by construction instead of by three matching predicates.
    # Keyed on `creation_date` being present in this write: a partial upsert that
    # doesn't touch the date must not blank the day derived from the last one.
    if "creation_date" in safe:
        safe["shot_date"] = normalise_shot_date(safe["creation_date"])
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
    "camera_make, camera_model, lens_model, reel_name, start_tc, codec, "
    # A-cam: multicam angle annotation, so list/grid views can group by camera.
    "camera_id, angle, "
    # Shoot date, for browsing footage by when it was filmed. Both are needed in the
    # LIGHT shape: `creation_date` is what the inspector displays, and `shot_date` is
    # what the semantic-search branch filters on — that branch filters enriched
    # records rather than in SQL, so a column missing here is a filter that silently
    # does nothing on ?q=, the shape of audits H8/H14. It reads the SAME derived
    # column the SQL branches compare against, which is what keeps the three
    # equivalent rather than merely similar.
    "creation_date, shot_date"
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


# ── Phase 9.8b backfill: retro-convert existing Simplified zh transcripts ─────
# (see retraditionalize.py — write-path 9.8b only converts NEW transcribes)

def iter_zh_media(_conn=None):
    """All zh media rows carrying a non-empty ACTIVE transcript, for the 9.8b
    backfill. Returns id/lang/transcript/segments_json/words_json. Pass _conn to
    read inside the caller's transaction."""
    sql = ("SELECT id, lang, transcript, segments_json, words_json FROM media "
           "WHERE lang LIKE 'zh%' AND transcript IS NOT NULL AND transcript != ''")
    if _conn is not None:
        return [dict(r) for r in _conn.execute(sql).fetchall()]
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def update_media_transcript_fields(media_id, transcript, segments_json, words_json, _conn=None):
    """Overwrite ONLY the ACTIVE transcript text columns on a media row (9.8b
    backfill). Duration / frames / tags / timings are untouched — the caller passes
    already-converted, timing-safe JSON. Pass _conn to join an open transaction."""
    sql = "UPDATE media SET transcript=?, segments_json=?, words_json=? WHERE id=?"
    params = (transcript, segments_json, words_json, media_id)
    if _conn is not None:
        _conn.execute(sql, params)
    else:
        with get_conn() as conn:
            conn.execute(sql, params)


def iter_zh_transcript_archive(_conn=None):
    """All zh rows in the per-language transcript archive (9.7 G2 table), for the
    9.8b backfill. Returns media_id/lang/transcript/segments_json/words_json."""
    sql = ("SELECT media_id, lang, transcript, segments_json, words_json FROM transcripts "
           "WHERE lang LIKE 'zh%'")
    if _conn is not None:
        return [dict(r) for r in _conn.execute(sql).fetchall()]
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


# ── Vision-description backfill (retraditionalize frames.description +
# media.frame_tags — the vision write-path historically never routed through
# zh_convert, so qwen3-vl's Simplified output was stored raw). No lang column on
# frames; the caller gates each description with classify_zh (language-agnostic:
# non-zh text classifies "traditional" and is skipped). ─────

def iter_frames_with_description(_conn=None):
    """All frame rows carrying a non-empty description, for the vision retraditionalize
    backfill. Returns id/media_id/description. Pass _conn to read inside the caller's
    transaction."""
    sql = ("SELECT id, media_id, description FROM frames "
           "WHERE description IS NOT NULL AND description != ''")
    if _conn is not None:
        return [dict(r) for r in _conn.execute(sql).fetchall()]
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def update_frame_description(frame_id, description, _conn=None):
    """Overwrite ONLY a frame's description (vision retraditionalize backfill). Tags /
    quality fields are untouched. Pass _conn to join an open transaction."""
    sql = "UPDATE frames SET description=? WHERE id=?"
    params = (description, frame_id)
    if _conn is not None:
        _conn.execute(sql, params)
    else:
        with get_conn() as conn:
            conn.execute(sql, params)


def iter_frame_tags_media(_conn=None):
    """All media rows carrying a non-empty frame_tags blob (the per-clip JSON rollup of
    frame descriptions/tags that the embed doc reads), for the vision backfill. Returns
    id/frame_tags."""
    sql = ("SELECT id, frame_tags FROM media "
           "WHERE frame_tags IS NOT NULL AND frame_tags != ''")
    if _conn is not None:
        return [dict(r) for r in _conn.execute(sql).fetchall()]
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def update_media_frame_tags(media_id, frame_tags_json, _conn=None):
    """Overwrite ONLY a media row's frame_tags JSON blob (vision retraditionalize
    backfill; the caller passes an already-converted, re-serialized blob). Pass _conn to
    join an open transaction."""
    sql = "UPDATE media SET frame_tags=? WHERE id=?"
    params = (frame_tags_json, media_id)
    if _conn is not None:
        _conn.execute(sql, params)
    else:
        with get_conn() as conn:
            conn.execute(sql, params)


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
        # fable-audit round-5 #27: fold five separate full-table scans into ONE
        # single-pass aggregate. COUNT(col) counts non-NULL values, i.e. it equals
        # COUNT(*) WHERE col IS NOT NULL — so with_transcript / with_thumb come free.
        agg = conn.execute(
            "SELECT COUNT(*) AS total, "
            "COUNT(transcript) AS with_transcript, "
            "COUNT(thumbnail_path) AS with_thumb, "
            "COALESCE(SUM(duration_s), 0) AS total_duration, "
            "COALESCE(SUM(size_mb), 0) AS total_size "
            "FROM media"
        ).fetchone()
        langs = conn.execute(
            "SELECT lang, COUNT(*) as cnt FROM media "
            "WHERE lang IS NOT NULL GROUP BY lang ORDER BY cnt DESC"
        ).fetchall()
        return {
            "total": agg["total"],
            "with_transcript": agg["with_transcript"],
            "with_thumb": agg["with_thumb"],
            "total_duration_s": agg["total_duration"],
            "total_size_mb": agg["total_size"],
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


def delete_media(media_id: int, _conn=None):
    """Delete one media row; its frames / tags / transcripts drop via ON DELETE
    CASCADE (get_conn sets PRAGMA foreign_keys=ON). Returns the media + frame
    thumbnail_path values (so the caller can unlink the .jpg files), or None if no
    such media. Chroma vectors are the caller's job (vectordb.delete_media) — this
    layer has no vector-store dependency. Used by the sample-library 'remove' flow
    (routers/sample.py); the UI has no general delete-media action today."""
    def _do(c):
        row = c.execute("SELECT id FROM media WHERE id = ?", (media_id,)).fetchone()
        if row is None:
            return None
        thumbs = []
        m = c.execute("SELECT thumbnail_path FROM media WHERE id = ?", (media_id,)).fetchone()
        if m and m["thumbnail_path"]:
            thumbs.append(m["thumbnail_path"])
        for fr in c.execute(
            "SELECT thumbnail_path FROM frames WHERE media_id = ? AND thumbnail_path IS NOT NULL",
            (media_id,),
        ).fetchall():
            thumbs.append(fr["thumbnail_path"])
        c.execute("DELETE FROM media WHERE id = ?", (media_id,))  # cascades children
        return thumbs
    if _conn is not None:
        return _do(_conn)
    with get_conn() as conn:
        return _do(conn)


# ── Enhanced Queries (Phase 4 UI) ─────────────────────────────────────────────

# `media.creation_date` is the SHOOT date and it is stored raw, in whatever shape
# the ingest path produced — `ingest.exiftool_extract` does `str(cdate)` with no
# normalisation, and there are two writers:
#
#   exiftool (EXIF CreateDate)        -> "2025:10:03 13:56:20"     colon-separated
#   XAVC NRT sidecar (parse_xavc_...) -> "2025-10-03T13:56:20+08:00"  ISO 8601
#
# Both happen to start with YYYY, which is why grouping by year via substr LOOKS like
# it works on either. Ordering does not: ':' (0x3A) sorts above '-' (0x2D), so a
# library holding both shapes sorts wrong under a plain string comparison.
#
# Nothing reads this column to answer a date question any more. `normalise_shot_date`
# runs once at write and its output is stored in `media.shot_date`; the facet groups
# on that column and all three filter branches compare against it. The raw text is
# kept only for display. Approximating the parser in SQL was tried and abandoned —
# see `shot_window_clause` for the seven values that broke it.
_SHOT_DATE_FORMATS = (
    "%Y:%m:%d %H:%M:%S",   # exiftool default
    "%Y-%m-%dT%H:%M:%S",   # ISO, timezone stripped by the caller
    "%Y-%m-%d %H:%M:%S",
    "%Y:%m:%d",
    "%Y-%m-%d",
)


def normalise_shot_date(raw) -> Optional[str]:
    """Raw `creation_date` -> ISO date `YYYY-MM-DD`, or None if it isn't a date.

    Returning None (rather than a guess, or the raw string) is deliberate: a clip
    whose shoot date can't be read must land in an explicit "unknown" bucket, not be
    silently dropped from the facet or filed under a plausible-looking year.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Drop a trailing timezone offset / Z — the shoot DAY is what a date facet is
    # about, and re-basing to UTC would move footage shot near midnight into the
    # neighbouring day, which is exactly the grouping a DIT would call wrong.
    text = re.sub(r"(?:Z|[+-]\d{2}:?\d{2})$", "", text).strip()
    # Sub-second precision appears on some sidecars.
    text = re.sub(r"\.\d+$", "", text)
    for fmt in _SHOT_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def shot_year(raw) -> Optional[str]:
    """The 4-digit shoot year, or None when it can't be established."""
    iso = normalise_shot_date(raw)
    return iso[:4] if iso else None


# The sidebar bucket for clips with no readable shoot date. Named rather than a bare
# string so the filter and the facet can't drift apart on a typo.
UNKNOWN_SHOT_YEAR = "unknown"

# One definition of "this row carries a readable shoot date", so that the three
# selectable states — a year, a day, and `unknown` — are a partition of the library
# rather than three independently-written predicates that happen to overlap.
#
# The GLOB accepts either separator because the two writers disagree (exiftool
# "2025:10:03 13:56:20", XAVC sidecar "2025-10-03T13:56:20+08:00"), and the length
# test requires the date to actually END at character 10. Without that test a value
# like "2025-10-03XGARBAGE" passes the year and day filters while
# normalise_shot_date rejects it and the facet files it under `unknown` — filter and
# facet then disagree about the same row, which is the one property this feature was
# built on.
#
def shot_window_clause(
    shot_year: Optional[str] = None,
    shot_date: Optional[str] = None,
) -> Tuple[str, List]:
    """SQL for "shot in this year / on this day", shared by every caller.

    Reads the derived `shot_date` column, never the raw `creation_date`. That is the
    whole design: `normalise_shot_date` runs once, at write, and every reader compares
    its output. An earlier version of this function approximated the parser in SQL —
    GLOB for the shape, range checks for the components — and a Codex audit produced
    seven values where it disagreed with the real parser in both directions:

        admitted but unreadable:  "2025:10-03"  "2025-10-03Tgarbage"
                                  "2025-10-03 99:99:99"  "2025-10-03T13:56:20."
        readable but rejected:    " 2025-10-03"  "2025-10-03+08:00"

    Each one made the sidebar advertise a count its own filter could not deliver.
    SQLite cannot reproduce `datetime.strptime`, and the set of near-misses has no
    edge to chase to — so there is one parser now, not two.

    Two SQL sites use this — `_build_filter_clause` for the list query and the
    degraded text search in `routers.media` — and they used to hand-write the
    predicate separately, which is how audits H8 and H14 happened.

    The arguments compose with AND rather than one overriding the other, so a
    contradictory request (year 2025 *and* undated) returns nothing instead of
    silently answering a question that wasn't asked.

    Returns ("", []) when neither is set, so callers can concatenate unconditionally.
    """
    clauses: List[str] = []
    params: List = []
    if shot_year == UNKNOWN_SHOT_YEAR:
        # "Show me the ones with no usable shoot date" has to be reachable, or those
        # clips are invisible from the sidebar the moment any year is selected.
        clauses.append("shot_date IS NULL")
    elif shot_year:
        clauses.append("substr(shot_date,1,4) = ?")
        params.append(str(shot_year))
    if shot_date == UNKNOWN_SHOT_YEAR:
        clauses.append("shot_date IS NULL")
    elif shot_date:
        clauses.append("shot_date = ?")
        params.append(str(shot_date))
    if not clauses:
        return "", []
    return " AND ".join(clauses), params


def _build_filter_clause(
    min_duration: float = 0,
    max_duration: float = 99999,
    lang: Optional[str] = None,
    rating: Optional[str] = None,
    media_type: Optional[str] = None,
    shot_year: Optional[str] = None,
    shot_date: Optional[str] = None,
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
        # R5-24: sourced from the shared set so .insv/.360 are included and this
        # filter can never drift from the rest of the codebase again.
        clauses.append("ext IN " + mediatypes.sql_in_literal(mediatypes.VIDEO_EXT))
    elif media_type == "audio":
        clauses.append("ext IN " + mediatypes.sql_in_literal(mediatypes.AUDIO_EXT))
    shot_sql, shot_params = shot_window_clause(shot_year=shot_year, shot_date=shot_date)
    if shot_sql:
        clauses.append(shot_sql)
        params.extend(shot_params)
    return " AND ".join(clauses), params


def get_shoot_date_facets() -> dict:
    """Year buckets — each with its shoot DAYS — for the sidebar's time entry point.

    Grouped on the derived `shot_date` column — the same column the filters compare
    against — so a bucket and the query behind it cannot disagree about which rows
    belong to it. That is not a stylistic preference: when the facet counted in Python
    and the filters approximated the parser in SQL, seven ordinary values landed in
    different buckets on the two sides.

    Days are inlined rather than served from a second lazy endpoint. This function
    already reads the whole column, so grouping it costs nothing extra, and the number
    of distinct shoot days is bounded by the number of clips. A year alone is too
    coarse to be the answer: a documentary shot across one season collapses into a
    single bucket, which is the same "one bucket answers nothing" failure that ruled
    `processed_at` out of this facet in the first place.

    Both lists are newest-first, and the client is expected not to re-sort. Note this
    is only orderable BECAUSE the values are normalised: the raw column cannot be
    sorted lexically at all, since ':' (0x3A) sorts above '-' (0x2D).
    """
    with get_conn() as conn:
        rows = conn.execute("SELECT shot_date FROM media").fetchall()
    days_by_year: Dict[str, Dict[str, int]] = {}
    counts: Dict[str, int] = {}
    unknown = 0
    for row in rows:
        iso = row["shot_date"]
        if iso is None:
            unknown += 1
            continue
        year = iso[:4]
        counts[year] = counts.get(year, 0) + 1
        per_day = days_by_year.setdefault(year, {})
        per_day[iso] = per_day.get(iso, 0) + 1
    return {
        "years": [
            {
                "year": y,
                "count": counts[y],
                "days": [
                    {"date": d, "count": days_by_year[y][d]}
                    for d in sorted(days_by_year[y], reverse=True)
                ],
            }
            for y in sorted(counts, reverse=True)
        ],
        "unknown": unknown,
        "total": len(rows),
    }


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
