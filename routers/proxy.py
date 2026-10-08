"""Proxy-management routes (R5-25 / round-5 #51 router split).

H.264 proxy generation for browser-incompatible codecs (HEVC/ProRes): the
whole-library status/build, the per-id build, and the two background workers
(_build_proxies + the guarded _build_proxies_all). The whole-library build is
single-flighted by the shared R5-22 (#59) guard — imported from state.py (the ONE
instance) so a double-clicked "build all" can't launch parallel ffmpeg loops that
would stream truncated proxies mid-build. `_proxy_ready` (the consumer side of the
C1 atomic-write fix) moved to pathres.py so /api/stream + these routes share it.
Also hosts the editing-proxy routes (/api/proxy/editor*, ProRes beside the
source for the NLE — see editor_proxy.py), on their own single-flight slot.
Imports auth + db + config + editor_proxy + webguard + pathres + state — no server import, no cycle.
"""
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

import config
import db
import editor_proxy
from auth import require_scopes
from pathres import _proxy_ready, _resolve_media_path
from state import proxy_build as _proxy_guard
from state import editor_proxy_build as _editor_guard
from webguard import _assert_same_site

router = APIRouter()


@router.get("/api/proxy/status")
def proxy_status(_tok: dict = Depends(require_scopes("videos_read"))):
    """Check proxy status for all media files."""
    proxy_dir = config.PROXIES_DIR
    proxy_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM media").fetchall()
    proxied = sum(
        1 for r in rows
        if _proxy_ready(config.proxy_path_for(r["id"], _resolve_media_path(r["path"])))
    )
    size_mb = round(sum(p.stat().st_size for p in proxy_dir.glob("*.mp4")) / 1048576, 1)
    return {"total": len(rows), "proxied": proxied, "size_mb": size_mb}


@router.post("/api/proxy/build")
def proxy_build(request: Request, background_tasks: BackgroundTasks, _tok: dict = Depends(require_scopes("ingest_write"))):
    """Queue proxy generation for all HEVC/ProRes files without proxy."""
    _assert_same_site(request)  # audit M14
    proxy_dir = config.PROXIES_DIR
    proxy_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM media").fetchall()
    to_build = [
        dict(r) for r in rows
        if not _proxy_ready(config.proxy_path_for(r["id"], _resolve_media_path(r["path"])))
    ]
    if not to_build:
        return {"message": "全部 proxy 已存在", "queued": 0}
    # R5-22 (#59): single-flight the whole-library build so a double-click can't
    # launch parallel full-library ffmpeg loops (mid-build playback would stream
    # truncated proxies). The guarded wrapper releases the slot in its finally.
    if not _proxy_guard.acquire():
        raise HTTPException(409, "proxy 生成已在進行中，請稍候")
    background_tasks.add_task(_build_proxies_all, to_build)
    return {"message": f"開始生成 {len(to_build)} 個 proxy（背景執行）", "queued": len(to_build)}


@router.post("/api/proxy/build/{media_id}")
def proxy_build_one(media_id: int, request: Request, background_tasks: BackgroundTasks, _tok: dict = Depends(require_scopes("ingest_write"))):
    """Per-id proxy build — surface 自 7.7g 409 「生成 proxy」按鈕，使用者點到
    哪個 HEVC 就只建那個，避免 build all 整庫拖時間。"""
    _assert_same_site(request)  # audit M14
    proxy_dir = config.PROXIES_DIR
    proxy_dir.mkdir(parents=True, exist_ok=True)
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到媒體")
    src = _resolve_media_path(rec["path"])
    if _proxy_ready(config.proxy_path_for(media_id, src)):
        return {"message": "proxy 已存在", "queued": 0, "media_id": media_id}
    background_tasks.add_task(_build_proxies, [{"id": media_id, "path": rec["path"]}])
    return {
        "message": f"開始生成 proxy（背景執行）",
        "queued": 1,
        "media_id": media_id,
        "filename": rec.get("filename"),
    }


def _build_proxies_all(items: list):
    """Whole-library proxy build background task — holds the R5-22 (#59)
    single-flight for its whole lifetime and frees it in finally. The per-id build
    stays unguarded (targeted, cheap) and calls _build_proxies directly."""
    try:
        _build_proxies(items)
    finally:
        _proxy_guard.release()


def _build_proxies(items: list):
    """Background task: generate H.264 proxy for each file."""
    import ingest
    for item in items:
        src = _resolve_media_path(item["path"])
        try:
            result = ingest.generate_proxy(item["id"], src)
            if not result:
                print(f"[proxy] Failed {item['id']}")
        except Exception as e:
            print(f"[proxy] Failed {item['id']}: {e}")


# ── editing proxies (ProRes beside the source, for the NLE) ──────────────────
# Independent of everything above: a different file, folder, codec and purpose.
# See editor_proxy.py for why the Inspector proxy can't double as this.

class EditorProxyBody(BaseModel):
    ids: List[int]


@router.get("/api/proxy/editor/status")
def editor_proxy_status(_tok: dict = Depends(require_scopes("videos_read"))):
    """Progress of the current/last editing-proxy batch."""
    return dict(_editor_guard.progress)


@router.get("/api/proxy/editor/{media_id}")
def editor_proxy_one(media_id: int, _tok: dict = Depends(require_scopes("videos_read"))):
    """Is there already a `Proxy/<stem>.*` beside this clip? Read-only."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到媒體")
    src = _resolve_media_path(rec["path"])
    found = editor_proxy.existing_for(src)
    return {
        "media_id": media_id,
        "exists": found is not None,
        "path": str(found) if found else None,
        "target": str(editor_proxy.target_for(src)),
    }


@router.post("/api/proxy/editor")
def editor_proxy_build(body: EditorProxyBody, request: Request, background_tasks: BackgroundTasks,
                       _tok: dict = Depends(require_scopes("ingest_write"))):
    """Write ProRes Proxy files to `<source dir>/Proxy/` for the given ids.
    Writes into the media folder, never over an existing file there."""
    _assert_same_site(request)
    ids = list(dict.fromkeys(int(i) for i in body.ids))
    if not ids:
        raise HTTPException(422, "ids 不可為空")
    if not _editor_guard.acquire():
        raise HTTPException(409, "剪輯用 proxy 生成已在進行中，請稍候")
    _editor_guard.reset_progress(running=True, total=len(ids), done=0, created=0,
                                 exists=0, failed=0, current=None, results=[])
    background_tasks.add_task(_build_editor_proxies, ids)
    return {"message": "開始生成 {0} 個剪輯用 proxy（背景執行）".format(len(ids)), "queued": len(ids)}


def _build_editor_proxies(ids: list):
    p = _editor_guard.progress
    try:
        for mid in ids:
            p["current"] = mid
            try:
                res = editor_proxy.generate_for_ids([mid])[0]
            except Exception as exc:  # one bad clip must not end the batch
                print("[editor-proxy] {0} crashed: {1}".format(mid, exc))
                res = {"media_id": mid, "status": editor_proxy.FAILED, "reason": str(exc)}
            key = {editor_proxy.CREATED: "created", editor_proxy.EXISTS: "exists"}.get(res["status"], "failed")
            if res["status"] != "skipped":
                p[key] += 1
            # Per-clip outcome so the UI can say WHERE it wrote, or WHY not —
            # "failed" alone reads as a mystery. Capped: a --all batch is the CLI's job.
            if len(p["results"]) < 200:
                p["results"].append({k: res.get(k) for k in ("media_id", "status", "path", "reason")})
            p["done"] += 1
    finally:
        p["running"] = False
        p["current"] = None
        _editor_guard.release()
