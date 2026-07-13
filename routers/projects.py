"""Projects registry routes (R5-25 / round-5 #51 router split).

The /api/projects group: list / add / remove / sync registry projects + a health
probe. Thin wrappers over the already-extracted `projects` registry module. The
create body model (ProjectCreate, with its name validator) and the path-visibility
sanitisers (fable-audit #22: absolute roots only leak to admin-scoped callers)
are projects-local and move here. Imports auth + projects + pathres — no server
import, no cycle.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

import projects as project_registry
from auth import require_scopes
from pathres import _basename_safe

router = APIRouter()


class ProjectCreate(BaseModel):
    name: str
    path: str
    tags: Optional[List[str]] = None

    # audit M21: empty / control-char / separator-bearing names were accepted —
    # a name containing '/' can be created but never DELETEd (the path param
    # can't match it), and unbounded names bloat the registry.
    @field_validator("name")
    @classmethod
    def _clean_project_name(cls, v: str) -> str:
        cleaned = " ".join(
            "".join(c for c in (v or "") if c == " " or (ord(c) >= 0x20 and c != "\x7f")).split()
        )
        if not cleaned:
            raise ValueError("project name must not be empty")
        if len(cleaned) > 100:
            raise ValueError("project name too long (max 100)")
        if "/" in cleaned or "\\" in cleaned:
            raise ValueError("project name must not contain path separators")
        return cleaned


def _project_paths_visible(tok: dict) -> bool:
    """fable-audit 2026-07-12 (#22): absolute project roots leave the backend only
    for admin-scoped callers. Loopback-trust already grants all scopes (incl.
    'admin'), so the LOCAL operator's registry / mount UI is unaffected (case a);
    a remote admin token also sees full paths (case c); a remote projects_read-only
    token gets basenames — so a browse-only LAN/Tailscale client can't map the
    operator's home layout, drive structure, or other clients' folder names."""
    return "admin" in (tok.get("scopes") or ())


def _sanitize_project_paths(projects: list, tok: dict) -> list:
    if _project_paths_visible(tok):
        return projects
    for p in projects:
        if p.get("path"):
            p["path"] = _basename_safe(p["path"])
    return projects


@router.get("/api/projects")
def list_projects(
    _tok: dict = Depends(require_scopes("projects_read")),
):
    # A corrupt ~/.arkiv-projects.json must not 500 with a raw stack trace on
    # every read — return a clean error the UI can show.
    try:
        projects = [project.to_dict() for project in project_registry.list_registry_projects()]
    except project_registry.RegistryError as exc:
        raise HTTPException(status_code=500, detail="project registry unreadable: {0}".format(exc))
    _sanitize_project_paths(projects, _tok)
    return {"projects": projects, "total": len(projects)}


@router.post("/api/projects")
def add_project(
    body: ProjectCreate,
    _tok: dict = Depends(require_scopes("projects_write")),
):
    # audit M21: registry add_project silently REPLACES an existing entry of the
    # same name — surface the collision as 409 instead of overwriting.
    try:
        existing = {p.name for p in project_registry.list_registry_projects()}
    except project_registry.RegistryError as exc:
        raise HTTPException(status_code=500, detail="project registry unreadable: {0}".format(exc))
    if body.name in existing:
        raise HTTPException(status_code=409, detail="project name already exists: {0}".format(body.name))
    try:
        project = project_registry.add_project(body.name, body.path, body.tags or [])
    except project_registry.RegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return project.to_dict()


@router.delete("/api/projects/{name}")
def remove_project(
    name: str,
    _tok: dict = Depends(require_scopes("projects_write")),
):
    try:
        project = project_registry.remove_project(name)
    except project_registry.RegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return project.to_dict()


@router.post("/api/projects/sync")
def sync_projects(
    _tok: dict = Depends(require_scopes("projects_write")),
):
    try:
        projects = [project.to_dict() for project in project_registry.sync_projects()]
    except project_registry.RegistryError as exc:
        raise HTTPException(status_code=500, detail="project registry unreadable: {0}".format(exc))
    return {"projects": projects, "total": len(projects)}


@router.get("/api/projects/health")
def projects_health(
    _tok: dict = Depends(require_scopes("projects_read")),
):
    projects = project_registry.health_projects()
    _sanitize_project_paths(projects, _tok)  # fable-audit #22 — see list_projects
    ok_count = sum(1 for item in projects if item["status"] == "ok")
    return {"projects": projects, "total": len(projects), "ok": ok_count}
