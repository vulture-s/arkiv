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
import time
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth
import config
import db
import ingest_budget
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
IMAGE_EXTS = mediatypes.IMAGE_EXT
assert VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS == MEDIA_EXTS  # the three must partition MEDIA_EXTS
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
        "image": cat(IMAGE_EXTS),
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
    cur_vision = settings_store.vision_model()
    vision_models = _vision.list_vision_models()
    if cur_vision and cur_vision not in vision_models:
        vision_models = sorted(set(vision_models) | {cur_vision})
    # Phase 9.7 G5③: the dialog's defaults come from the persisted settings
    # (library default), falling back to config. These are genuinely consumed —
    # IngestSetup pre-fills its pickers from them, and the vision model/num_ctx
    # are what an ingest run actually uses (ingest.py reads the same accessors).
    # Read at PROJECT scope: these are the defaults for THIS library, and a
    # bare effective() answered with the global layer no matter what the
    # project row said.
    return {
        "whisper_modes": modes,
        "default_mode": settings_store.transcription_default_mode(),
        "default_language": settings_store.transcription_default_language(),
        "default_recursive": settings_store.ingest_recursive(),
        "vision_model": cur_vision,
        "vision_models": vision_models,
        "vision_num_ctx": settings_store.vision_num_ctx(),
        "languages": _INGEST_LANGUAGES,
        # Source shortcuts. The browser build has no folder picker at all
        # (canPickFolder() needs window.__TAURI__), so on the web UI the only way
        # to reach a long NAS path is to retype it every time.
        "source_presets": settings_store.source_preset_list(),
    }


# ffprobe over a NAS measured 0.13s/file; /api/ingest/scan probes the folder to
# show an ETA and then the run probes THE SAME FILES AGAIN to derive its budget.
# A clip's duration cannot change without its size or mtime changing, so this
# memoises on exactly that. Bounded so a long-lived server cannot grow it without
# limit; the eviction is crude because a wrong eviction only costs one re-probe.
_PROBE_CACHE: dict = {}
_PROBE_CACHE_MAX = 20000


def _probe_key(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (str(path), st.st_size, int(st.st_mtime))


def _probe_durations(paths, max_probe: int = 500):
    """Duration per file, in the order given; None where it could not be read.

    Bounded on two axes so a huge or a wedged folder cannot hang the scan:
    at most `max_probe` files are actually probed, and each ffprobe gets its own
    short timeout. A probe that fails yields None rather than 0 — treating
    "unknown" as "zero seconds" would shrink the budget, and a too-small budget
    kills healthy imports.

    The `max_probe` sample is taken EVENLY across the list, not off the head.
    `stall_seconds` scales the silence threshold by `max(known)`, and whisper is
    silent for the whole decode of a single clip — so the one number that must
    not be missed is the longest clip. Probing the alphabetical first 500 of a
    2000-clip folder makes the 40-minute interview at position 1200 invisible,
    and the stall watchdog then kills a healthy import in the middle of
    transcribing it. An even stride costs nothing and samples the whole folder.
    """
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    def _one(path):
        key = _probe_key(path)
        if key is not None and key in _PROBE_CACHE:
            return _PROBE_CACHE[key]
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace")
            val = float((r.stdout or "").strip())
        except Exception:
            return None  # not cached: a failure can be transient (NAS asleep)
        if key is not None:
            if len(_PROBE_CACHE) >= _PROBE_CACHE_MAX:
                _PROBE_CACHE.clear()
            _PROBE_CACHE[key] = val
        return val

    n = len(paths)
    if not n:
        return []
    if n <= max_probe:
        idxs = list(range(n))
    else:
        stride = n / float(max_probe)
        idxs = sorted({min(n - 1, int(i * stride)) for i in range(max_probe)})
    with ThreadPoolExecutor(max_workers=16) as ex:
        probed = list(ex.map(_one, [paths[i] for i in idxs]))
    out = [None] * n
    for i, d in zip(idxs, probed):
        out[i] = d
    return out


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
    # Probe durations so the caller can be told how long this will take BEFORE it
    # starts, and so the run's budget is derived from the actual material instead
    # of a fixed number. ffprobe over a NAS measured 0.13s/file serially; a small
    # pool brings 200 files to ~8s, which is worth it for an honest ETA.
    pending = [f for f in files if not f["already"]]
    durations = _probe_durations([f["path"] for f in pending])
    for f, d in zip(pending, durations):
        f["duration_s"] = d
    est = ingest_budget.estimate_seconds(durations, n_files=len(pending))
    known = [d for d in durations if d]
    return {
        "total": len(files),
        "new": len(pending),
        "manifest": _build_scan_manifest(files, unsupp),
        "files": files,
        # Advisory only — the UI shows it, nothing is killed on it. What actually
        # bounds the run is `budget_s` (and the stall watchdog, which this cannot see).
        "estimate": {
            "seconds": round(est),
            # 同一個 max_duration 要餵進去，否則 UI 顯示的「上限 X 分」
            # 跟實際執行時 _ingest_limits 算出來的不是同一個數字。
            "budget_s": round(ingest_budget.budget_seconds(
                est, max_duration_s=(max(known) if known else 0))),
            "stall_s": round(ingest_budget.stall_seconds(max(known) if known else 0)),
            "probed": len(known),
            "of": len(pending),
        },
    }


def _unprocessed(paths):
    """The paths ingest.py will actually touch — already-indexed ones dropped.

    Best-effort: if the DB cannot be read we return the input unchanged, because
    an over-wide budget is only as wrong as it already was, whereas raising here
    would take down a request that has nothing to do with budgets.
    """
    try:
        import db as _db
        with _db.get_conn() as conn:
            done = {r[0] for r in conn.execute("SELECT path FROM media")}
    except Exception:
        return list(paths)
    return [p for p in paths if p not in done]


def _unprocessed_first(paths):
    """Order-preserving variant: unprocessed first, processed after. Kept because
    a caller that must not lose entries needs the reordering, not the filter."""
    try:
        import db as _db
        with _db.get_conn() as conn:
            done = {r[0] for r in conn.execute("SELECT path FROM media")}
    except Exception:
        return paths
    return [p for p in paths if p not in done] + [p for p in paths if p in done]


def _ingest_limits(target: Path, limit: int = 0):
    """(stall_seconds, budget_seconds) for this folder, from its own material.

    Probing is best-effort: a folder we cannot read yields the floors, which are
    the old behaviour plus a stall watchdog. Never let the estimate step be able
    to fail the import it is only advising on.
    """
    try:
        paths = []
        for f in sorted(target.rglob("*")):
            if f.is_file() and f.suffix.lower() in MEDIA_EXTS:
                paths.append(str(f))
        # ingest.py SKIPs anything already indexed, in both modes. Budgeting the
        # full listing therefore prices work that will never happen — and it made
        # /api/ingest/scan's comment ("同一個 max_duration 要餵進去，否則 UI 顯示
        # 的『上限 X 分』跟實際執行時算出來的不是同一個數字") false for the
        # limit=0 path the UI actually uses: scan estimates over `pending`, this
        # estimated over everything. Re-importing into a 5000-clip library showed
        # an ETA from the 3 new files and ran on a budget from all 5000.
        pending = _unprocessed(paths)
        if limit and limit > 0:
            # ingest.py --limit N processes the first N *unprocessed* files, so
            # slicing the full listing budgets for the wrong set: a folder whose
            # first 400 clips are already indexed 10-second takes and whose last
            # 100 are 30-minute interviews gets a budget derived from the takes,
            # then spends it on the interviews and is killed mid-run.
            pending = pending[:limit]
        paths = pending
        durations = _probe_durations(paths)
        est = ingest_budget.estimate_seconds(durations, n_files=len(paths))
        known = [d for d in durations if d]
        mx = max(known) if known else 0
        return (ingest_budget.stall_seconds(mx),
                ingest_budget.budget_seconds(est, max_duration_s=mx))
    except Exception:
        return (config.INGEST_STALL_FLOOR_SECONDS, config.INGEST_BUDGET_FLOOR_SECONDS)


def _timeout_detail(e) -> str:
    mins = lambda s: int((s or 0) // 60)
    if getattr(e, "kind", None) == "stall":
        return ("匯入停滯：已 {0} 分鐘沒有任何進度輸出（門檻 {1} 分鐘），"
                "研判卡住，已停止整個處理程序。已完成的素材都已寫進素材庫，"
                "重跑會從中斷處接續。").format(mins(e.idle), mins(e.timeout))
    return ("匯入超過預估時間的上限（{0} 分鐘）並被停止 —— 它當時仍在輸出進度，"
            "所以多半只是估值偏低而非卡住。已完成的素材都已寫進素材庫，"
            "重跑會從中斷處接續。").format(mins(e.timeout))


@router.post("/api/ingest")
def ingest_media(
    body: IngestRequest,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Trigger ingest — runs ingest.py as a subprocess and blocks until it ends.

    ⚠️ Not the UI's path (the SPA uses POST /api/ingest/ws + the /ws/ingest
    stream). This one is for MCP/CLI callers, and it is synchronous in three
    senses at once, for as long as `budget_s` — which is derived from the
    material and was 94 minutes for the 200-clip field run: it holds one anyio
    threadpool worker, one HTTP connection, and the single-flight ingest slot.
    A caller whose own timeout is shorter gets nothing back AND cannot retry
    (409) until the run it can no longer see finishes. Nothing here can detect
    that disconnect — a sync route has no equivalent of the offload stream's
    GeneratorExit. Long imports belong on the WS route; this stays for small,
    scripted ones.
    """
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
        # run_tree_watched, not a single wall-clock cap: the old fixed 1800s could
        # not finish a 200-clip import (measured 94 minutes) yet was still too long
        # to notice a wedged child. Two limits derived from the material itself —
        # `stall` catches "hung", `budget` catches "estimate was wrong". Both kill
        # the whole ingest.py→ffmpeg/whisper tree (fable-audit round-5 #2 / #12).
        import proctree
        stall_s, budget_s = _ingest_limits(target, body.limit)
        result = proctree.run_tree_watched(
            cmd, stall_timeout=stall_s, total_timeout=budget_s, cwd=str(BASE_DIR))
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
    except proctree.TreeTimeout as e:
        # The two kinds need different words: "stalled" means go look at the box,
        # "over budget" means it was probably still working and can be resumed.
        raise HTTPException(504, _timeout_detail(e))
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "匯入逾時")  # audit L11: was 200 + ok:false
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


# ── Upload (HTTP multipart → media-in → auto ingest) ──────────────────────────
# Phase 14 addendum: let MCP clients / browsers push media without manually
# copying into the bind mount. Files land in the ingest source (media-in) and a
# background ingest picks them up. Filenames are constrained to a basename (no
# path traversal) and only known media extensions are accepted.

_UPLOAD_DIR = (
    Path(os.environ.get("ARKIV_UPLOAD_DIR", "")).expanduser()
    if os.environ.get("ARKIV_UPLOAD_DIR")
    else (config.PROJECT_ROOT / "media-in")
)
_UPLOAD_MAX_BYTES = int(os.environ.get("ARKIV_UPLOAD_MAX_MB", "4096")) * 1024 * 1024
# Phase 14 addendum: cap concurrent upload handlers so a burst of large files
# can't exhaust the worker pool / saturate disk. Waiters queue up to
# _UPLOAD_SEM_TIMEOUT seconds for a slot, then get 429. (Background ingest is
# already single-flight via the shared slot in state.py.)
_UPLOAD_MAX_CONCURRENT = max(1, int(os.environ.get("ARKIV_UPLOAD_MAX_CONCURRENT", "3")))
_UPLOAD_SEM_TIMEOUT = float(os.environ.get("ARKIV_UPLOAD_MAX_QUEUE_SEC", "300"))
_upload_sem = asyncio.Semaphore(_UPLOAD_MAX_CONCURRENT)


def _safe_upload_dir() -> Path:
    d = _UPLOAD_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_upload_file(uf: UploadFile, dest: Path, max_bytes: int) -> None:
    """Blocking: stream one UploadFile to dest, enforcing the per-file size cap.
    Runs inside asyncio.to_thread so it never pins the event loop."""
    written = 0
    with dest.open("wb") as out:
        while True:
            chunk = uf.file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    413,
                    f"檔案超過上傳上限（{max_bytes // (1024 * 1024)} MB）：{getattr(uf, 'filename', '?')}",
                )
            out.write(chunk)


@router.post("/api/ingest/upload")
async def ingest_upload(
    files: List[UploadFile] = File(...),
    background_tasks: BackgroundTasks = None,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Accept multipart media uploads and store them under the ingest source
    (media-in). A background ingest of that directory is triggered so the new
    clips become searchable. Concurrency is capped by _upload_sem so a burst of
    large uploads can't exhaust workers/disk; filename is constrained to a
    basename (no path traversal) and only known media extensions are accepted."""
    acquired = False
    try:
        await asyncio.wait_for(_upload_sem.acquire(), timeout=_UPLOAD_SEM_TIMEOUT)
        acquired = True
    except asyncio.TimeoutError:
        raise HTTPException(429, "上傳並發已達上限，請稍後再試")
    try:
        upload_dir = await asyncio.to_thread(_safe_upload_dir)
        saved = []
        renamed = []
        for uf in files:
            raw = uf.filename or ""
            name = PurePosixPath(raw).name
            if not name or name in (".", "..") or "/" in name or "\\" in name:
                raise HTTPException(400, f"非法檔名：{raw}")
            ext = Path(name).suffix.lower()
            if ext not in MEDIA_EXTS:
                raise HTTPException(400, f"不支援的檔案類型：{ext}（僅允許媒體格式）")
            # Collision-safe name: never overwrite an existing file on disk, nor a
            # media row already indexed at this path. A same-name re-upload is
            # renamed (<stem>__<YYYYMMDD-HHMMSS><ext>) so both clips survive instead
            # of the original being clobbered + then SKIPped by ingest (data loss).
            stem = Path(name).stem
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            cand = name
            n = 0
            while True:
                c = upload_dir / cand
                if not c.exists() and not db.path_is_indexed(str(c)):
                    break
                n += 1
                cand = "{0}__{1}{2}{3}".format(
                    stem, ts, ("_" + str(n)) if n > 1 else "", ext
                )
            dest = upload_dir / cand
            # basename guarantees containment, but assert defensively
            if dest.resolve() != (upload_dir.resolve() / cand):
                raise HTTPException(400, f"非法路徑：{raw}")
            await asyncio.to_thread(_save_upload_file, uf, dest, _UPLOAD_MAX_BYTES)
            saved.append(cand)
            if cand != name:
                renamed.append({"from": name, "to": cand})
        if not saved:
            raise HTTPException(400, "沒有收到任何檔案")
        if background_tasks is not None:
            background_tasks.add_task(_bg_ingest, str(upload_dir))
        return JSONResponse(
            status_code=202,
            content={
                "ok": True,
                "saved": saved,
                "renamed": renamed,
                "upload_dir": str(upload_dir),
                "ingest": "triggered" if background_tasks is not None else "skipped",
            },
        )
    finally:
        if acquired:
            _upload_sem.release()


def _bg_ingest(dir_path: str) -> None:
    """Run ingest.py over the upload directory in the background, serialised
    through the shared single-flight slot (audit H3 — no concurrent whisper)."""
    import subprocess, sys, proctree

    deadline = time.time() + 1800
    while time.time() < deadline:
        if _acquire_ingest_slot():
            try:
                # Same two limits as the interactive route. This path swallows
                # its exceptions (it is a background task with nobody to tell),
                # so without a stall watchdog a wedged upload-ingest would hold
                # the single-flight slot for the full budget and block every
                # later import with a 409.
                stall_s, budget_s = _ingest_limits(Path(dir_path))
                proctree.run_tree_watched(
                    [sys.executable, str(BASE_DIR / "ingest.py"), "--dir", dir_path],
                    stall_timeout=stall_s,
                    total_timeout=budget_s,
                    cwd=str(BASE_DIR),
                )
            except Exception:
                pass
            finally:
                _release_ingest_slot()
            return
        time.sleep(5)


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


def _kill_ingest_tree(proc) -> None:
    """Kill the ingest subprocess AND its descendants (ffmpeg / whisper / ollama
    client). `proc.kill()` alone leaves them running and holding the GPU.

    Delegates to proctree._kill_tree rather than reimplementing it. This *was* a
    third hand-rolled copy whose non-POSIX branch fell straight through to
    proc.kill() — no taskkill /F /T — so on Windows the watchdog reaped ingest.py
    and orphaned its ffmpeg/whisper grandchildren. proctree's own docstring
    predicted exactly this ("the Windows branch in particular is easy to fix on
    one side only"); the drift arrived in the same change that wrote it.
    """
    try:
        import proctree
        proctree._kill_tree(proc)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass


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
        # Own session so the watchdog below can kill the WHOLE tree. Without it
        # `proc.kill()` reaps ingest.py and orphans its ffmpeg/whisper children —
        # the exact bug run_tree exists to prevent (fable-audit round-5 #2 / #12),
        # which this path never had because it never had a timeout at all.
        start_new_session=(os.name == "posix"),
        # brick 3: drive the structured per-stage progress protocol (own-line JSON
        # events) instead of parsing the compact inline `>probe` human markers.
        env={**os.environ, "ARKIV_STAGE_EVENTS": "1"},
        # audit M3: a single stdout line beyond the default 64KB reader limit
        # raises inside the read loop; give pathological log lines 1MB headroom.
        limit=2 ** 20,
    )

    # Two limits derived from the material (see ingest_budget). This path had NO
    # timeout at all: a wedged child produced no EOF, so the read loop waited
    # forever, the UI sat on its last event, and the single-flight slot stayed held.
    stall_s, budget_s = await asyncio.to_thread(_ingest_limits, target, limit)
    beat = {"at": time.monotonic()}
    halted = {"reason": None}

    async def _watchdog():
        started = time.monotonic()
        while True:
            await asyncio.sleep(1.0)
            if proc.returncode is not None:
                return
            now = time.monotonic()
            idle, elapsed = now - beat["at"], now - started
            if idle > stall_s:
                halted["reason"] = "stall"
            elif elapsed > budget_s:
                halted["reason"] = "budget"
            else:
                continue
            # Re-check immediately before acting: the broadcast below is an
            # await, and a run that finishes during it would otherwise be
            # reported as HALTED *and* have its already-reaped pid passed to
            # killpg — which on pid reuse signals an unrelated process group.
            # The final phase (embedding / index rebuild) is silent, so this is
            # precisely when a healthy run looks stalled.
            if proc.returncode is not None:
                halted.pop("reason", None)
                return
            _kill_ingest_tree(proc)
            await ingest_ws.broadcast({
                "type": "halted", "reason": halted["reason"],
                "idle_s": round(idle), "elapsed_s": round(elapsed),
                "stall_s": round(stall_s), "budget_s": round(budget_s),
            })
            return

    watchdog = asyncio.create_task(_watchdog())

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
            # Any byte counts as liveness. ingest.py flushes a marker at every
            # stage, so silence really is "not progressing", not "buffered".
            beat["at"] = time.monotonic()
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
        watchdog.cancel()
        if proc.returncode is None:  # audit M3: loop exited abnormally — reap child
            _kill_ingest_tree(proc)
            await proc.wait()
    # Derive failed from the observed total (not `limit`, which is 0 for "all"
    # and overstates when it exceeds the real file count) and surface the exit
    # code so a nonzero ingest result (e.g. vision halt) is visible to the UI.
    failed = max(0, last_total - ok - skipped)
    rc = proc.returncode or 0
    print(f"[ingest-ws] COMPLETE ok={ok} skipped={skipped} failed={failed} rc={rc}", flush=True)

    await ingest_ws.broadcast({
        "type": "complete", "ok": ok, "skipped": skipped, "failed": failed,
        "returncode": rc,
        # Carried on `complete` as well as the earlier `halted`: a client that
        # connected late, or missed the event, must still learn this run was cut
        # short rather than read a partial result as a finished one.
        "halted": halted["reason"],
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
