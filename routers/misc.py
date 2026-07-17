"""Leftover singleton routes (R5-25 / round-5 #51 router split).

The handlers that don't form a larger group but still leave server.py as a pure
app shell: /api/stream (range-friendly playback, serves the H.264 proxy when the
source codec is browser-incompatible), /api/embed/rebuild (single-flight semantic
index rebuild), /api/open-file (OS-level open/reveal — videos_write gated), and
the unauthenticated /api/client-log diagnostics sink (+ its _log_safe sanitiser).

_proxy_ready + _resolve_media_path come from pathres; the embed single-flight
guard + _rebuild_embeddings worker from state (the ONE shared instance);
_assert_same_site from webguard. No server import, no cycle.
"""
import mimetypes
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import codec
import config
import db
import mediatypes
from auth import require_scopes
from pathres import _proxy_ready, _resolve_media_path
from state import _rebuild_embeddings, embed_rebuild as _embed_guard
from webguard import _assert_same_site

router = APIRouter()


def _log_safe(text: str, limit: int) -> str:
    """Strip control chars (newlines, ANSI/terminal escapes) and truncate, so a
    value printed to the server terminal/log can't forge lines or fill disk."""
    if not text:
        return ""
    cleaned = "".join(c for c in text if c == " " or (0x20 <= ord(c) < 0x7F) or ord(c) >= 0x80)
    return cleaned[:limit]


class OpenFileRequest(BaseModel):  # audit M22: malformed JSON → clean 422, not a raw 500
    path: str
    reveal: bool = False


class ClientLogRequest(BaseModel):
    # audit M22: the model's job is turning malformed JSON into a 422 instead of
    # a raw 500. Fields stay Any (the WebView occasionally logs non-string
    # payloads); the handler stringifies + sanitizes as before.
    level: Any = "info"
    msg: Any = ""


@router.get("/api/stream/{media_id}")
def stream_media(media_id: int, _tok: dict = Depends(require_scopes("videos_read"))):
    """Stream a media file with range request support for seeking.
    Serves H.264 proxy if available (for browser-incompatible codecs like ProRes/HEVC).
    """
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到媒體")
    # Proxy filename is hash-scoped by absolute source path so that a
    # proxies/ directory copied between installations cannot serve another
    # user's content under the same media_id.
    resolved_src = _resolve_media_path(rec["path"])
    proxy_path = config.proxy_path_for(media_id, resolved_src)
    if _proxy_ready(proxy_path):
        return FileResponse(
            path=str(proxy_path),
            media_type="video/mp4",
            filename=Path(resolved_src).stem + "_proxy.mp4",
        )
    file_path = Path(resolved_src)
    if not file_path.exists():
        raise HTTPException(404, "找不到檔案")
    # Only serve known media extensions
    if file_path.suffix.lower() not in mediatypes.MEDIA_EXT:
        raise HTTPException(403, "不是媒體檔案")

    # Phase 7.7g: HEVC/ProRes 沒對應 proxy 時不要 silently 送原檔（Chrome/WKWebView
    # 都播不出來，使用者只看到「無法播放」），改回 409 + JSON，前端可 surface
    # 「需先建 proxy」的引導 + POST /api/proxy/build 觸發背景生成。
    # tri-state: NEEDED → 409；NOT_NEEDED / UNKNOWN（ffprobe 失敗、binary 缺、
    # NAS unreachable）→ fall through，維持送原檔的舊 fallback 行為。
    # audit M24: use the codec persisted at ingest instead of re-running ffprobe
    # on EVERY playback; only probe when the column is NULL (legacy rows) and
    # backfill so the next play is probe-free. None (probe failed / audio-only)
    # keeps the UNKNOWN fall-through behavior.
    stored_codec = (rec.get("codec") or "").strip().lower() or None
    if stored_codec is None:
        stored_codec = codec.probe_codec(str(file_path))
        if stored_codec:
            try:
                with db.get_conn() as conn:
                    conn.execute("UPDATE media SET codec=? WHERE id=?", (stored_codec, media_id))
            except Exception:
                pass  # backfill is best-effort; playback must not fail on it
    if stored_codec and stored_codec in codec.PROXY_CODECS:
        return JSONResponse(
            status_code=409,
            content={
                "need_proxy": True,
                "media_id": media_id,
                "filename": rec.get("filename"),
                "reason": "browser-incompatible codec (HEVC/ProRes); proxy required for playback",
                "hint": "POST /api/proxy/build to queue proxy generation",
            },
        )

    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        mime = "video/mp4"
    return FileResponse(
        path=str(file_path),
        media_type=mime,
        filename=file_path.name,
    )


@router.post("/api/embed/rebuild")
def embed_rebuild(request: Request, background_tasks: BackgroundTasks, _tok: dict = Depends(require_scopes("ingest_write"))):
    """Drop + rebuild the ChromaDB semantic index from all media.
    Wired to 進階設定 → 搜尋引擎 → 「重建向量索引」button."""
    _assert_same_site(request)  # audit M14
    with db.get_conn() as conn:
        total = conn.execute("SELECT count(*) FROM media").fetchone()[0]
    if not total:
        return {"message": "尚無素材可建立索引", "queued": 0}
    if not _embed_guard.acquire():  # audit M8: refuse concurrent rebuilds
        raise HTTPException(409, "向量索引重建已在進行中，請稍候")
    background_tasks.add_task(_rebuild_embeddings)
    return {"message": f"開始重建向量索引（{total} 筆素材，背景執行）", "queued": total}


@router.post("/api/open-file")
def open_file(
    body: OpenFileRequest,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Open file in OS default app or reveal in file manager. Only allows known media files from DB.

    Requires videos_write: launching an OS app / revealing a file is a privileged
    side effect on the host. Loopback is token-free (full scope); a remote
    read-only browse token is correctly refused.
    """
    import subprocess, platform
    file_path = body.path
    reveal = body.reveal
    # Validate: only allow paths that exist in our database
    if not db.is_processed(file_path):
        raise HTTPException(403, "只能開啟已索引的媒體檔案")
    resolved_path = _resolve_media_path(file_path)
    if not Path(resolved_path).exists():
        raise HTTPException(404, "找不到檔案")
    system = platform.system()
    if reveal:
        if system == "Darwin":
            subprocess.Popen(["open", "-R", resolved_path])
        elif system == "Windows":
            subprocess.Popen(["explorer", "/select,", resolved_path])
        else:
            subprocess.Popen(["xdg-open", str(Path(resolved_path).parent)])
    else:
        if system == "Darwin":
            subprocess.Popen(["open", resolved_path])
        elif system == "Windows":
            os.startfile(resolved_path)
        else:
            subprocess.Popen(["xdg-open", resolved_path])
    return {"ok": True}


@router.post("/api/client-log")
def client_log(body: ClientLogRequest):
    """Receive client-side logs (errors, info) and print to server terminal.

    Stays unauthenticated (the WebView logs diagnostics, sometimes before a token
    is wired), but the attacker-controlled fields are sanitized + truncated:
    control chars (newlines / ANSI terminal escapes) are stripped so a remote
    caller can't forge log lines or corrupt the operator's terminal, and lengths
    are capped so it can't be used to fill a redirected logfile.
    """
    level = _log_safe(str(body.level if body.level is not None else "info").upper(), 16)
    msg = _log_safe(str(body.msg if body.msg is not None else ""), 2000)
    print(f"[WebView {level}] {msg}", flush=True)
    return {"ok": True}


@router.get("/api/version")
def api_version():
    """Unauthenticated: identify the build. `curl <host>/api/version`."""
    return {"version": config.VERSION}


@router.get("/api/health")
def api_health():
    """Unauthenticated self-diagnostic — a user (or support) curls this ONE URL to
    see if the runtime deps are present, without a terminal or a token. Returns
    booleans + model names only (NO absolute paths) so it's safe to expose. Mirrors
    the checks in python health.py. The desktop app / a helper can hit it directly."""
    import shutil

    out = {"version": config.VERSION, "ready": True, "checks": {}}

    def _mark(name, ok, detail=""):
        out["checks"][name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            out["ready"] = False

    # external binaries (ffmpeg/ffprobe required; exiftool optional)
    _mark("ffmpeg", shutil.which(config.FFMPEG_PATH) is not None)
    _mark("ffprobe", shutil.which(config.FFPROBE_PATH) is not None)
    exif = getattr(config, "EXIFTOOL_PATH", "") or "exiftool"
    out["checks"]["exiftool"] = {  # optional — doesn't flip ready
        "ok": shutil.which(exif) is not None,
        "detail": "optional",
    }

    # ollama reachable + the three configured models pulled
    try:
        import requests

        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
        r.raise_for_status()
        models = [m.get("name", "") for m in r.json().get("models", [])]
        _mark("ollama", True, f"{len(models)} models")
        for label, want in (
            ("embed", config.OLLAMA_EMBED_MODEL),
            ("vision", config.OLLAMA_VISION_MODEL),
            ("chat", config.OLLAMA_CHAT_MODEL),
        ):
            base = want.split(":")[0]
            present = any(base in m for m in models)
            _mark(f"model:{want}", present, "" if present else f"ollama pull {want}")
    except Exception:
        _mark("ollama", False, "unreachable — is `ollama serve` running?")

    return JSONResponse(out, status_code=200 if out["ready"] else 503)
