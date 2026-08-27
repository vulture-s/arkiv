"""Phase 9.7 G5② — persisted, curated settings overrides.

config.py holds the baked-in defaults (env-driven). This module layers an
operator-editable override on top, persisted in the `settings` table:

    effective(key) = SCHEMA default ← global row ← project row

Only keys declared in SETTINGS_SCHEMA can ever be read or written, so a
malicious/garbage PUT can't poison arbitrary state. Each key carries a type
that PUT coerces + validates against; an out-of-range / wrong-type value is
rejected (422) rather than silently stored.

Discipline note (no-fake): a setting only belongs here if something actually
consumes it. The pipeline / export paths read these via effective(); the
frontend pre-fills its pickers from the same values. We do NOT add a control
that has no downstream effect.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import config
import db

GLOBAL_SCOPE = "global"


def canonical_scope(scope: Optional[str]) -> Optional[str]:
    """One spelling per project, so that a row written is a row found.

    A scope key is a filesystem path, and a path has more than one spelling:
    `~/lib`, `/Users/me/lib`, a symlink pointing at it, and a trailing slash are
    four strings for one project. Measured: with `ARKIV_PROJECT_ROOT` set to a
    symlink, `str(config.PROJECT_ROOT)` and its resolved form differ —
    `_validate_writable_path` computes the canonical form only to check it against
    the system-directory denylist and then returns the path it was given. Meanwhile
    `routers/settings.py` accepts either that string or the registry's own
    spelling. So a row written under one and read under the other simply misses,
    and misses in silence.

    `normcase` rather than a bare `resolve`: on Windows `C:\\x` and `c:\\x` are the
    same directory and would otherwise be two rows. It is a no-op on POSIX, which
    is where two paths differing only in case really are two directories.

    `GLOBAL_SCOPE` and None are not paths and pass through untouched.
    """
    if not scope or scope == GLOBAL_SCOPE:
        return scope
    return os.path.normcase(str(Path(scope).expanduser().resolve(strict=False)))


def current_scope() -> str:
    """The scope key for the library this process is serving.

    Read at call time, not at import: `config.PROJECT_ROOT` is what a test rebinds
    to point at a temp library, and a value frozen at import would make every such
    test read the developer's own project.
    """
    return canonical_scope(str(config.PROJECT_ROOT))


class SettingError(ValueError):
    """Raised on an unknown key, non-editable key, or invalid value."""


# Each entry: group/label for the UI, a `type`, a lazy `default` (callable so a
# test that rebinds config still gets the live default), optional `choices` for
# enums and `min`/`max` for numerics, and `editable` (False = read-only surface).
def _schema() -> Dict[str, Dict[str, Any]]:
    return {
        # --- Transcription ---
        "transcription.default_mode": {
            "group": "transcription",
            "label": "Whisper guard mode (0–4)",
            "type": "int",
            "default": lambda: config.WHISPER_GUARD_DEFAULT_MODE,
            "min": 0,
            "max": 4,
        },
        "transcription.default_language": {
            "group": "transcription",
            "label": "Forced language (blank = auto-detect)",
            "type": "str",
            "default": lambda: "",
        },
        # --- Vision ---
        "vision.model": {
            "group": "vision",
            "label": "Ollama vision model",
            "type": "str",
            "default": lambda: config.OLLAMA_VISION_MODEL,
        },
        "vision.num_ctx": {
            "group": "vision",
            "label": "Vision context window (num_ctx)",
            "type": "int",
            "default": lambda: config.OLLAMA_VISION_NUM_CTX,
            "min": 512,
            "max": 131072,
        },
        # --- Export defaults ---
        "export.default_dir": {
            "group": "export",
            "label": "Default export directory (blank = browser download)",
            "type": "str",
            "default": lambda: "",
        },
        "export.subtitle_max_cjk": {
            "group": "export",
            "label": "Subtitle line width (CJK units per line)",
            "type": "int",
            "default": lambda: config.SUBTITLE_MAX_CJK,
            "min": 8,
            "max": 40,
        },
        # --- Ingest defaults ---
        "ingest.recursive": {
            "group": "ingest",
            "label": "Recurse into sub-folders by default",
            "type": "bool",
            "default": lambda: True,
        },
    }


# Built once per process; cheap to rebuild but stable across a request.
SETTINGS_SCHEMA: Dict[str, Dict[str, Any]] = _schema()


def _coerce(key: str, raw: Any) -> Any:
    """Coerce + validate a raw value against the key's schema type."""
    spec = SETTINGS_SCHEMA.get(key)
    if spec is None:
        raise SettingError(f"unknown setting key: {key}")
    if not spec.get("editable", True):
        raise SettingError(f"setting is read-only: {key}")
    t = spec["type"]
    if t == "int":
        try:
            v = int(raw)
        except (TypeError, ValueError):
            raise SettingError(f"{key} must be an integer")
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and v < lo:
            raise SettingError(f"{key} must be >= {lo}")
        if hi is not None and v > hi:
            raise SettingError(f"{key} must be <= {hi}")
        return v
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if t == "str":
        v = "" if raw is None else str(raw)
        choices = spec.get("choices")
        if choices is not None and v not in choices:
            raise SettingError(f"{key} must be one of {choices}")
        return v
    raise SettingError(f"unsupported setting type: {t}")


def _stored_to_typed(key: str, stored: str) -> Any:
    """Decode a stored (always-TEXT) value back to its typed form."""
    spec = SETTINGS_SCHEMA[key]
    t = spec["type"]
    if t == "int":
        try:
            return int(stored)
        except (TypeError, ValueError):
            return spec["default"]()
    if t == "bool":
        return str(stored).strip().lower() in ("1", "true", "yes", "on")
    return "" if stored is None else str(stored)


def _typed_to_stored(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _rows_for(scope: str) -> Dict[str, str]:
    scope = canonical_scope(scope)
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE scope = ?", (scope,)
        ).fetchall()
    return {r["key"]: r["value"] for r in rows}


def effective(key: str, project: Optional[str] = None) -> Any:
    """Resolve the effective value: default ← global ← project."""
    if key not in SETTINGS_SCHEMA:
        raise SettingError(f"unknown setting key: {key}")
    value = SETTINGS_SCHEMA[key]["default"]()
    g = _rows_for(GLOBAL_SCOPE)
    if key in g and g[key] is not None:
        value = _stored_to_typed(key, g[key])
    if project:
        p = _rows_for(project)
        if key in p and p[key] is not None:
            value = _stored_to_typed(key, p[key])
    return value


def subtitle_max_cjk(project: Optional[str] = None) -> int:
    """Effective caption line width. Equals config.SUBTITLE_MAX_CJK when unset, so
    every subtitle export is unchanged until an operator actually moves it."""
    return effective("export.subtitle_max_cjk", project=project)


def vision_model(project: Optional[str] = None) -> str:
    """Effective vision model for an ingest run. Equals config.VISION_MODEL when
    unset, so wiring this in is behavior-preserving until an operator overrides."""
    return effective("vision.model", project=project)


def vision_num_ctx(project: Optional[str] = None) -> int:
    """Effective vision num_ctx. Equals config.OLLAMA_VISION_NUM_CTX when unset."""
    return effective("vision.num_ctx", project=project)


def describe(project: Optional[str] = None) -> List[Dict[str, Any]]:
    """Effective settings + metadata for every schema key (for GET)."""
    g = _rows_for(GLOBAL_SCOPE)
    p = _rows_for(project) if project else {}
    out: List[Dict[str, Any]] = []
    for key, spec in SETTINGS_SCHEMA.items():
        default = spec["default"]()
        value = default
        source = "default"
        if key in g and g[key] is not None:
            value = _stored_to_typed(key, g[key])
            source = "global"
        if project and key in p and p[key] is not None:
            value = _stored_to_typed(key, p[key])
            source = "project"
        item = {
            "key": key,
            "group": spec["group"],
            "label": spec["label"],
            "type": spec["type"],
            "value": value,
            "default": default,
            "source": source,
            "editable": spec.get("editable", True),
        }
        for opt in ("choices", "min", "max"):
            if opt in spec:
                item[opt] = spec[opt]
        out.append(item)
    return out


def put(values: Dict[str, Any], scope: str = GLOBAL_SCOPE) -> List[str]:
    """Validate + persist a batch of overrides. Returns the keys written.

    Raises SettingError on the first invalid key/value — nothing is written in
    that case (validate-all-then-write), so a bad key can't leave a partial set.
    """
    if not isinstance(values, dict) or not values:
        raise SettingError("values must be a non-empty object")
    scope = canonical_scope(scope)
    coerced = {k: _coerce(k, v) for k, v in values.items()}  # validates all first
    with db.get_conn() as conn:
        for key, val in coerced.items():
            conn.execute(
                "INSERT INTO settings(scope, key, value, updated_at) "
                "VALUES(?, ?, ?, datetime('now')) "
                "ON CONFLICT(scope, key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at",
                (scope, key, _typed_to_stored(val)),
            )
        conn.commit()
    return list(coerced.keys())


def reset(key: str, scope: str = GLOBAL_SCOPE) -> None:
    """Drop an override so the key falls back to the next layer down."""
    if key not in SETTINGS_SCHEMA:
        raise SettingError(f"unknown setting key: {key}")
    scope = canonical_scope(scope)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM settings WHERE scope = ? AND key = ?", (scope, key))
        conn.commit()
