"""Settings routes (R5-25 / round-5 #51 router split).

The /api/settings group: read the effective (default ← global ← project) settings,
persist a batch of overrides at a scope, and reset one key. Thin wrappers over the
already-extracted `settings` store; the scope-validation helper and the update
body model are settings-local and move here with the handlers. Imports auth +
settings + config + projects (registry) — no server import, no cycle.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import config
import projects as project_registry
import settings as settings_store
from auth import require_scopes

router = APIRouter()


def _resolve_settings_scope(scope: str) -> str:
    """Validate the scope param. 'global' is the library-wide default; any other
    value must be a known project root (registry or the current PROJECT_ROOT) —
    we never let an arbitrary string become a scope row, so the table can't be
    used as free-form key/value storage."""
    if not scope or scope == settings_store.GLOBAL_SCOPE:
        return settings_store.GLOBAL_SCOPE
    known = {str(config.PROJECT_ROOT)}
    try:
        known.update(p.path for p in project_registry.list_registry_projects())
    except project_registry.RegistryError:
        pass
    if scope not in known:
        raise HTTPException(status_code=400, detail="unknown settings scope: {0}".format(scope))
    return scope


@router.get("/api/settings")
def get_settings(
    scope: str = "global",
    _tok: dict = Depends(require_scopes("projects_read")),
):
    """Effective settings (default ← global ← project) + metadata per key."""
    resolved = _resolve_settings_scope(scope)
    project = None if resolved == settings_store.GLOBAL_SCOPE else resolved
    return {"scope": resolved, "settings": settings_store.describe(project=project)}


class SettingsUpdate(BaseModel):
    scope: str = "global"
    values: dict


@router.put("/api/settings")
def put_settings(
    body: SettingsUpdate,
    _tok: dict = Depends(require_scopes("admin")),
):
    """Persist a batch of overrides at the given scope. Validate-all-then-write:
    a single bad key/value rejects the whole batch (422), nothing is stored."""
    resolved = _resolve_settings_scope(body.scope)
    try:
        written = settings_store.put(body.values, scope=resolved)
    except settings_store.SettingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    project = None if resolved == settings_store.GLOBAL_SCOPE else resolved
    return {
        "scope": resolved,
        "written": written,
        "settings": settings_store.describe(project=project),
    }


@router.delete("/api/settings/{key}")
def reset_setting(
    key: str,
    scope: str = "global",
    _tok: dict = Depends(require_scopes("admin")),
):
    """Drop one override so the key falls back to the next layer down."""
    resolved = _resolve_settings_scope(scope)
    try:
        settings_store.reset(key, scope=resolved)
    except settings_store.SettingError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"scope": resolved, "reset": key}
