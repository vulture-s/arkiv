"""Offload (DIT card→drive) routes (R5-25 / round-5 #51 router split).

The /api/offload group: a read-only layout preview and the streaming copy+verify
run (never deletes source), plus the legacy /dit redirect. The per-source
single-flight primitives (_offload_lock / _offload_active / _acquire_offload_slot
/ _release_offload_slot — R5-17 #19) are offload-only and move here with the
handlers. `BASE_DIR` (config) replaces server.ROOT for locating offload.py. Imports
auth + config + webguard (dst denylist) + offload — no server import, no cycle.
"""
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel

import config
from auth import require_scopes
from config import BASE_DIR
from webguard import _assert_offload_dst_safe

router = APIRouter()

# R5-17 (#19): single-flight the offload endpoint PER SOURCE. Two runs over the
# same card (a double-clicked Run, or a retry over a still-live run) would race
# on that source's resumable state file, tearing the JSON mid-write and
# double-copying every byte. Keyed on the resolved source path so offloading two
# *different* cards concurrently is still allowed.
_offload_lock = threading.Lock()
_offload_active = set()


def _acquire_offload_slot(key: str) -> bool:
    with _offload_lock:
        if key in _offload_active:
            return False
        _offload_active.add(key)
        return True


def _release_offload_slot(key: str) -> None:
    with _offload_lock:
        _offload_active.discard(key)


def _offload_state_path(state_cwd: Path, src: Path, include_heic: bool = False) -> Path:
    """Resumable state file for ONE card.

    Audit 2026-10-09 (HIGH): this used to be keyed on the mount path alone, so a
    second card mounted at the same path (Sony → /Volumes/Untitled, Canon →
    /Volumes/EOS_DIGITAL) silently reused the first card's state and was reported
    done without being copied. The key now also covers the card's content
    fingerprint (every file's relpath + size + mtime) — the same card re-inserted
    after an interruption resumes; a different card starts a fresh state. The
    engine additionally refuses a state that does not match the source, so a
    fingerprint collision cannot reintroduce the bug."""
    import hashlib as _hashlib
    import offload as _offload
    files = _offload._collect_sources(src, include_heic=include_heic)
    fp = _offload.source_fingerprint(files, src)
    key = "{0}\0{1}\0{2}".format(src, int(bool(include_heic)), fp)
    return state_cwd / "offload-state-{0}.json".format(
        _hashlib.sha1(key.encode("utf-8")).hexdigest()[:16])


class OffloadPreviewRequest(BaseModel):
    src: str
    organize: Optional[str] = None
    include_heic: bool = False
    limit: int = 200  # cap the preview; a full card can be hundreds of files


class OffloadRequest(BaseModel):
    src: str
    dst: List[str]
    organize: Optional[str] = None
    include_heic: bool = False


@router.post("/api/offload/preview")
def offload_preview(
    body: OffloadPreviewRequest,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Read-only layout preview for the DIT Offload UI — shows source→dest mapping
    under the --organize template without copying anything."""
    import offload as _offload
    src = Path(body.src).expanduser().resolve()
    if not src.exists():
        raise HTTPException(400, "來源路徑不存在")
    # Clamp the limit server-side: it's client-controlled and a 0 / negative / huge
    # value would otherwise disable the cap and force full enumeration + exiftool +
    # a giant JSON on a large card (Codex). Always (0, 200].
    safe_limit = body.limit if 0 < body.limit <= 200 else 200
    try:
        return _offload.preview_layout(
            str(src), organize=body.organize, include_heic=body.include_heic,
            limit=safe_limit)
    except ValueError as exc:  # bad --organize template
        raise HTTPException(400, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/offload")
def offload_run(
    body: OffloadRequest,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Run a DIT offload (copy + hash-verify + MHL, never deletes source) with the
    --organize naming policy. Mirrors /api/ingest's subprocess pattern. Source/dest
    are arbitrary by design (card → backup drives); gated by videos_write (loopback
    is token-free, a remote read token is refused)."""
    import subprocess, sys
    src = Path(body.src).expanduser().resolve()
    if not src.exists():
        raise HTTPException(400, "來源路徑不存在")
    if not body.dst:
        raise HTTPException(400, "至少需要一個目的地 (dst)")
    for d in body.dst:
        _assert_offload_dst_safe(d)  # fable-audit 2026-07-12 (#1): block system/exec dirs
    cmd = [sys.executable, str(BASE_DIR / "offload.py"), "--src", str(src), "--progress", "json"]
    for d in body.dst:
        cmd += ["--dst", d]
    if body.organize:
        cmd += ["--organize", body.organize]
    if body.include_heic:
        cmd += ["--include-heic"]
    # offload writes offload-state.json into its cwd; keep that out of the install
    # ROOT (it would dirty the repo / install dir on every UI run). Use the
    # project's .arkiv dir so the state is scoped + resumable per project.
    state_cwd = config.THUMBNAILS_DIR.parent
    state_cwd.mkdir(parents=True, exist_ok=True)
    # R5-17 (#19): give each source card its OWN resumable state file and ALWAYS
    # --resume it. Previously no --resume was passed, so offload.py fell back to a
    # single cwd/offload-state.json: a retry re-copied from zero, and a second
    # concurrent card clobbered the first's state. A stable per-source path means a
    # 400GB offload that dies at 92% picks up from the last verified file.
    try:
        state_path = _offload_state_path(state_cwd, src, body.include_heic)
    except OSError as exc:  # unreadable card → a 400 the UI can show, not a 500
        raise HTTPException(400, "無法讀取來源：{0}".format(exc))
    cmd += ["--resume", str(state_path)]

    # Single-flight per source (see _acquire_offload_slot): reject a second run over
    # the same card so the two can't tear the shared state file. Acquired here (sync)
    # so the collision surfaces as a real 409; released in the generator's finally.
    slot_key = str(src)
    if not _acquire_offload_slot(slot_key):
        raise HTTPException(409, "此來源的轉存正在進行中，請稍候")

    def _stream():
        # Stream the offload's --progress json events line-by-line (ndjson) so the UI
        # shows live per-file progress instead of blocking on one giant request.
        import json as _json
        proc = None
        saw_done = False
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace",
                bufsize=1, cwd=str(state_cwd))
            for line in proc.stdout:
                try:  # JSON parse (not substring) so a filename can't false-positive
                    if _json.loads(line).get("type") == "done":
                        saw_done = True
                except (ValueError, AttributeError):
                    pass
                yield line if line.endswith("\n") else line + "\n"
            proc.wait()
            if not saw_done:
                # offload exited before emitting its terminal event (e.g. a
                # ValueError/RuntimeError exit path) — synthesize one so the UI's
                # done-handler always fires instead of hanging on "running" (Codex).
                yield _json.dumps({"type": "done", "code": proc.returncode if proc.returncode is not None else 1,
                                   "summary": {}}, ensure_ascii=False) + "\n"
        except GeneratorExit:
            # client disconnected (Stop button / navigate-away) — stop the
            # (resumable) offload and bound the wait so a stalled child can't pin
            # the worker forever (Codex). The per-source state file is left intact
            # so the next Run resumes.
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()  # reap to avoid a zombie if terminate timed out
            raise
        finally:
            if proc is not None and proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
            _release_offload_slot(slot_key)
    return StreamingResponse(_stream(), media_type="application/x-ndjson")


class OffloadResolveRequest(BaseModel):
    src: str
    dst: str
    rel: str  # the clip's card-layout path, as reported in the run's needs_rename list
    action: str  # "rename" | "skip"
    new_name: Optional[str] = None
    confirm: bool = False  # required for "skip": that clip is then NOT backed up
    include_heic: bool = False


@router.post("/api/offload/resolve")
def offload_resolve(
    body: OffloadResolveRequest,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Settle one same-name conflict of a finished offload (Hevin 2026-10-09
    23:14): copy that single clip under the user's new name (hash-verified, never
    overwrites, MHL records the original path) or — confirmed — skip it, leaving
    the card NOT done. The state file is found exactly like /api/offload finds it
    (same card → same state), so the card must still be mounted; the engine
    re-checks the clip against the state before copying."""
    import offload as _offload
    src = Path(body.src).expanduser().resolve()
    if not src.exists():
        raise HTTPException(400, "來源路徑不存在（卡片要插著才能補拷）")
    _assert_offload_dst_safe(body.dst)
    state_cwd = config.THUMBNAILS_DIR.parent
    try:
        state_path = _offload_state_path(state_cwd, src, body.include_heic)
    except OSError as exc:
        raise HTTPException(400, "無法讀取來源：{0}".format(exc))
    if not state_path.exists():
        raise HTTPException(400, "找不到這張卡的轉存紀錄 — 請先跑一次 offload")
    slot_key = str(src)
    if not _acquire_offload_slot(slot_key):
        raise HTTPException(409, "此來源的轉存正在進行中，請稍候")
    try:
        return _offload.resolve_conflict(
            state_path, body.dst, body.rel, body.action,
            new_name=body.new_name, confirm=body.confirm)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        _release_offload_slot(slot_key)


@router.get("/dit")
def serve_dit():
    """Legacy DIT path — the standalone dit-offload.html island was ported into
    the SPA (Svelte cutover Phase 3). Redirect old bookmarks to the SPA route."""
    return RedirectResponse(url="/#/offload", status_code=308)
