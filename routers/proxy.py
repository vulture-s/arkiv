"""Proxy-management routes (R5-25 / round-5 #51 router split).

H.264 proxy generation for browser-incompatible codecs (HEVC/ProRes): the
whole-library status/build, the per-id build, and the two background workers
(_build_proxies + the guarded _build_proxies_all). The whole-library build is
single-flighted by the shared R5-22 (#59) guard — imported from state.py (the ONE
instance) so a double-clicked "build all" can't launch parallel ffmpeg loops that
would stream truncated proxies mid-build. `_proxy_ready` (the consumer side of the
C1 atomic-write fix) moved to pathres.py so /api/stream + these routes share it.
Imports auth + db + config + webguard + pathres + state — no server import, no cycle.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

import config
import db
from auth import require_scopes
from pathres import _proxy_ready, _resolve_media_path
from state import proxy_build as _proxy_guard
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
