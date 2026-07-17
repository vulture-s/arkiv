from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from health import HealthStatus, project_health


REGISTRY_VERSION = 1

# Serializes registry read-modify-write so two concurrent add/remove/sync calls
# (FastAPI runs sync handlers in a threadpool) can't lose-update each other.
_REGISTRY_LOCK = threading.Lock()


class RegistryError(Exception):
    pass


def _default_registry_path() -> Path:
    return Path(os.getenv("ARKIV_PROJECTS_REGISTRY", str(Path.home() / ".arkiv-projects.json"))).expanduser().resolve(strict=False)


def _normalize_key(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False)).casefold()


def _iso_from_mtime(path: Path) -> str:
    dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _split_roots(value: str) -> List[str]:
    roots = []
    for chunk in value.split(os.pathsep):
        for part in chunk.split(","):
            part = part.strip()
            if part:
                roots.append(part)
    return roots


# Phase 8.0c renamed the per-project SQLite DB media.db → project.db. Libraries
# indexed before that rename still keep their corpus in the legacy
# .arkiv/media.db and may carry only an EMPTY .arkiv/project.db stub, so any code
# that hardcodes project.db (federation search, registry sync) reads an empty DB
# and returns nothing for those projects. resolve_project_db() falls back to the
# legacy name in exactly that case, while a real, populated project.db (the
# common case) is unaffected. Mirrors config.DB_PATH's env-override-then-default
# style: an explicit path always wins.
_DEFAULT_DB_NAME = "project.db"
_LEGACY_DB_NAME = "media.db"


def _media_row_count(db_path: Path) -> int:
    """Rows in the ``media`` table, or -1 when the DB is missing / unreadable /
    has no media table. Opened read-only (URI ``mode=ro``) so probing a project's
    DB never creates a stub file nor writes -wal/-shm side files against it."""
    try:
        if not db_path.exists():
            return -1
        uri = db_path.resolve(strict=False).as_uri() + "?mode=ro"
    except (OSError, ValueError):
        return -1
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error:
        return -1
    try:
        row = conn.execute("SELECT COUNT(*) FROM media").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return -1
    finally:
        conn.close()


def resolve_project_db(project_root, explicit=None) -> Path:
    """Resolve the SQLite DB path for a project root.

    Precedence:
      1. ``explicit`` — an operator-configured override (e.g. ARKIV_DB_PATH /
         ``--db`` for the current install) — when provided;
      2. ``.arkiv/project.db`` — the Phase 8.0c default;
      3. ``.arkiv/media.db`` — the pre-8.0c legacy name — but ONLY as a fallback
         when project.db is absent or an empty stub (0 media rows) while media.db
         exists and holds data.

    A project with a real, populated project.db always resolves to it (the legacy
    DB is not even opened), so existing libraries are completely unaffected."""
    if explicit is not None:
        return Path(explicit).expanduser()
    arkiv_dir = Path(project_root).expanduser() / ".arkiv"
    project_db = arkiv_dir / _DEFAULT_DB_NAME
    # Short-circuit on the common case: a populated project.db wins immediately
    # and media.db is never touched.
    if _media_row_count(project_db) <= 0:
        legacy_db = arkiv_dir / _LEGACY_DB_NAME
        if _media_row_count(legacy_db) > 0:
            return legacy_db
    return project_db


@dataclass
class ProjectMeta(object):
    name: str
    path: Path
    added_at: Optional[str] = None
    last_indexed_at: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    source: str = "registry"

    @classmethod
    def from_mapping(cls, data: Dict[str, Any], source: str = "registry") -> "ProjectMeta":
        if not isinstance(data, dict):
            raise RegistryError("project entry must be an object")
        name = data.get("name")
        path = data.get("path")
        if not name or not path:
            raise RegistryError("project entry missing name or path")
        tags = data.get("tags") or []
        if not isinstance(tags, list):
            raise RegistryError("project entry tags must be a list")
        return cls(
            name=str(name),
            path=Path(str(path)).expanduser(),
            added_at=data.get("added_at"),
            last_indexed_at=data.get("last_indexed_at"),
            tags=[str(tag) for tag in tags if str(tag).strip()],
            source=source,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path.expanduser()),
            "added_at": self.added_at,
            "last_indexed_at": self.last_indexed_at,
            "tags": list(self.tags),
            "source": self.source,
        }

    def key(self) -> str:
        return _normalize_key(self.path)


def load_registry() -> Dict[str, Any]:
    path = _default_registry_path()
    if not path.exists():
        data = {"version": REGISTRY_VERSION, "projects": []}
        save_registry(data)
        return data
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError as exc:
        raise RegistryError("registry JSON is corrupt") from exc
    if not isinstance(data, dict):
        raise RegistryError("registry root must be an object")
    projects = data.get("projects", [])
    if not isinstance(projects, list):
        raise RegistryError("registry projects must be a list")
    version = data.get("version", REGISTRY_VERSION)
    if version != REGISTRY_VERSION:
        raise RegistryError("unsupported registry version")
    data["version"] = version
    data["projects"] = projects
    return data


def save_registry(data: Dict[str, Any]) -> None:
    path = _default_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": int(data.get("version", REGISTRY_VERSION)),
        "projects": data.get("projects", []),
    }
    # Unique tmp name per writer — a shared "<file>.tmp" let two concurrent saves
    # interleave bytes into the same temp file and publish a truncated/garbled
    # registry via replace(). os.replace() is atomic so the final file is always
    # one writer's complete payload.
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


def list_registry_projects() -> List[ProjectMeta]:
    registry = load_registry()
    items = []
    for raw in registry.get("projects", []):
        items.append(ProjectMeta.from_mapping(raw))
    return items


def discover_projects() -> List[ProjectMeta]:
    items = []
    seen = set()
    for project in list_registry_projects():
        key = project.key()
        if key in seen:
            continue
        items.append(project)
        seen.add(key)

    env_roots = os.getenv("ARKIV_PROJECT_ROOTS", "")
    for root in _split_roots(env_roots):
        path = Path(root).expanduser()
        key = _normalize_key(path)
        if key in seen:
            continue
        items.append(
            ProjectMeta(
                name=path.name or str(path),
                path=path,
                tags=[],
                source="env",
            )
        )
        seen.add(key)
    return items


def _find_by_name(projects: Iterable[ProjectMeta], name: str) -> Optional[ProjectMeta]:
    for project in projects:
        if project.name == name:
            return project
    return None


def add_project(name: str, path: str, tags: Optional[List[str]] = None) -> ProjectMeta:
    project_path = Path(path).expanduser()
    if not project_path.exists():
        raise RegistryError("project path not found: {0}".format(path))

    with _REGISTRY_LOCK:
        registry = load_registry()
        projects = [ProjectMeta.from_mapping(raw) for raw in registry.get("projects", [])]
        existing_name = _find_by_name(projects, name)
        if existing_name is not None:
            projects = [p for p in projects if p.name != name]
        else:
            existing_path_key = _normalize_key(project_path)
            if any(p.key() == existing_path_key for p in projects):
                raise RegistryError("project path already registered: {0}".format(path))

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        project = ProjectMeta(
            name=name,
            path=project_path,
            added_at=existing_name.added_at if existing_name and existing_name.added_at else now,
            last_indexed_at=existing_name.last_indexed_at if existing_name else None,
            tags=[tag for tag in (tags or []) if tag],
            source="registry",
        )
        projects.append(project)
        registry["projects"] = [item.to_dict() for item in sorted(projects, key=lambda item: item.name.lower())]
        save_registry(registry)
        return project


def remove_project(name: str) -> ProjectMeta:
    with _REGISTRY_LOCK:
        registry = load_registry()
        projects = [ProjectMeta.from_mapping(raw) for raw in registry.get("projects", [])]
        removed = None
        kept = []
        for project in projects:
            if project.name == name:
                removed = project
                continue
            kept.append(project)
        if removed is None:
            raise RegistryError("project not found: {0}".format(name))
        registry["projects"] = [item.to_dict() for item in kept]
        save_registry(registry)
        return removed


def sync_projects() -> List[ProjectMeta]:
    with _REGISTRY_LOCK:
        registry = load_registry()
        projects = [ProjectMeta.from_mapping(raw) for raw in registry.get("projects", [])]
        updated = []
        for project in projects:
            db_path = resolve_project_db(project.path)
            if db_path.exists():
                project.last_indexed_at = _iso_from_mtime(db_path)
            updated.append(project)
        registry["projects"] = [item.to_dict() for item in updated]
        save_registry(registry)
        return updated


def health_projects() -> List[Dict[str, Any]]:
    results = []
    for project in discover_projects():
        status = project_health(project)
        results.append({
            "name": project.name,
            "path": str(project.path),
            "status": status.value if isinstance(status, HealthStatus) else str(status),
            "tags": list(project.tags),
            "source": project.source,
        })
    return results


def _format_table(projects: List[ProjectMeta]) -> str:
    rows = [["name", "path", "last_indexed_at", "tags"]]
    for project in projects:
        rows.append([
            project.name,
            str(project.path),
            project.last_indexed_at or "",
            ",".join(project.tags),
        ])
    widths = [max(len(str(row[idx])) for row in rows) for idx in range(len(rows[0]))]
    lines = []
    for row_index, row in enumerate(rows):
        padded = [str(value).ljust(widths[idx]) for idx, value in enumerate(row)]
        lines.append("  ".join(padded))
        if row_index == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Manage arkiv project registry")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--format", choices=["table", "json"], default="table")

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("--name", required=True)
    add_parser.add_argument("--path", required=True)
    add_parser.add_argument("--tag", action="append", default=[])

    remove_parser = subparsers.add_parser("remove")
    remove_parser.add_argument("--name", required=True)

    sync_parser = subparsers.add_parser("sync")

    health_parser = subparsers.add_parser("health")
    health_parser.add_argument("--format", choices=["table", "json"], default="table")

    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            projects = list_registry_projects()
            if args.format == "json":
                print(json.dumps([project.to_dict() for project in projects], ensure_ascii=False, indent=2))
            else:
                print(_format_table(projects))
            return 0
        if args.command == "add":
            project = add_project(args.name, args.path, args.tag)
            print("added {0} -> {1}".format(project.name, project.path))
            return 0
        if args.command == "remove":
            project = remove_project(args.name)
            print("removed {0} -> {1}".format(project.name, project.path))
            return 0
        if args.command == "sync":
            projects = sync_projects()
            print("synced {0} project(s)".format(len(projects)))
            return 0
        if args.command == "health":
            rows = health_projects()
            if args.format == "json":
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                table_rows = [["name", "status", "path"]]
                for row in rows:
                    table_rows.append([row["name"], row["status"], row["path"]])
                widths = [max(len(str(row[idx])) for row in table_rows) for idx in range(len(table_rows[0]))]
                lines = []
                for row_index, row in enumerate(table_rows):
                    padded = [str(value).ljust(widths[idx]) for idx, value in enumerate(row)]
                    lines.append("  ".join(padded))
                    if row_index == 0:
                        lines.append("  ".join("-" * width for width in widths))
                print("\n".join(lines))
            return 0
    except RegistryError as exc:
        print(str(exc), file=sys.stderr)
        if "not found" in str(exc):
            return 2
        if "path not found" in str(exc):
            return 3
        return 4

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
