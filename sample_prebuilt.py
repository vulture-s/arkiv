"""Pre-built sample-library loader / remover (A1 · launch Wave-0 fast-follow).

`seed_sample.py` (v1) re-ingests the four bundled CC-BY clips ON DEMAND — minutes of
whisper + vision and it needs Ollama + models pulled, so it never actually covered
the cold-start / missing-deps first impression it was meant to hide. This loads the
PRE-INDEXED artifact instead (`sample/prebuilt/arkiv-sample.tar`, built by
`scripts/build_sample_prebuilt.py`): the corpus (rows + frame descriptions +
transcripts + thumbnails + Chroma vectors) is baked, so first run is instantly
browsable / tag- / lexical-searchable with ZERO pipeline; semantic search needs only
Ollama to embed the query (the corpus side is already vectorised).

Direction A1 (Hevin 2026-07-17): arkiv has one active browsable project (= boot
PROJECT_ROOT) + read-only federated search — there is no browsable "separate demo
project". So the sample is seeded INTO the project, but only when it is FRESH
(0 media): that is the first-run case, and it means no id-remap, no clobber, and no
pollution of a configured footage root (a technical user who set ARKIV_PROJECT_ROOT
at their own footage keeps a non-empty / distinct project → auto-seed never fires).
A marker records the seeded media ids so "remove sample" is a clean one-click; a
dismiss flag stops it coming back on the next restart.

Store layout (all stored paths are relative to PROJECT_ROOT, verified at build):
    <root>/clips/<name>.mp4                 media.path = clips/<name>.mp4
    <root>/.arkiv/thumbnails/<stem>*.jpg    thumbnail_path = .arkiv/thumbnails/...
    <root>/.arkiv/chroma_db/                baked query-able vectors
The four content tables (media/frames/tags/transcripts) are ATTACH-merged into the
live DB rather than replacing the file — so we never yank a live WAL database out
from under an open connection.
"""
import json
import shutil
import sqlite3
import tarfile
from pathlib import Path

import config
import db

# The four tables the sample corpus lives in. NOT settings/jobs/chat/tokens — those
# stay the user's. media must go first (frames/tags/transcripts FK it) and reversed
# on nothing (we only ever INSERT into an empty project).
_CONTENT_TABLES = ("media", "frames", "tags", "transcripts")

_ARTIFACT = config.BASE_DIR / "sample" / "prebuilt" / "arkiv-sample.tar"
_CLIPS_SRC = config.BASE_DIR / "sample" / "clips"


def _arkiv_dir() -> Path:
    return config.PROJECT_ROOT / ".arkiv"


def _loaded_marker() -> Path:
    return _arkiv_dir() / ".sample.json"


def _dismissed_marker() -> Path:
    return _arkiv_dir() / ".sample-dismissed"


def artifact_available() -> bool:
    return _ARTIFACT.exists()


def is_loaded() -> bool:
    return _loaded_marker().exists()


def is_dismissed() -> bool:
    return _dismissed_marker().exists()


def _media_count() -> int:
    try:
        with db.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    except Exception:  # noqa: BLE001 — a missing/locked DB counts as "unknown", caller decides
        return -1


def status() -> dict:
    """Shape the in-app 'Sample data' chip reads: is the artifact bundled, is it
    loaded right now, and which media ids are the sample (so the UI can badge them)."""
    ids = []
    if is_loaded():
        try:
            ids = json.loads(_loaded_marker().read_text()).get("media_ids", [])
        except Exception:  # noqa: BLE001
            ids = []
    return {
        "available": artifact_available(),
        "loaded": is_loaded(),
        "dismissed": is_dismissed(),
        "media_ids": ids,
    }


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract every member under `dest`, refusing any that would escape it (no
    absolute paths, no '..' traversal). Our own artifact is trusted, but a tar
    extract is a traversal footgun and the guard is cheap + 3.9-portable (the
    stdlib `filter='data'` only lands mid-3.12)."""
    dest = dest.resolve()
    for member in tar.getmembers():
        # No links in our own artifact (only project.db / chroma_db / thumbnails) —
        # reject any, so the pre-3.12 fallback path can't create an escaping symlink
        # that a later member writes through.
        if member.issym() or member.islnk():
            raise ValueError("unexpected link member in sample artifact: {0}".format(member.name))
        target = (dest / member.name).resolve()
        if dest != target and dest not in target.parents:
            raise ValueError("unsafe path in sample artifact: {0}".format(member.name))
    try:
        tar.extractall(dest, filter="data")  # 3.12+ defense-in-depth (the guard above is the real one)
    except TypeError:
        tar.extractall(dest)  # 3.9/3.11: `filter` kwarg absent — validated members above


def _merge_content(src_db: Path) -> list:
    """ATTACH the pre-built db and copy the four content tables into the live project
    db in one transaction. Returns the SAMPLE media ids read from the prebuilt db
    ITSELF — never from the merged live table — so a caller that force-loads onto a
    library holding the user's own footage can never later delete the user's rows
    (remove_sample only ever touches these ids). Inserts an explicit column
    intersection (not `SELECT *`) so a future migration that adds a column can't
    misalign values by position; a column present on only one side is simply skipped
    (new live col → its default), never silently shifted."""
    with db.get_conn() as conn:
        conn.execute("ATTACH DATABASE ? AS prebuilt", (str(src_db),))
        try:
            for table in _CONTENT_TABLES:
                # _CONTENT_TABLES are trusted constants — safe to interpolate.
                live_cols = [r[1] for r in conn.execute("PRAGMA table_info({0})".format(table))]
                pre_cols = {r[1] for r in conn.execute("PRAGMA prebuilt.table_info({0})".format(table))}
                cols = [c for c in live_cols if c in pre_cols]  # order from live → INSERT/SELECT align
                collist = ", ".join('"{0}"'.format(c) for c in cols)
                conn.execute(
                    "INSERT INTO {0} ({1}) SELECT {1} FROM prebuilt.{0}".format(table, collist)
                )
            ids = [r[0] for r in conn.execute("SELECT id FROM prebuilt.media ORDER BY id").fetchall()]
            # DETACH refuses to run inside the open write transaction ("database is
            # locked") — commit the inserts first, then detach outside it.
            conn.commit()
        finally:
            # own try: a DETACH failure must not mask a real INSERT error above.
            try:
                conn.execute("DETACH DATABASE prebuilt")
            except Exception:  # noqa: BLE001
                pass
        return ids


def load_prebuilt(force: bool = False) -> dict:
    """Seed the pre-indexed sample into the CURRENT project. Refuses a non-fresh
    project (media_count > 0) unless force — the caller (on-demand CTA) should fall
    back to the re-ingest seed there. Idempotent: a no-op when already loaded."""
    if not artifact_available():
        return {"ok": False, "reason": "artifact-missing"}
    if is_loaded() and not force:
        return {"ok": True, "already": True, **status()}
    count = _media_count()
    if count < 0 and not force:
        return {"ok": False, "reason": "db-unknown"}  # unreadable DB → never seed blind
    if count > 0 and not force:
        return {"ok": False, "reason": "project-not-fresh", "media": count}

    arkiv = _arkiv_dir()
    arkiv.mkdir(parents=True, exist_ok=True)
    staging = arkiv / ".sample-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        with tarfile.open(_ARTIFACT) as tar:
            _safe_extract(tar, staging)

        # 1) content rows → live DB (ATTACH-merge, no live-file replacement)
        media_ids = _merge_content(staging / "project.db")

        # Write the marker the instant the rows exist — BEFORE the copies below — so a
        # mid-copy failure (disk full / permission) still leaves exactly these ids
        # removable instead of un-cleanable orphan rows. Rewritten with clip names at
        # the end. An explicit (re)load also clears any prior dismiss flag.
        _loaded_marker().write_text(json.dumps({"media_ids": media_ids, "clips": []}, ensure_ascii=False))
        _dismissed_marker().unlink(missing_ok=True)

        # 2) baked vectors + thumbnails → the config-resolved store dirs (respects
        #    any ARKIV_CHROMA_PATH / ARKIV_THUMBNAILS_DIR override). Fresh project →
        #    these dirs are absent/empty, so a clean replace is correct.
        if (staging / "chroma_db").exists():
            if config.CHROMA_PATH.exists():
                shutil.rmtree(config.CHROMA_PATH)
            shutil.copytree(staging / "chroma_db", config.CHROMA_PATH)
        if (staging / "thumbnails").exists():
            config.THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
            for thumb in (staging / "thumbnails").glob("*"):
                if thumb.is_file():
                    shutil.copy2(thumb, config.THUMBNAILS_DIR / thumb.name)

        # 3) the clips themselves → <root>/clips (media.path = clips/<name>.mp4)
        clips_dst = config.PROJECT_ROOT / "clips"
        clips_dst.mkdir(parents=True, exist_ok=True)
        clip_names = []
        for clip in sorted(_CLIPS_SRC.glob("*.mp4")):
            shutil.copy2(clip, clips_dst / clip.name)
            clip_names.append(clip.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    # Rewrite the marker with the clip names now that they're on disk (removability
    # of the rows was already guaranteed by the early write above).
    _loaded_marker().write_text(
        json.dumps({"media_ids": media_ids, "clips": clip_names}, ensure_ascii=False)
    )
    return {"ok": True, "media_ids": media_ids, "clips": clip_names}


def remove_sample() -> dict:
    """One-click remove: delete exactly the seeded media (rows cascade; vectors +
    thumbnail files + copied clips cleaned best-effort), then set the dismiss flag so
    auto-seed does not resurrect it next restart. Only touches the marker's ids — a
    user's own footage ingested alongside the sample is untouched."""
    if not is_loaded():
        return {"ok": True, "removed": 0, "already": True}
    try:
        marker = json.loads(_loaded_marker().read_text())
    except Exception:  # noqa: BLE001
        marker = {}
    media_ids = marker.get("media_ids", [])
    clips = marker.get("clips", [])

    col = None
    try:
        import vectordb
        col = vectordb.get_collection()
    except Exception:  # noqa: BLE001 — chroma may be absent; DB delete still proceeds
        col = None

    removed = 0
    for mid in media_ids:
        thumbs = db.delete_media(mid)
        if thumbs is None:
            continue
        removed += 1
        for rel in thumbs:
            _unlink_rel(rel)
        if col is not None:
            try:
                vectordb.delete_media(col, mid)
            except Exception:  # noqa: BLE001
                pass
    for name in clips:
        _unlink_rel(str(Path("clips") / name))

    _loaded_marker().unlink(missing_ok=True)
    _dismissed_marker().write_text("")
    return {"ok": True, "removed": removed}


def _unlink_rel(stored_path: str) -> None:
    """Unlink a PROJECT_ROOT-relative (or absolute) stored path, best-effort."""
    try:
        p = Path(stored_path)
        target = p if p.is_absolute() else (config.PROJECT_ROOT / p)
        target.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def maybe_autoseed() -> dict:
    """Called once from the app lifespan (after db.init_db). Auto-load the sample
    only for a genuinely fresh, never-dismissed project — so a returning user or one
    who pointed arkiv at their own footage is never surprised. Best-effort: any
    failure is swallowed so a sample glitch can never block app startup."""
    try:
        # Only ever seed a real, distinct project root — never the install/repo dir.
        # PROJECT_ROOT falls back to BASE_DIR under bare uvicorn AND in the test
        # suite (neither sets ARKIV_PROJECT_ROOT), so this one guard keeps auto-seed
        # from polluting the working tree there while still firing for the .app /
        # any explicitly-configured project.
        if config.PROJECT_ROOT.resolve() == config.BASE_DIR.resolve():
            return {"seeded": False, "reason": "unconfigured-root"}
        if not artifact_available() or is_loaded() or is_dismissed():
            return {"seeded": False, "reason": "skip"}
        if _media_count() != 0:
            return {"seeded": False, "reason": "not-fresh"}
        res = load_prebuilt()
        return {"seeded": bool(res.get("ok")), "detail": res}
    except Exception as exc:  # noqa: BLE001
        return {"seeded": False, "reason": "error: {0}".format(type(exc).__name__)}
