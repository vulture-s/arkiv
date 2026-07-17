"""First-run sample-library loader (single-flight + progress poll).

POST /api/sample/seed ingests the bundled CC-BY sample clips (sample/clips/) so a
fresh library delivers the search-awe before the user has their own footage —
driven from the in-app "Load sample library" CTA (no terminal needed). Long-running
→ single-flight background task that also holds the shared H3 ingest slot (the seed
spawns ingest.py = whisper, so it must NOT run alongside a real /api/ingest, or a
second whisper OOMs a 16 GB box). Poll GET /api/sample/seed/status. Idempotent:
no-op when the clips are already indexed.

Imports the ONE shared guard from state.py (never a copy) — no server import, no cycle.
"""
import subprocess
import sys

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

import config
import db
import sample_prebuilt
from auth import require_scopes
from state import (
    sample_seed as _sample_guard,
    _acquire_ingest_slot,
    _release_ingest_slot,
)
from webguard import _assert_same_site

router = APIRouter()

_CLIPS_DIR = config.BASE_DIR / "sample" / "clips"


def _sample_basenames():
    return sorted(p.name for p in _CLIPS_DIR.glob("*.mp4"))


def _run_sample_seed():
    """Background worker: run seed_sample.py in a child process (idempotent, and it
    auto-embeds so search works immediately). Isolated like the other ingest workers
    (state._rebuild_embeddings). Releases both locks in finally so a wedged child
    can't pin them."""
    prog = _sample_guard.progress
    try:
        r = subprocess.run(
            [sys.executable, str(config.BASE_DIR / "scripts" / "seed_sample.py")],
            check=False, capture_output=True, text=True, timeout=1800,
        )
        prog["returncode"] = r.returncode
        prog["ok"] = r.returncode == 0
        prog["message"] = "done" if r.returncode == 0 else "seed failed — see Settings → System"
    except subprocess.TimeoutExpired:
        prog["returncode"], prog["ok"], prog["message"] = -1, False, "seed timed out"
    except Exception as e:  # noqa: BLE001
        prog["returncode"], prog["ok"], prog["message"] = -1, False, "seed error: {0}".format(type(e).__name__)
    finally:
        prog["running"] = False
        _sample_guard.release()
        _release_ingest_slot()


def _already_seeded(names):
    """True if every sample basename is already in the media table. Defensive: a
    missing/uninitialised DB counts as not-seeded (→ proceed to seed)."""
    try:
        with db.get_conn() as conn:
            known = {r[0] for r in conn.execute("SELECT filename FROM media").fetchall()}
    except Exception:  # noqa: BLE001
        return False
    return all(n in known for n in names)


@router.post("/api/sample/seed")
def sample_seed(
    request: Request,
    background_tasks: BackgroundTasks,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Load the bundled CC-BY sample clips into the current project so a fresh
    library is instantly searchable. Idempotent; single-flight; holds the shared
    ingest slot. Poll GET /api/sample/seed/status."""
    _assert_same_site(request)
    names = _sample_basenames()
    if not names:
        raise HTTPException(500, "no sample clips bundled")
    # Idempotency BEFORE any lock: if already seeded, don't grab the ingest slot
    # just to no-op (that would 409 a concurrent real ingest for nothing).
    if _already_seeded(names):
        return {"queued": 0, "message": "sample already loaded"}
    if not _sample_guard.acquire():
        raise HTTPException(409, "sample 載入已在進行中")
    # The seed spawns whisper — it must hold the H3 ingest slot. If a real ingest
    # holds it, refuse (free our own guard first so a later retry isn't rejected).
    if not _acquire_ingest_slot():
        _sample_guard.release()
        raise HTTPException(409, "已有匯入任務進行中，請稍候")
    _sample_guard.reset_progress(
        running=True, ok=False, returncode=None, message="loading…", clips=len(names),
    )
    background_tasks.add_task(_run_sample_seed)
    return {"queued": len(names)}


@router.get("/api/sample/seed/status")
def sample_seed_status(_tok: dict = Depends(require_scopes("projects_read"))):
    """Poll sample-seed progress {running, ok, returncode, message, clips}."""
    return dict(_sample_guard.progress)


# ── Pre-built (instant) sample library — A1 · launch Wave-0 ───────────────────
# Distinct from /api/sample/seed above (that re-ingests on demand = minutes +
# needs Ollama). These load the PRE-INDEXED artifact with zero pipeline. The
# server lifespan auto-seeds a fresh project; these back the in-app "Sample data"
# chip so the user can load-it-back-after-dismiss or remove it one-click.


@router.get("/api/sample/status")
def sample_prebuilt_status(_tok: dict = Depends(require_scopes("projects_read"))):
    """{available, loaded, dismissed, media_ids} — drives the 'Sample data' chip."""
    return sample_prebuilt.status()


@router.post("/api/sample/load")
def sample_prebuilt_load(
    request: Request,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Instant-load the pre-indexed sample into the current project (no re-ingest).
    Refuses a non-fresh project (409) — the caller falls back to /api/sample/seed
    there. Idempotent when already loaded."""
    _assert_same_site(request)
    # Hold the shared H3 ingest slot across the merge so a concurrent /api/ingest
    # can't interleave writes with the freshness-check → seed (same discipline as
    # /api/sample/seed).
    if not _acquire_ingest_slot():
        raise HTTPException(409, "已有匯入任務進行中，請稍候")
    try:
        res = sample_prebuilt.load_prebuilt()
    finally:
        _release_ingest_slot()
    if not res.get("ok"):
        reason = res.get("reason")
        if reason == "project-not-fresh":
            raise HTTPException(409, "專案已有內容，請改用重新索引的範例載入")
        raise HTTPException(404, "找不到預建範例（尚未打包）" if reason == "artifact-missing" else str(reason))
    return res


@router.post("/api/sample/remove")
def sample_prebuilt_remove(
    request: Request,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Remove exactly the seeded sample media (rows + vectors + files) and set the
    dismiss flag so auto-seed won't resurrect it. A user's own footage is untouched."""
    _assert_same_site(request)
    return sample_prebuilt.remove_sample()
