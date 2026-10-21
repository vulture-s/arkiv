"""First-run setup wizard routes (roadmap 9.8a).

GET  /api/setup/status        hardware, transcription device, model presence, done flag
POST /api/setup/models/pull   download the missing models (single-flight background job)
GET  /api/setup/models/pull   poll that job
POST /api/setup/complete      mark setup done (or skipped) for this user
POST /api/setup/reset         show the wizard again (Settings → 重新執行設定精靈)

Downloads are model weights from the Ollama registry and Hugging Face — nothing of
the user's footage leaves the machine. Leaf module: imports config/state/setup_check,
never server.
"""
import json
import threading
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import config
import setup_check
from auth import require_scopes
from state import model_pull as _pull
from webguard import _assert_same_site

router = APIRouter()


class PullBody(BaseModel):
    # Names to fetch; omitted = every missing model the status reports.
    models: Optional[List[str]] = None


class CompleteBody(BaseModel):
    skipped: bool = False


def _pull_ollama(item: dict) -> None:
    import requests

    with requests.post("{0}/api/pull".format(config.OLLAMA_URL),
                       json={"model": item["name"], "stream": True}, stream=True, timeout=(5, 600)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("error"):
                raise RuntimeError(ev["error"])
            item["message"] = ev.get("status", "")
            if ev.get("total"):
                item["total"] = ev["total"]
                item["completed"] = ev.get("completed", 0)


def _pull_whisper(item: dict) -> None:
    """snapshot_download gives no progress callback worth wiring, so the poller
    watches the cache directory grow instead — bytes on disk cannot be wrong."""
    from huggingface_hub import snapshot_download

    repo_dir = setup_check.hf_cache_dir() / ("models--" + item["repo"].replace("/", "--"))
    stop = threading.Event()

    def _watch():
        while not stop.wait(1.0):
            item["completed"] = setup_check.dir_bytes(repo_dir)

    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    try:
        snapshot_download(item["repo"])
    finally:
        stop.set()
        item["completed"] = setup_check.dir_bytes(repo_dir)


def _friendly(e: Exception) -> str:
    """One sentence a user can act on; the raw text (request IDs, URLs) goes to the log."""
    raw = "{0}: {1}".format(type(e).__name__, e)
    print("[setup] model pull failed: " + raw[:500], flush=True)
    low = raw.lower()
    if "repositorynotfound" in low or "file does not exist" in low or "not found" in low:
        return "找不到這個模型名稱——檢查設定裡的模型名稱"
    if any(k in low for k in ("connection", "timed out", "timeout", "name resolution", "unreachable")):
        return "連不到下載伺服器——確認網路後再試一次"
    if "no space" in low or "disk" in low:
        return "磁碟空間不足"
    return raw[:120]


def _run(items: List[dict]) -> None:
    try:
        for item in items:
            item["status"] = "downloading"
            try:
                (_pull_whisper if item["kind"] == "whisper" else _pull_ollama)(item)
                item["status"] = "done"
            except Exception as e:  # noqa: BLE001 — one failed model must not stop the rest
                item["status"] = "error"
                item["message"] = _friendly(e)
        failed = [i["name"] for i in items if i["status"] == "error"]
        _pull.progress["message"] = ("有 {0} 個模型下載失敗".format(len(failed)) if failed else "done")
    finally:
        _pull.progress["running"] = False
        _pull.release()


def _missing(status: dict) -> List[dict]:
    out = []
    w = status["whisper"]
    if not w["present"] and w.get("repo"):
        out.append({"kind": "whisper", "name": w["name"], "repo": w["repo"]})
    if status["ollama"]["running"]:
        for m in status["ollama"]["models"]:
            if not m["present"]:
                out.append({"kind": "ollama", "name": m["name"]})
    return out


@router.get("/api/setup/status")
def setup_status(_tok: dict = Depends(require_scopes("projects_read"))):
    st = setup_check.status()
    st["pull"] = dict(_pull.progress)
    return st


@router.post("/api/setup/models/pull")
def setup_models_pull(
    request: Request,
    body: Optional[PullBody] = None,
    _tok: dict = Depends(require_scopes("admin")),
):
    _assert_same_site(request)
    st = setup_check.status()
    items = _missing(st)
    if body and body.models:
        wanted = set(body.models)
        items = [i for i in items if i["name"] in wanted]
    if not items:
        if not st["ollama"]["running"] and any(not m["present"] for m in st["ollama"]["models"]):
            raise HTTPException(409, "Ollama 沒有在執行，無法下載它的模型——先安裝並開啟 Ollama")
        return {"queued": 0, "message": "所有模型都已就緒"}
    if not _pull.acquire():
        raise HTTPException(409, "模型下載已在進行中")
    for i in items:
        i.update(status="queued", completed=0, total=None, message="")
    _pull.reset_progress(running=True, items=items, message="", started=time.strftime("%H:%M:%S"))
    threading.Thread(target=_run, args=(items,), daemon=True).start()
    return {"queued": len(items)}


@router.get("/api/setup/models/pull")
def setup_models_pull_status(_tok: dict = Depends(require_scopes("projects_read"))):
    return dict(_pull.progress)


@router.post("/api/setup/complete")
def setup_complete(
    request: Request,
    body: Optional[CompleteBody] = None,
    _tok: dict = Depends(require_scopes("projects_write")),
):
    _assert_same_site(request)
    return setup_check.mark_completed(skipped=bool(body and body.skipped))


@router.post("/api/setup/reset")
def setup_reset(request: Request, _tok: dict = Depends(require_scopes("projects_write"))):
    _assert_same_site(request)
    setup_check.reset()
    return {"completed": False}
