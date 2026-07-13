"""Bins (media collections) routes (R5-25 / round-5 #51 router split).

The /api/bins group: CRUD over bins, add/remove items, and copy-into-project. The
four body models, the detail-payload builder, and the verified-copy primitives
(_unique_dest / _copy_clip_verified — fable-audit critical #3: never overwrite a
same-basename clip, never delete the source) are bins-local and move here.

`copy_bin` streams ndjson while spawning an ingest.py subprocess; it shares the H3
single-flight ingest slot with the ingest routes — imported from `state` (the same
process-wide singleton), never re-created. `BASE_DIR` (from config) replaces
server.py's ROOT for locating ingest.py. Imports auth/bins/projects/offload/state/
config/pathres — no server import, no cycle.
"""
import os
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

import bins as bins_store
import projects as project_registry
from auth import require_scopes
from config import BASE_DIR
from pathres import _basename_safe
from state import _acquire_ingest_slot, _release_ingest_slot

router = APIRouter()


class BinCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _clean_bin_name(cls, v: str) -> str:
        cleaned = " ".join(
            "".join(c for c in (v or "") if c == " " or (ord(c) >= 0x20 and c != "\x7f")).split()
        )
        if not cleaned:
            raise ValueError("bin name must not be empty")
        if len(cleaned) > 100:
            raise ValueError("bin name too long (max 100)")
        return cleaned


class BinItemRef(BaseModel):
    project_name: str
    media_id: str
    filename: Optional[str] = None


class BinAddItems(BaseModel):
    # Accept one ref or a batch — the frontend adds a multi-selection at once.
    items: List[BinItemRef]


class BinCopyRequest(BaseModel):
    # dest = an existing registered project name, OR (create_new) a filesystem path
    # for a brand-new project. mode: 'reference' indexes the original files in place
    # (no bytes moved — 原檔不動); 'copy' verified-copies bytes into the dest first.
    dest: str
    create_new: bool = False
    dest_name: Optional[str] = None  # registry name when create_new (default = dir basename)
    mode: Literal["reference", "copy"] = "reference"
    skip_vision: bool = False
    no_embed: bool = False


def _unique_dest(dst_dir: Path, name: str, taken=None) -> Path:
    """Pick a path in dst_dir for `name` that overwrites NOTHING. If the name already
    exists on disk OR was already used this run (casefolded — macOS/exFAT are
    case-insensitive), append ' (n)' before the extension until free.

    fable-audit 2026-07-12 critical #3: two clips sharing a basename (e.g. C0001.MP4
    from two source projects) both resolved to dst_dir/C0001.MP4 and the second
    os.replace silently destroyed the first's bytes while verified-copy reported
    success. The casefolded `taken` set closes the sequential-run window before the
    first file lands on disk (codex footgun: check BOTH on-disk and per-run names)."""
    n = 1
    candidate = name
    stem, ext = os.path.splitext(name)
    while (dst_dir / candidate).exists() or (taken is not None and candidate.casefold() in taken):
        n += 1
        candidate = "{0} ({1}){2}".format(stem, n, ext)
    if taken is not None:
        taken.add(candidate.casefold())
    return dst_dir / candidate


def _copy_clip_verified(src_path: str, dst_dir: Path, taken=None) -> Path:
    """Verified copy of one clip into dst_dir (hash both sides, atomic rename, NEVER
    deletes the source, NEVER overwrites an existing/colliding dest — see
    _unique_dest). offload.run_offload is card/dir-oriented (its rel math breaks for
    a lone file), so this reuses offload's hash primitive on a simple single-file
    copy. Raises on hash mismatch. Returns the actual final path (which may be a
    ' (n)'-suffixed rename); the caller reads .name to detect a rename. (ascMHL
    provenance is a follow-up — not load-bearing for copy-into-project.)"""
    import shutil
    import offload as _offload
    src = Path(src_path)
    dst_dir.mkdir(parents=True, exist_ok=True)
    final = _unique_dest(dst_dir, src.name, taken)
    partial = final.with_name(final.name + ".partial")
    if partial.exists():
        partial.unlink()
    shutil.copyfile(str(src), str(partial))  # bytes only; never touches the source
    if _offload._hash_file(src, _offload.DEFAULT_HASH) != _offload._hash_file(partial, _offload.DEFAULT_HASH):
        partial.unlink()
        raise ValueError("hash mismatch copying {0}".format(src.name))
    os.replace(str(partial), str(final))
    return final


def _bin_detail_payload(b) -> dict:
    """Bin + per-item reachability status. The status probe re-resolves the source
    server-side; only the enum + basename filename + project_name reach the client
    (Phase 16.2 — no absolute path ever leaves the backend)."""
    # fable-audit round-5 #23: one batched status probe (grouped by project) instead
    # of a per-item registry read + health probe + sqlite open.
    statuses = bins_store.bin_item_statuses(b.items)
    items = []
    for item in b.items:
        status = statuses.get((item.project_name, str(item.media_id)), bins_store.STATUS_ERROR)
        items.append({
            "project_name": item.project_name,
            "media_id": item.media_id,
            "filename": _basename_safe(item.filename) if item.filename else "",
            "added_at": item.added_at,
            "status": status,
        })
    payload = b.summary()
    payload["items"] = items
    payload["reachable"] = sum(1 for it in items if it["status"] == bins_store.STATUS_OK)
    return payload


@router.get("/api/bins")
def list_bins(_tok: dict = Depends(require_scopes("collections_read"))):
    try:
        rows = [b.summary() for b in bins_store.list_bins()]
    except bins_store.BinsError as exc:
        raise HTTPException(status_code=500, detail="bins store unreadable: {0}".format(exc))
    return {"bins": rows, "total": len(rows)}


@router.post("/api/bins")
def create_bin(body: BinCreate, _tok: dict = Depends(require_scopes("collections_write"))):
    try:
        b = bins_store.create_bin(body.name)
    except bins_store.BinsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return b.summary()


@router.get("/api/bins/{bin_id}")
def get_bin(bin_id: str, _tok: dict = Depends(require_scopes("collections_read"))):
    try:
        b = bins_store.get_bin(bin_id)
    except bins_store.BinsError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _bin_detail_payload(b)


@router.patch("/api/bins/{bin_id}")
def rename_bin(bin_id: str, body: BinCreate, _tok: dict = Depends(require_scopes("collections_write"))):
    try:
        b = bins_store.rename_bin(bin_id, body.name)
    except bins_store.BinsError as exc:
        code = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc))
    return b.summary()


@router.delete("/api/bins/{bin_id}")
def delete_bin(bin_id: str, _tok: dict = Depends(require_scopes("collections_write"))):
    try:
        b = bins_store.delete_bin(bin_id)
    except bins_store.BinsError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return b.summary()


@router.post("/api/bins/{bin_id}/items")
def add_bin_items(bin_id: str, body: BinAddItems, _tok: dict = Depends(require_scopes("collections_write"))):
    payload = [
        {"project_name": it.project_name, "media_id": it.media_id, "filename": it.filename}
        for it in body.items
    ]
    try:
        b = bins_store.add_items(bin_id, payload)
    except bins_store.BinsError as exc:
        code = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc))
    return _bin_detail_payload(b)


@router.delete("/api/bins/{bin_id}/items")
def remove_bin_item(bin_id: str, body: BinItemRef, _tok: dict = Depends(require_scopes("collections_write"))):
    try:
        b = bins_store.remove_item(bin_id, body.project_name, body.media_id)
    except bins_store.BinsError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _bin_detail_payload(b)


@router.post("/api/bins/{bin_id}/copy")
def copy_bin(bin_id: str, body: BinCopyRequest, _tok: dict = Depends(require_scopes("videos_write"))):
    """Copy a bin's reachable clips into a project (existing or brand-new), then
    index them so they're searchable there. Streams ndjson progress. RED LINE: each
    item is re-gated for reachability server-side; unreachable ones are SKIPPED and
    listed in the summary (never silently dropped), the source library is never
    mutated, and in 'copy' mode the verified-copy engine never deletes the source.

    mode='reference' indexes the original absolute paths in place (no bytes moved);
    mode='copy' verified-copies (hash + MHL) bytes into the dest first."""
    import subprocess, sys as _sys, json as _json

    try:
        b = bins_store.get_bin(bin_id)
    except bins_store.BinsError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Resolve destination project root + registry name.
    if body.create_new:
        dest_root = Path(body.dest).expanduser().resolve(strict=False)
        # refuse to bootstrap into an existing NON-empty project dir (avoid clobber)
        if (dest_root / ".arkiv" / "project.db").exists():
            raise HTTPException(400, "目的地已是既有專案（含 .arkiv/project.db）；請改選現有專案或換新路徑")
        dest_root.mkdir(parents=True, exist_ok=True)
        dest_name = (body.dest_name or dest_root.name).strip() or dest_root.name
    else:
        proj = None
        for p in project_registry.discover_projects():
            if p.name == body.dest:
                proj = p
                break
        if proj is None:
            raise HTTPException(400, "找不到目的專案：{0}".format(body.dest))
        dest_root = Path(proj.path).expanduser().resolve(strict=False)
        dest_name = proj.name

    dest_db = dest_root / ".arkiv" / "project.db"
    (dest_root / ".arkiv").mkdir(parents=True, exist_ok=True)
    dest_media_dir = dest_root / "media"

    def _stream():
        skipped = []
        reachable = []  # (project_name, media_id, absolute_path)
        # ── gate: re-resolve every item server-side; skip unreachable (fail-loud) ──
        for item in b.items:
            info = bins_store.resolve_source(item.project_name, item.media_id)
            status = (info or {}).get("status") or bins_store.STATUS_PROJECT_UNREGISTERED
            if status != bins_store.STATUS_OK or not (info or {}).get("absolute_path"):
                skipped.append({"project_name": item.project_name, "media_id": item.media_id, "status": status})
                yield _json.dumps({"type": "gate", "project_name": item.project_name,
                                   "media_id": item.media_id, "status": status, "action": "skipped"},
                                  ensure_ascii=False) + "\n"
                continue
            reachable.append((item.project_name, item.media_id, info["absolute_path"]))
            yield _json.dumps({"type": "gate", "project_name": item.project_name,
                               "media_id": item.media_id, "status": status, "action": "queued"},
                              ensure_ascii=False) + "\n"

        # ── copy phase (mode=copy only): verified byte-copy into the dest ──
        ingest_paths = []
        if body.mode == "copy" and reachable:
            dest_media_dir.mkdir(parents=True, exist_ok=True)
            taken = set()  # casefolded dest names claimed this run (critical #3)
            for idx, (pn, mid, src) in enumerate(reachable):
                try:
                    copied = _copy_clip_verified(src, dest_media_dir, taken)
                    ingest_paths.append(str(copied))
                    evt = {"type": "copy", "file": Path(src).name,
                           "done": idx + 1, "total": len(reachable)}
                    if copied.name != Path(src).name:  # basename collision → renamed, not clobbered
                        evt["renamed_to"] = copied.name
                    yield _json.dumps(evt, ensure_ascii=False) + "\n"
                except Exception as exc:  # a copy failure must not fake success
                    skipped.append({"project_name": pn, "media_id": mid, "status": "copy_failed: {0}".format(exc)})
                    yield _json.dumps({"type": "copy", "file": Path(src).name, "error": str(exc)},
                                      ensure_ascii=False) + "\n"
        else:
            ingest_paths = [src for (_pn, _mid, src) in reachable]

        # ── index phase: one ingest run over the gathered files (one model warmup) ──
        index_skipped_busy = False
        if ingest_paths:
            cmd = [_sys.executable, str(BASE_DIR / "ingest.py"), "--files", *ingest_paths,
                   "--db", str(dest_db)]
            if body.skip_vision:
                cmd.append("--skip-vision")
            if body.no_embed:
                cmd.append("--no-embed")
            # fable-audit 2026-07-12 (#2/#5): this launches a full whisper+vision
            # pipeline — it MUST share the H3 single-flight slot, or a concurrent
            # /api/ingest runs a second pipeline → double-whisper OOM. If the slot
            # is busy, the bytes are already copied; skip indexing (fail-loud) so
            # the user can re-ingest later rather than risk the OOM.
            if not _acquire_ingest_slot():
                index_skipped_busy = True
                yield _json.dumps({"type": "index", "status": "busy",
                                   "error": "已有匯入任務進行中，已複製檔案但略過索引；請稍後手動 ingest"},
                                  ensure_ascii=False) + "\n"
            else:
                # fable-audit round-5 #58: the slot is now held. The FIRST yield below
                # and the Popen sit BEFORE the inner try, so a GeneratorExit at that
                # yield (client disconnects) or a Popen failure used to escape the
                # release → every ingest endpoint 409s until restart. Wrap everything
                # from here in an outer try whose finally always releases the slot.
                proc = None
                try:
                    yield _json.dumps({"type": "index", "status": "start", "files": len(ingest_paths)},
                                      ensure_ascii=False) + "\n"
                    # Run ingest AS the dest project: ARKIV_PROJECT_ROOT=dest_root so
                    # paths relativize against the dest, not the server's own project.
                    # In reference mode the sources sit OUTSIDE dest_root, so this
                    # stores an absolute media.path (resolvable from the dest); in copy
                    # mode the copied files sit under dest_root/media, so they store
                    # relative. cwd points at the dest .arkiv so ingest's
                    # bench_ingest.json / state don't dirty the install dir (mirrors
                    # /api/offload's state_cwd).
                    ingest_env = dict(os.environ)
                    ingest_env["ARKIV_PROJECT_ROOT"] = str(dest_root)
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, bufsize=1, env=ingest_env,
                                            cwd=str(dest_root / ".arkiv"))
                    try:
                        for line in proc.stdout:
                            yield _json.dumps({"type": "log", "line": line.rstrip("\n")}, ensure_ascii=False) + "\n"
                        proc.wait()
                    except GeneratorExit:
                        # fable-audit 2026-07-12 (#6/#7): client disconnected —
                        # terminate the child (mirrors offload_run) so a multi-minute
                        # whisper/vision pass doesn't run orphaned with the slot held.
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        raise
                    finally:
                        if proc.stdout and not proc.stdout.closed:
                            proc.stdout.close()
                    yield _json.dumps({"type": "index", "status": "done", "code": proc.returncode},
                                      ensure_ascii=False) + "\n"
                finally:
                    _release_ingest_slot()

        # ── bootstrap: register the new project (path now exists) ──
        if body.create_new:
            try:
                project_registry.add_project(dest_name, str(dest_root))
                yield _json.dumps({"type": "registered", "name": dest_name}, ensure_ascii=False) + "\n"
            except project_registry.RegistryError as exc:
                yield _json.dumps({"type": "registered", "error": str(exc)}, ensure_ascii=False) + "\n"

        yield _json.dumps({"type": "done", "summary": {
            "copied": len(ingest_paths), "skipped": skipped, "dest": dest_name, "mode": body.mode,
            "index_skipped_busy": index_skipped_busy,
        }}, ensure_ascii=False) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")
