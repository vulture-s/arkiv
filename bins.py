"""Cross-library 精選集 (curated bins) — persistent, named selections of clips that
live scattered across MULTIPLE registered projects.

A bin references clips *by identity* — the (project_name, media_id) pair that
survives federation's Phase-16.2 path sanitization — and never moves or mutates
the source library (cross-library is forever read-only, W2.2 spec §0.3). The
payoff action "copy selected clips into a new project" lives in server.py; this
module owns the persistence + a per-item reachability gate.

Storage mirrors the project registry (projects.py): a versioned, lock-guarded,
atomically-written JSON that lives OUTSIDE any single project's .arkiv (a bin
spans projects, so it can't belong to one project.db). Default
~/.arkiv-bins.json, overridable via ARKIV_BINS_PATH.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BINS_VERSION = 1

# Serializes bins read-modify-write so two concurrent add/remove calls (FastAPI
# runs sync handlers in a threadpool) can't lose-update each other — same reason
# projects.py serializes the registry.
_BINS_LOCK = threading.Lock()


class BinsError(Exception):
    pass


class BinEntitlementError(BinsError):
    """A bin operation refused because it needs the Pro add-on.

    Subclasses BinsError so every existing `except BinsError` keeps working —
    but is its own type so the API layer can answer 403 with a machine-readable
    code instead of folding a licensing refusal into a generic 400. A caller
    that cannot tell "you may not" from "you asked wrong" cannot show the user
    the one thing that would help: what to do next.
    """

    def __init__(self, message, code="cross_project"):
        super(BinEntitlementError, self).__init__(message)
        self.code = code


# Per-item reachability, surfaced in GET /api/bins/{id} and gated before copy.
# OK plus the HealthStatus values (passed through) plus three bin-specific ones.
STATUS_OK = "ok"
STATUS_PROJECT_UNREGISTERED = "project_unregistered"  # project_name no longer in the registry
STATUS_ROW_MISSING = "row_missing"                    # clip deleted from the source project.db
STATUS_FILE_MISSING = "file_missing"                  # source file gone from disk
STATUS_ERROR = "error"                                # unexpected failure probing the source


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_bins_path() -> Path:
    return Path(
        os.getenv("ARKIV_BINS_PATH", str(Path.home() / ".arkiv-bins.json"))
    ).expanduser().resolve(strict=False)


def _item_key(project_name: str, media_id: Any) -> tuple:
    # Dedup key inside a bin. media_id is per-project auto-increment, so it must
    # be qualified by the project name (federation's cross-project identity).
    return (str(project_name), str(media_id))


@dataclass
class BinItem(object):
    project_name: str
    media_id: str
    filename: str = ""
    added_at: Optional[str] = None

    @classmethod
    def from_mapping(cls, data: Dict[str, Any]) -> "BinItem":
        if not isinstance(data, dict):
            raise BinsError("bin item must be an object")
        project_name = data.get("project_name")
        media_id = data.get("media_id")
        if not project_name or media_id in (None, ""):
            raise BinsError("bin item missing project_name or media_id")
        return cls(
            project_name=str(project_name),
            media_id=str(media_id),
            filename=str(data.get("filename") or ""),
            added_at=data.get("added_at"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "media_id": self.media_id,
            "filename": self.filename,
            "added_at": self.added_at,
        }

    def key(self) -> tuple:
        return _item_key(self.project_name, self.media_id)


@dataclass
class Bin(object):
    id: str
    name: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    items: List[BinItem] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: Dict[str, Any]) -> "Bin":
        if not isinstance(data, dict):
            raise BinsError("bin entry must be an object")
        bin_id = data.get("id")
        name = data.get("name")
        if not bin_id or not name:
            raise BinsError("bin entry missing id or name")
        raw_items = data.get("items") or []
        if not isinstance(raw_items, list):
            raise BinsError("bin items must be a list")
        return cls(
            id=str(bin_id),
            name=str(name),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            items=[BinItem.from_mapping(item) for item in raw_items],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "items": [item.to_dict() for item in self.items],
        }

    def summary(self) -> Dict[str, Any]:
        # List view — no items payload (cheap; the detail view resolves statuses).
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "item_count": len(self.items),
        }


def load_bins() -> Dict[str, Any]:
    path = _default_bins_path()
    if not path.exists():
        data = {"version": BINS_VERSION, "bins": []}
        save_bins(data)
        return data
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError as exc:
        raise BinsError("bins JSON is corrupt") from exc
    if not isinstance(data, dict):
        raise BinsError("bins root must be an object")
    bins = data.get("bins", [])
    if not isinstance(bins, list):
        raise BinsError("bins must be a list")
    version = data.get("version", BINS_VERSION)
    if version != BINS_VERSION:
        raise BinsError("unsupported bins version")
    data["version"] = version
    data["bins"] = bins
    return data


def save_bins(data: Dict[str, Any]) -> None:
    path = _default_bins_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": int(data.get("version", BINS_VERSION)),
        "bins": data.get("bins", []),
    }
    # Unique tmp name per writer + atomic os.replace — see projects.save_registry
    # for why a shared "<file>.tmp" corrupts under concurrent writers.
    tmp_path = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _load_bin_objects() -> List[Bin]:
    return [Bin.from_mapping(raw) for raw in load_bins().get("bins", [])]


def _find(bins: Iterable[Bin], bin_id: str) -> Optional[Bin]:
    for b in bins:
        if b.id == bin_id:
            return b
    return None


def _persist(bins: List[Bin]) -> None:
    data = load_bins()
    data["bins"] = [b.to_dict() for b in bins]
    save_bins(data)


def list_bins() -> List[Bin]:
    return _load_bin_objects()


def get_bin(bin_id: str) -> Bin:
    b = _find(_load_bin_objects(), bin_id)
    if b is None:
        raise BinsError("bin not found: {0}".format(bin_id))
    return b


def create_bin(name: str) -> Bin:
    name = (name or "").strip()
    if not name:
        raise BinsError("bin name required")
    with _BINS_LOCK:
        bins = _load_bin_objects()
        now = _now_iso()
        new = Bin(id=uuid.uuid4().hex[:12], name=name, created_at=now, updated_at=now, items=[])
        bins.append(new)
        _persist(bins)
        return new


def rename_bin(bin_id: str, name: str) -> Bin:
    name = (name or "").strip()
    if not name:
        raise BinsError("bin name required")
    with _BINS_LOCK:
        bins = _load_bin_objects()
        target = _find(bins, bin_id)
        if target is None:
            raise BinsError("bin not found: {0}".format(bin_id))
        target.name = name
        target.updated_at = _now_iso()
        _persist(bins)
        return target


def delete_bin(bin_id: str) -> Bin:
    with _BINS_LOCK:
        bins = _load_bin_objects()
        target = _find(bins, bin_id)
        if target is None:
            raise BinsError("bin not found: {0}".format(bin_id))
        _persist([b for b in bins if b.id != bin_id])
        return target


def add_items(bin_id: str, items: List[Dict[str, Any]]) -> Bin:
    """Add items (dedup by (project_name, media_id), preserving add order). Accepts
    a list of {project_name, media_id, filename?}."""
    with _BINS_LOCK:
        bins = _load_bin_objects()
        target = _find(bins, bin_id)
        if target is None:
            raise BinsError("bin not found: {0}".format(bin_id))
        existing = {item.key() for item in target.items}
        now = _now_iso()

        # A bin holding items from more than one project IS the "collections
        # spanning projects" half of the Pro cross-project feature
        # (docs/pro-addon-license.md). Gate the operation that INTRODUCES a new
        # project into a bin — not the bin's existing contents.
        #
        # That distinction is the whole design. Refusing to touch a bin that is
        # already cross-project would retroactively break data the user already
        # has, which is the one thing the grandfather promise exists to prevent;
        # and it would punish exactly the pre-cap installs the promise protects.
        # So adding items from a project the bin already contains stays allowed
        # even on the free tier, while widening the bin to a NEW project is what
        # requires entitlement.
        parsed = [BinItem.from_mapping(raw) for raw in items or []]
        incoming_projects = {
            item.project_name for item in parsed if item.key() not in existing
        }
        existing_projects = {item.project_name for item in target.items}
        if incoming_projects and not incoming_projects.issubset(existing_projects):
            if len(existing_projects | incoming_projects) > 1:
                # Lazy, matching bin_item_status above: pure-CRUD callers and
                # tests should not pay for the registry/health import chain.
                import entitlements
                from projects import known_project_dbs

                verdict = entitlements.check_cross_project(
                    db_paths=known_project_dbs()
                )
                if not verdict.allowed:
                    raise BinEntitlementError(verdict.reason, verdict.code)

        added = 0
        for item in parsed:
            if item.key() in existing:
                continue
            item.added_at = now
            target.items.append(item)
            existing.add(item.key())
            added += 1
        if added:
            target.updated_at = now
            _persist(bins)
        return target


def remove_item(bin_id: str, project_name: str, media_id: Any) -> Bin:
    with _BINS_LOCK:
        bins = _load_bin_objects()
        target = _find(bins, bin_id)
        if target is None:
            raise BinsError("bin not found: {0}".format(bin_id))
        drop = _item_key(project_name, media_id)
        kept = [item for item in target.items if item.key() != drop]
        if len(kept) != len(target.items):
            target.items = kept
            target.updated_at = _now_iso()
            _persist(bins)
        return target


def remove_media_everywhere(project_name: str, media_id: Any) -> int:
    """Drop one clip from EVERY bin. Returns how many bins were touched.

    Deleting a clip used to leave its reference behind in every 精選集 that held
    it, and that reference can never come good again: `delete_media_full` removes
    the row, so the id is gone, and restoring from the recycle bin re-ingests the
    file — which mints a NEW id. The old entry stays `ROW_MISSING` forever while
    still counting toward the bin's size.

    (The reference-integrity gate showing it as broken rather than silently wrong
    is why this was a papercut and not data loss. It also meant nobody had to fix
    it, which is how it survived.)

    One pass over the whole file under a single lock, rather than calling
    `remove_item` per bin: that would re-read, re-write and re-lock once per bin
    holding the clip, and a delete is not a good moment to be doing N writes.
    """
    drop = _item_key(project_name, media_id)
    with _BINS_LOCK:
        bins = _load_bin_objects()
        touched = 0
        for b in bins:
            kept = [item for item in b.items if item.key() != drop]
            if len(kept) != len(b.items):
                b.items = kept
                b.updated_at = _now_iso()
                touched += 1
        if touched:
            _persist(bins)
        return touched


def bin_item_status(project_name: str, media_id: Any) -> str:
    """Reference-integrity gate for one bin item. Composes the primitives that
    already exist but that nothing wires together for a cross-project item:
    registry lookup → library health → row-in-db → file-on-disk. Returns a status
    string (STATUS_* / a HealthStatus value). The resolved absolute path is used
    ONLY here (server-side); it is never returned to the client (Phase 16.2)."""
    # federation drags chromadb at import; keep it lazy so pure-CRUD callers and
    # tests don't pay for it.
    from projects import discover_projects
    from health import project_health, HealthStatus
    from federation import _connect_project_db, _fetch_media_row, _resolve_paths

    project = None
    for candidate in discover_projects():
        if candidate.name == project_name:
            project = candidate
            break
    if project is None:
        return STATUS_PROJECT_UNREGISTERED

    health = project_health(project)
    if health != HealthStatus.OK:
        return health.value if isinstance(health, HealthStatus) else str(health)

    conn = None
    try:
        conn = _connect_project_db(project)
        row = _fetch_media_row(conn, media_id)
        if row is None:
            return STATUS_ROW_MISSING
        _relative, absolute = _resolve_paths(project, row.get("path") or "")
        if not absolute or not os.path.exists(absolute):
            return STATUS_FILE_MISSING
        return STATUS_OK
    except Exception:
        return STATUS_ERROR
    finally:
        if conn is not None:
            conn.close()


def bin_item_statuses(items) -> Dict[Any, str]:
    """Batch of bin_item_status keyed by (project_name, str(media_id)).

    fable-audit round-5 #23: calling bin_item_status per item did a registry read +
    health probe + fresh SQLite connection PER ITEM — a 200-item NAS bin cost ~200×
    (discover + health + open) on every bin open and every add/remove. This groups
    items by project and pays that cost ONCE per project, fetching all of a project's
    rows with a single WHERE id IN (...). `items` is any iterable exposing
    project_name + media_id (bin items or (name, id) tuples)."""
    from projects import discover_projects
    from health import project_health, HealthStatus
    from federation import _connect_project_db, _resolve_paths

    by_project: Dict[str, list] = {}
    for it in items:
        pn = it.project_name if hasattr(it, "project_name") else it[0]
        mid = it.media_id if hasattr(it, "media_id") else it[1]
        by_project.setdefault(pn, []).append(str(mid))

    result: Dict[Any, str] = {}
    projects_by_name = {p.name: p for p in discover_projects()}  # ONE registry read

    for pn, mids in by_project.items():
        project = projects_by_name.get(pn)
        if project is None:
            for mid in mids:
                result[(pn, mid)] = STATUS_PROJECT_UNREGISTERED
            continue
        health = project_health(project)  # ONE health probe per project
        if health != HealthStatus.OK:
            hv = health.value if isinstance(health, HealthStatus) else str(health)
            for mid in mids:
                result[(pn, mid)] = hv
            continue
        conn = None
        try:
            conn = _connect_project_db(project)  # ONE connection per project
            placeholders = ",".join("?" * len(mids))
            rows = {
                str(r["id"]): r
                for r in conn.execute(
                    "SELECT id, path FROM media WHERE id IN ({0})".format(placeholders),
                    mids,
                ).fetchall()
            }
            for mid in mids:
                row = rows.get(mid)
                if row is None:
                    result[(pn, mid)] = STATUS_ROW_MISSING
                    continue
                _relative, absolute = _resolve_paths(project, row["path"] or "")
                if not absolute or not os.path.exists(absolute):
                    result[(pn, mid)] = STATUS_FILE_MISSING
                else:
                    result[(pn, mid)] = STATUS_OK
        except Exception:
            for mid in mids:
                result.setdefault((pn, mid), STATUS_ERROR)
        finally:
            if conn is not None:
                conn.close()
    return result


def resolve_source(project_name: str, media_id: Any) -> Optional[Dict[str, Any]]:
    """Server-side ONLY: (project_name, media_id) → {absolute_path, filename,
    status}. The absolute path is for the copy orchestrator (server.py); never
    hand it to a client. Returns None only if the project is unregistered."""
    from projects import discover_projects
    from federation import _connect_project_db, _fetch_media_row, _resolve_paths

    project = None
    for candidate in discover_projects():
        if candidate.name == project_name:
            project = candidate
            break
    if project is None:
        return None

    status = bin_item_status(project_name, media_id)
    absolute = ""
    filename = ""
    if status == STATUS_OK:
        conn = None
        try:
            conn = _connect_project_db(project)
            row = _fetch_media_row(conn, media_id)
            if row is not None:
                _relative, absolute = _resolve_paths(project, row.get("path") or "")
                filename = row.get("filename") or ""
        finally:
            if conn is not None:
                conn.close()
    return {
        "project_name": project_name,
        "media_id": str(media_id),
        "status": status,
        "absolute_path": absolute,
        "filename": filename,
    }
