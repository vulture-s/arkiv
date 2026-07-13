"""Path-resolution service for the API layer.

R5-25 / round-5 #51: the APIRouter split is blocked by ~50 cross-group helpers —
a naive cut has each router doing `from server import _resolve_media_path`, and
since `server` imports the routers, that's a partially-initialized-module
ImportError. The fix is to extract the shared helpers into leaf service modules
that the routers (and server) import, breaking the cycle. This is the first such
module: the non-leaking display-path + media-path resolution used by ~every
media/bins/export/stream route.

Depends only on `db` (to_relative / resolve_path) and stdlib — no server state —
so it sits safely at the bottom of the import graph. server.py re-exports these
names for backward compatibility (existing call sites + tests that reference
`server._resolve_media_path` keep working).
"""
import os

import db


def _basename_safe(value: str) -> str:
    """Separator-agnostic basename. `os.path.basename` on a POSIX host treats `\\`
    as an ordinary character, so a Windows project path (`C:\\Users\\me\\proj`)
    handed over by a cross-platform federation peer would survive `basename` +
    `rstrip("/")` and leak intact. Normalise both separators first."""
    if not value:
        return value
    normalized = str(value).replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1] or normalized


def _looks_absolute(p: str) -> bool:
    """True for POSIX (`/x`), Windows drive (`C:\\x`), or UNC (`\\\\host`) absolute
    paths. `os.path.isabs` on a POSIX host misses the Windows forms, which would
    let a cross-platform peer's absolute path slip past the leak guard."""
    if not p:
        return False
    return (
        p.startswith("/")
        or p.startswith("\\\\")
        # Windows drive form `X:` followed by EITHER separator (`C:\` or `C:/` —
        # Windows APIs emit and accept both). Security-first call on an inherent
        # ambiguity: `C:/...` could also be a POSIX *relative* path under a dir
        # literally named "C:", but a leak guard must not let a Windows-absolute
        # path through, and a Unix media dir literally named "C:" is pathological.
        # So we basename it (the rare round-trip loss is the accepted trade-off vs
        # re-opening the path leak). (Codex round-1 wanted C:/ preserved; round-2
        # showed that re-leaks C:/ Windows absolutes — no-leak wins.)
        or (len(p) >= 3 and p[0].isalpha() and p[1] == ":" and p[2] in ("\\", "/"))
    )


def _display_path(path: str) -> str:
    """Phase 16.2: the non-leaking path form for API responses.

    Returning the absolute fs path leaked the operator's directory tree
    (/Volumes/home/影片專案/…) to any read-scope / loopback client. We return the
    PROJECT_ROOT-relative form; a legacy row whose stored path is absolute AND
    outside PROJECT_ROOT (so to_relative can't relativize it) is reduced to its
    basename rather than leaking the full path. Relative paths round-trip through
    /api/open-file (db.is_processed matches relative; the server re-absolutizes);
    once a library is migrated (ingest.py --migrate-relative) every row is
    relative and open-file works for all of them.
    """
    if not path:
        return path
    rel = db.to_relative(path)
    if _looks_absolute(rel):  # absolute (POSIX/Windows/UNC) & outside root — don't leak
        return _basename_safe(rel)
    return rel


def _resolve_record(rec: dict) -> dict:
    if rec.get("path"):
        rec["path"] = _display_path(rec["path"])
    if rec.get("thumbnail_path"):
        rec["thumbnail_path"] = _display_path(rec["thumbnail_path"])
    return rec


def _resolve_frame(frame: dict) -> dict:
    if frame.get("thumbnail_path"):
        frame["thumbnail_path"] = _display_path(frame["thumbnail_path"])
    return frame


def _resolve_media_path(path: str) -> str:
    if not path:
        return path
    if os.name == "nt" and path.startswith("/"):
        return path
    return db.resolve_path(path)
