"""Unified media delete (Phase 14.5).

Single source of truth for removing a media record AND its on-disk artefacts:

- SQLite row (cascades frames/tags/transcripts via db.delete_media)
- Chroma vectors (vectordb.delete_media)
- derivatives: thumbnails, proxy, waveform cache
- the original source file: moved into the recycle bin (.arkiv/trash) instead of
  unlinked, so a mistaken delete is recoverable for ARKIV_TRASH_TTL_DAYS days.

External paths (offload cold storage / absolute paths outside PROJECT_ROOT /
federated libraries) are metadata-only: we refuse to physically remove a file
arkiv does not own, and record a warning in the audit log.

Reused by:
- DELETE /api/media/{id} and POST /api/media/bulk-delete (routers/media.py)
- POST /api/media/prune-missing (routers/media.py) with allow_file_delete=False
- routers/sample.py remove (so sample cleanup also nukes proxies/waveforms)
"""
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import config
import db


def _within_allowed_roots(resolved: str) -> bool:
    """True if `resolved` lives under PROJECT_ROOT or one of ARKIV_MEDIA_ROOTS.

    Uses a separator-joined prefix check rather than Path.is_relative_to so the
    behaviour is identical on 3.9 across platforms (is_relative_to is 3.9 but has
    edge cases); the trailing os.sep guards against /Volumes/foo being treated as
    a parent of /Volumes/foobar."""
    resolved = Path(resolved).resolve()
    roots = [Path(config.PROJECT_ROOT).resolve()]
    roots += [Path(r).resolve() for r in config.MEDIA_ROOTS]
    s = str(resolved)
    for r in roots:
        rs = str(r)
        if s == rs or s.startswith(rs + os.sep):
            return True
    return False


def _unlink_rel(stored_path: str) -> None:
    """Unlink a PROJECT_ROOT-relative (or absolute) stored path, best-effort."""
    try:
        p = Path(stored_path)
        target = p if p.is_absolute() else (config.PROJECT_ROOT / p)
        target.unlink(missing_ok=True)
    except Exception:
        pass


def _join_warning(existing, extra):
    """Two things can go wrong in one delete (the file move AND the bins cleanup)
    and `warning` is a single slot. Keep both rather than letting the later one
    overwrite the earlier."""
    if not existing:
        return extra
    if not extra:
        return existing
    return "{0}; {1}".format(existing, extra)


def _audit(media_id, filename, file_deleted, warning, token_info):
    try:
        actor = "unknown"
        if isinstance(token_info, dict):
            actor = token_info.get("name") or token_info.get("id") or "unknown"
        log_dir = config.PROJECT_ROOT / ".arkiv" / "audit"
        log_dir.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": "media_delete",
            "media_id": media_id,
            "filename": filename,
            "file_deleted": file_deleted,
            "warning": warning,
            "actor": actor,
        }
        with (log_dir / "delete.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


def delete_media_full(media_id, allow_file_delete=True, token_info=None):
    """Delete one media record and all its artefacts.

    Returns a result dict, or None if the media row does not exist (so the
    caller can answer 404). `allow_file_delete=False` skips the physical move
    (used by prune-missing where the file is already gone)."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, path, filename FROM media WHERE id=?", (media_id,)
        ).fetchone()
    if row is None:
        return None

    path = row["path"]
    filename = row["filename"] or ""

    thumbs = db.delete_media(media_id) or []

    resolved = ""
    try:
        if path:
            resolved = db.resolve_path(path)
    except ValueError:
        resolved = ""

    file_deleted = False
    warning = None
    trash_path = ""
    if resolved and allow_file_delete:
        if _within_allowed_roots(resolved):
            try:
                config.TRASH_DIR.mkdir(parents=True, exist_ok=True)
                stem = "%d__%s" % (media_id, filename or Path(resolved).name)
                dest = config.TRASH_DIR / stem
                if dest.exists():
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    dest = config.TRASH_DIR / ("%s__%s" % (stamp, stem))
                shutil.move(str(resolved), str(dest))
                trash_path = str(dest)
                file_deleted = True
            except Exception:
                warning = "file move to trash failed; metadata removed"
        else:
            warning = "external path outside allowed roots: metadata-only, original kept"

    # derivatives (best-effort, run even when the original was already gone)
    for rel in thumbs:
        _unlink_rel(rel)
    if resolved:
        try:
            config.proxy_path_for(media_id, str(resolved)).unlink(missing_ok=True)
        except Exception:
            pass
    try:
        for f in config.WAVEFORMS_DIR.glob("%d_*" % media_id):
            f.unlink(missing_ok=True)
    except Exception:
        pass

    # vectors
    try:
        import vectordb
        col = vectordb.get_collection()
        if col is not None:
            vectordb.delete_media(col, media_id)
    except Exception:
        pass

    # Cross-library 精選集 hold clips by (registry name, media_id). That pair is
    # gone the moment the row is: restoring from the recycle bin re-INGESTS the
    # file and mints a new id, so the old entry can never resolve again.
    #
    # 🔴 And it does not merely sit there. `media.id` is INTEGER PRIMARY KEY with
    # no AUTOINCREMENT, so SQLite hands this id to the next clip ingested. The
    # stale entry then resolves — with status ok — to footage the user never put
    # in that bin, and 精選集 feeds the cross-project copy. So this cleanup is
    # not cosmetic, and a silent failure here is not a papercut.
    #
    # Still best-effort on the delete's own outcome: the row, files and vectors
    # are already gone, and reporting failure for work that actually happened
    # helps nobody. But the failure has to be VISIBLE — it goes into `warning`,
    # which reaches both the API response and the audit log.
    #
    # Narrow on purpose: only the bins call is guarded. An import error or a
    # broken registry lookup is a bug in arkiv, not a runtime condition, and the
    # previous blanket try/except turned exactly that into a silent no-op.
    import bins as bins_store
    import projects as project_registry

    registry_name = project_registry.current_registry_name()
    if registry_name:
        try:
            bins_store.remove_media_everywhere(registry_name, media_id)
        except Exception as exc:
            warning = _join_warning(
                warning,
                "精選集未能清除此素材（{0}）：該編號會被下一支匯入的素材重用".format(
                    type(exc).__name__),
            )

    # Only record a recycle-bin entry when an actual file was moved (it is the
    # only thing recoverable). Metadata-only deletes are still captured by the
    # audit log below.
    if trash_path:
        db.trash_media(media_id, filename, path or "", trash_path, config.TRASH_TTL_DAYS)
    _audit(media_id, filename, file_deleted, warning, token_info)

    return {
        "ok": True,
        "media_id": media_id,
        "file_deleted": file_deleted,
        "warning": warning,
    }
