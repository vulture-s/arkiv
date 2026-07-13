"""Ingest + WebSocket-progress routes (R5-25 / round-5 #51 router split).

The final peel: the /api/ingest family (engines list, scan, run, single-clip
reingest) plus the real-time WS progress channel (/ws/ingest + /api/ingest/ws).
All ingest entrypoints (REST / reingest / WS) serialize through the ONE shared
single-flight slot + broadcaster in state.py (_acquire_ingest_slot /
_release_ingest_slot / ingest_ws) — the audit-H3 double-whisper-OOM guard — which
this module imports as live instances, never recreates. `BASE_DIR` (config)
replaces server.ROOT for locating ingest.py. Imports auth + config + db +
mediatypes + pathres + reqopts + state + webguard directly — no server import,
no cycle.
"""
import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth
import config
import db
import mediatypes
import settings as settings_store
from auth import require_scopes
from config import BASE_DIR
from pathres import _display_path, _resolve_media_path
from reqopts import _INGEST_LANGUAGES, _ingest_cmd_opts
from state import _acquire_ingest_slot, _release_ingest_slot, ingest_ws
from webguard import _ALLOWED_ORIGINS, _assert_ingest_path_safe

router = APIRouter()


# ── Ingest ────────────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    path: str
    limit: int = 0
    # ingest.py engine options, surfaced so the redesign's ingest setup dialog
    # (docs/design/redesign-2026 op-01) has a real backend to bind to. Each
    # defaults to the pre-existing behaviour, so existing callers are unchanged.
    skip_vision: bool = False
    refresh: bool = False
    recursive: bool = False
    max_failures: int = 0
    skip_failed: bool = False
    no_embed: bool = False
    # brick 4: transcription engine knobs. whisper_guard = quality preset 0-4
    # (None = default); language = forced whisper code (zh/en/ja/ko, None = auto).
    whisper_guard: Optional[int] = None
    language: Optional[str] = None


# _INGEST_LANGUAGES / _INGEST_LANGUAGE_CODES / _ingest_cmd_opts moved to
# reqopts.py (R5-25 / #51), imported above.


class ScanRequest(BaseModel):
    path: str

MEDIA_EXTS = mediatypes.MEDIA_EXT
VIDEO_EXTS = mediatypes.VIDEO_EXT
AUDIO_EXTS = mediatypes.AUDIO_EXT
assert VIDEO_EXTS | AUDIO_EXTS == MEDIA_EXTS  # the two must partition MEDIA_EXTS
# camera raw / stills the ingest pipeline does not process — surfaced in the scan
# manifest as "skipped" so the redesign's setup dialog (op-01) can show what will
# not be ingested (e.g. a card of .mov clips + .crw stills).
UNSUPPORTED_STILL_EXTS = {".crw", ".cr2", ".cr3", ".arw", ".nef", ".dng",
                          ".raf", ".orf", ".rw2", ".heic", ".heif", ".tif", ".tiff"}


def _build_scan_manifest(files: list, unsupp: dict) -> dict:
    """Aggregate a scanned file list into op-01's MANIFEST panel: counts + sizes
    by category (video / audio) plus the unsupported-stills skip count."""
    def cat(exts: set) -> dict:
        sub = [f for f in files if Path(f["name"]).suffix.lower() in exts]
        return {"count": len(sub), "size_mb": round(sum(f["size_mb"] for f in sub), 1)}
    return {
        "video": cat(VIDEO_EXTS),
        "audio": cat(AUDIO_EXTS),
        "unsupported": {"count": sum(unsupp.values()), "by_ext": dict(sorted(unsupp.items()))},
        "total_size_mb": round(sum(f["size_mb"] for f in files), 1),
    }

# _allowed_ingest_roots / _assert_ingest_path_safe moved to webguard.py
# (R5-25 / #51) and are imported above.


@router.get("/api/ingest/engines")
def ingest_engines(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """brick 4 — real options for the setup dialog's transcription pickers, so the
    UI is driven by backend truth (config.WHISPER_GUARD_LAYERS) instead of a
    hardcoded list that would drift. whisper_modes = quality presets 0-4;
    languages = the curated forced-language set (None/omit = auto-detect)."""
    modes = [
        {"mode": k, "name": config.WHISPER_GUARD_LAYERS[k].get("name", str(k))}
        for k in sorted(config.WHISPER_GUARD_LAYERS)
    ]
    # brick 4b: the vision picker's options come from the installed Ollama models
    # (queried live, vision-capable only), so the setup dialog is driven by
    # backend truth instead of a hardcoded list. Empty when Ollama is unreachable
    # → the UI falls back to a free-text field. Always include the current
    # effective model so the active selection shows even if detection missed it
    # (or Ollama is down).
    import vision as _vision
    cur_vision = settings_store.effective("vision.model")
    vision_models = _vision.list_vision_models()
    if cur_vision and cur_vision not in vision_models:
        vision_models = sorted(set(vision_models) | {cur_vision})
    # Phase 9.7 G5③: the dialog's defaults come from the persisted settings
    # (library default), falling back to config. These are genuinely consumed —
    # IngestSetup pre-fills its pickers from them, and the vision model/num_ctx
    # are what an ingest run actually uses (ingest.py reads settings.effective).
    return {
        "whisper_modes": modes,
        "default_mode": settings_store.effective("transcription.default_mode"),
        "default_language": settings_store.effective("transcription.default_language"),
        "default_recursive": settings_store.effective("ingest.recursive"),
        "vision_model": cur_vision,
        "vision_models": vision_models,
        "vision_num_ctx": settings_store.effective("vision.num_ctx"),
        "languages": _INGEST_LANGUAGES,
    }


@router.post("/api/ingest/scan")
def scan_media(
    body: ScanRequest,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Quick scan — return file list without processing."""
    target = Path(body.path).expanduser().resolve()
    if not target.is_dir():
        raise HTTPException(400, "路徑不是有效的目錄")
    _assert_ingest_path_safe(target)
    files = []
    unsupp: dict = {}  # ext -> count, for the MANIFEST "skipped" line
    for f in sorted(target.rglob("*")):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in MEDIA_EXTS:
            already = db.is_processed(str(f)) if hasattr(db, 'is_processed') else False
            files.append({"name": f.name, "size_mb": round(f.stat().st_size / 1048576, 1), "path": str(f), "already": already})
        elif ext in UNSUPPORTED_STILL_EXTS:
            unsupp[ext] = unsupp.get(ext, 0) + 1
    return {
        "total": len(files),
        "new": sum(1 for f in files if not f["already"]),
        "manifest": _build_scan_manifest(files, unsupp),
        "files": files,
    }

@router.post("/api/ingest")
def ingest_media(
    body: IngestRequest,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Trigger ingest from the web UI — runs ingest.py as subprocess."""
    import subprocess, sys
    target = Path(body.path).expanduser().resolve()
    if not target.is_dir():
        raise HTTPException(400, "路徑不是有效的目錄")
    _assert_ingest_path_safe(target)
    cmd = [sys.executable, str(BASE_DIR / "ingest.py"), "--dir", str(target)]
    if body.limit > 0:
        cmd += ["--limit", str(body.limit)]
    cmd += _ingest_cmd_opts(body)
    if not _acquire_ingest_slot():  # audit H3
        raise HTTPException(409, "已有匯入任務進行中，請稍候")
    try:
        # run_tree, not subprocess.run: on timeout the whole ingest.py→ffmpeg/whisper
        # tree is killed, not just the direct child (fable-audit round-5 #2 / #12).
        import proctree
        result = proctree.run_tree(cmd, timeout=1800, cwd=str(BASE_DIR))
        payload = {
            "ok": result.returncode == 0,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
        }
        if result.returncode != 0:
            # audit L11: failures used to come back HTTP 200 + ok:false — keep
            # the body shape for the UI but surface the failure as a 5xx so
            # status-code monitors see it.
            return JSONResponse(status_code=500, content=payload)
        return payload
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "匯入逾時（>30 分鐘）")  # audit L11: was 200 + ok:false
    finally:
        _release_ingest_slot()


@router.post("/api/media/{media_id}/reingest")
def reingest_media(
    media_id: int,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Re-run full ingest pipeline: probe + whisper + thumbnail + llava + embed."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    media_path = _resolve_media_path(rec.get("path", ""))
    if not Path(media_path).exists():
        # fable-audit 2026-07-12: don't leak the resolved absolute path in the
        # error body — surface the PROJECT_ROOT-relative/basename form (Phase 16.2).
        raise HTTPException(400, f"找不到媒體檔案：{_display_path(rec.get('path') or '')}")
    # G2/G7: a fresh ingest re-runs Whisper and overwrites media.transcript — which
    # would silently destroy a hand-corrected transcript (e.g. via the 9.6b
    # correction dictionary). Snapshot the current transcript into the per-language
    # archive first, so it survives and can be reactivated. (This is the real core
    # of the retracted "conflict merge UI" gap — non-destructive + restorable, the
    # arkiv RP-4 way, instead of asking the user to merge field-by-field.)
    if (rec.get("transcript") or "").strip() and rec.get("lang"):
        db.upsert_transcript(
            media_id, rec["lang"], rec["transcript"],
            rec.get("segments_json"), rec.get("words_json"),
        )
    import subprocess, sys, proctree
    if not _acquire_ingest_slot():  # audit H3 — don't run concurrently with another ingest
        raise HTTPException(409, "已有匯入任務進行中，請稍候")
    try:
        # Single-file mode (ingest.py handles a file path as --dir). The old
        # `--dir <parent> --limit 1` re-processed the alphabetically-first file of
        # the folder, not this media — silently refreshing the WRONG row (audit H4).
        # run_tree kills the whole tree on timeout (round-5 #2 / #12), so an orphaned
        # ffmpeg can't keep writing after the 600s cap with the slot released.
        result = proctree.run_tree(
            [sys.executable, str(BASE_DIR / "ingest.py"), "--dir", media_path, "--refresh"],
            timeout=600, cwd=str(BASE_DIR),
        )
        payload = {
            "ok": result.returncode == 0,
            "stdout": result.stdout[-1000:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
        }
        if result.returncode != 0:
            # audit L11: same error-schema unification as /api/ingest — failure
            # is a 5xx with the diagnostic body, not 200 + ok:false.
            return JSONResponse(status_code=500, content=payload)
        return payload
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "重新處理逾時（>10 分鐘）")  # audit L11
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        _release_ingest_slot()


# ── WebSocket: Ingest Progress ───────────────────────────────────────────

def _ws_authorized(ws: WebSocket, scope: str) -> bool:
    """Authorize a WebSocket handshake. `require_scopes` (a Request-typed Depends)
    does NOT apply to @app.websocket routes, so this is enforced manually:
    - Origin check (CSWSH): a browser always sends Origin; reject cross-site so a
      malicious page can't open ws:// to a loopback-trusted instance.
    - same loopback-trust rule as HTTP (loopback peer + no forwarding header), else
    - a `?token=` with the required scope (a browser ws can't set headers)."""
    # CSWSH guard. A browser always sends Origin; accept it if it's same-origin
    # (its authority == the request Host — the normal case for ANY deployment
    # host/port, incl. remote + HTTPS reverse proxy) or in the static dev/Tauri
    # allowlist. A cross-site page (different authority) is rejected. Non-browser
    # clients (no Origin) fall through to token auth.
    origin = ws.headers.get("origin")
    if origin is not None:
        origin_authority = origin.split("://", 1)[-1]
        host_header = ws.headers.get("host", "")
        if origin_authority != host_header and origin not in _ALLOWED_ORIGINS:
            return False
    host = ws.client.host if ws.client is not None else ""
    if auth._trust_loopback() and host in auth._LOOPBACK_HOSTS and not auth._looks_proxied(ws):
        return True
    try:
        tok = auth.resolve_raw_token((ws.query_params.get("token") or "").strip(), host)
    except Exception:
        return False
    return scope in tok.get("scopes", ())


@router.websocket("/ws/ingest")
async def ws_ingest(ws: WebSocket):
    """WebSocket endpoint for real-time ingest progress updates.

    Previously accepted ANY client (the HTTP scope-gate doesn't reach websocket
    routes) → any LAN/tailnet client, or a malicious browser page (CSWSH), could
    connect and stream every ingest's filenames. Now gated: Origin + ingest_write.
    """
    if not _ws_authorized(ws, "ingest_write"):
        await ws.close(code=1008)  # policy violation
        return
    if not await ingest_ws.connect(ws):  # may refuse over the connection cap
        return
    try:
        while True:
            await ws.receive_text()  # keep alive, client can send pings
    except WebSocketDisconnect:
        ingest_ws.disconnect(ws)


# Tracks the single in-flight WS ingest. asyncio.create_task references must be
# held or the task can be GC'd mid-run and its exceptions silently dropped; the
# flag also serializes ingests so concurrent runs don't hammer the same SQLite DB.
_ingest_ws_tasks: set = set()
def _on_ingest_ws_done(task: "asyncio.Task") -> None:
    _ingest_ws_tasks.discard(task)
    _release_ingest_slot()  # audit H3 — shared single-flight guard
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        import traceback
        print(
            "[ingest-ws] task crashed: "
            + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            flush=True,
        )


async def _run_ingest_with_ws(target: Path, limit: int, opts: Optional[list] = None):
    """Run ingest as a single subprocess, parse stdout for progress."""
    import re, sys

    cmd = [sys.executable, str(BASE_DIR / "ingest.py"), "--dir", str(target)]
    if limit > 0:
        cmd += ["--limit", str(limit)]
    if opts:
        cmd += opts

    await ingest_ws.broadcast({"type": "start", "total": limit or 0})

    # stderr merged into stdout (STDOUT) rather than a separate PIPE: ingest.py is
    # log-heavy, and an unread stderr=PIPE fills its ~64KB OS buffer, blocks the
    # child's write, stalls the stdout read loop, and `proc.wait()` hangs forever.
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, cwd=str(BASE_DIR),
        # brick 3: drive the structured per-stage progress protocol (own-line JSON
        # events) instead of parsing the compact inline `>probe` human markers.
        env={**os.environ, "ARKIV_STAGE_EVENTS": "1"},
        # audit M3: a single stdout line beyond the default 64KB reader limit
        # raises inside the read loop; give pathological log lines 1MB headroom.
        limit=2 ** 20,
    )

    ok, skipped, failed = 0, 0, 0
    last_total = limit or 0
    # brick 3 structured-protocol state: the in-flight file (so a stage event can
    # be attributed) + running per-stage tallies (PROBED/TRANSCRIBED/… aggregate).
    cur_idx, cur_total, cur_file = 0, 0, ""
    stage_counts: dict = {}
    # Filenames can contain spaces — match non-greedily up to the trailing " >"
    # / " ...[OK]" marker instead of \S+ (which truncated at the first space).
    file_re = re.compile(r"\[(\d+)/(\d+)\]\s+(SKIP\s+)?(.+?)\s+>")
    done_re = re.compile(r"\[(\d+)/(\d+)\]\s+(.+?)\s+.+\[OK\]")

    # audit M3: if the read loop dies (oversized line, task cancel, broadcast
    # error) nobody drains stdout — the child wedges forever on a full pipe
    # while the done-callback frees the single-flight slot, allowing a second
    # concurrent ingest. Always kill the subprocess on the way out.
    try:
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            print(f"[ingest-ws] {text}", flush=True)

            # Structured per-stage protocol (brick 3): ingest.py emits one JSON
            # event per line behind the __ARKIV__ sentinel. This fully replaces the
            # human-line regexes for normal files; SKIP lines still flow through
            # file_re below. cur_* tracks the in-flight file so a bare stage event
            # can be attributed to it.
            if text.startswith("__ARKIV__ "):
                try:
                    ev = json.loads(text[len("__ARKIV__ "):])
                except Exception:
                    ev = None
                if not isinstance(ev, dict):
                    continue
                t, status = ev.get("t"), ev.get("status")
                if t == "file" and status == "start":
                    cur_idx, cur_total, cur_file = int(ev.get("index", 0)), int(ev.get("total", 0)), ev.get("file", "")
                    last_total = max(last_total, cur_total)
                    await ingest_ws.broadcast({
                        "type": "file", "index": cur_idx, "total": cur_total,
                        "filename": cur_file, "status": "transcribing",
                    })
                elif t == "stage":
                    st = ev.get("stage", "")
                    stage_counts[st] = stage_counts.get(st, 0) + 1
                    await ingest_ws.broadcast({
                        "type": "stage", "stage": st,
                        "index": cur_idx, "total": cur_total,
                        "filename": ev.get("file") or cur_file,
                        "counts": dict(stage_counts),
                    })
                elif t == "file" and status == "phase1_done":
                    ok += 1
                    last_total = max(last_total, int(ev.get("total", cur_total)))
                    await ingest_ws.broadcast({
                        "type": "file", "index": int(ev.get("index", cur_idx)),
                        "total": int(ev.get("total", cur_total)),
                        "filename": ev.get("file", cur_file), "status": "done",
                    })
                continue  # structured line fully handled — skip the human regexes

            # Parse progress lines like "[1/3] FX30.5365.MP4 >probe >whisper..."
            m = file_re.match(text)
            if m:
                idx, total, skip, fname = m.group(1), m.group(2), m.group(3), m.group(4)
                last_total = max(last_total, int(total))
                if skip:
                    skipped += 1
                    await ingest_ws.broadcast({
                        "type": "file", "index": int(idx), "total": int(total),
                        "filename": fname.strip(), "status": "skipped"
                    })
                else:
                    await ingest_ws.broadcast({
                        "type": "file", "index": int(idx), "total": int(total),
                        "filename": fname.strip(), "status": "transcribing"
                    })

            # Parse completion "[OK]"
            d = done_re.match(text)
            if d:
                ok += 1
                last_total = max(last_total, int(d.group(2)))
                await ingest_ws.broadcast({
                    "type": "file", "index": int(d.group(1)), "total": int(d.group(2)),
                    "filename": d.group(3).strip(), "status": "done"
                })

            # Parse "Found N media files"
            if text.startswith("Found "):
                fm = re.search(r"Processing (\d+)", text)
                if fm:
                    last_total = max(last_total, int(fm.group(1)))
                    await ingest_ws.broadcast({"type": "start", "total": int(fm.group(1))})

        await proc.wait()
    finally:
        if proc.returncode is None:  # audit M3: loop exited abnormally — reap child
            proc.kill()
            await proc.wait()
    # Derive failed from the observed total (not `limit`, which is 0 for "all"
    # and overstates when it exceeds the real file count) and surface the exit
    # code so a nonzero ingest result (e.g. vision halt) is visible to the UI.
    failed = max(0, last_total - ok - skipped)
    rc = proc.returncode or 0
    print(f"[ingest-ws] COMPLETE ok={ok} skipped={skipped} failed={failed} rc={rc}", flush=True)

    await ingest_ws.broadcast({
        "type": "complete", "ok": ok, "skipped": skipped, "failed": failed,
        "returncode": rc
    })


@router.post("/api/ingest/ws")
async def ingest_media_ws(
    body: IngestRequest,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Trigger ingest with WebSocket progress broadcasting.

    Mirrors the /api/ingest twin's gates: ingest_write scope + resolve() +
    _assert_ingest_path_safe. Previously this endpoint had NEITHER, so any client
    could drive the ingest pipeline over an arbitrary directory (Codex-class
    finding from the overnight audit).
    """
    target = Path(body.path).expanduser().resolve()
    if not target.is_dir():
        raise HTTPException(400, f"路徑不是有效的目錄：{body.path}")
    _assert_ingest_path_safe(target)
    if not _acquire_ingest_slot():  # audit H3 — shared single-flight with REST ingest/reingest
        raise HTTPException(409, "已有匯入進行中，請等待完成後再試")
    task = asyncio.create_task(_run_ingest_with_ws(target, body.limit, _ingest_cmd_opts(body)))
    _ingest_ws_tasks.add(task)
    task.add_done_callback(_on_ingest_ws_done)
    return {"ok": True, "message": "已開始匯入 — 連線 /ws/ingest 取得進度"}
