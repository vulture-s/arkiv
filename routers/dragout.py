"""Drag a clip out of arkiv into an NLE / Finder (roadmap 12.6).

The desktop shell starts a NATIVE file drag (tauri-plugin-drag) — a WebView's
HTML5 drag can only carry text/URLs, never a real file Resolve will accept. The
shell needs an absolute path on disk, and only the backend can resolve a record's
stored (often root-relative) path, so this endpoint hands it over:

  * no IN/OUT (or a window covering the whole clip) → the ORIGINAL file, untouched;
  * an IN/OUT window → a sub-clip cut with ffmpeg stream copy into
    `<project>/.arkiv/dragout/`, reused while it exists, swept after 7 days.

Stream copy is instant and lossless but can only cut on keyframes, so the sub-clip
may start a little before IN — the response says so (`keyframe_cut`). Re-encoding a
4K original on every drag would take minutes; the editor trims the head anyway.
Leaf router: imports config/db/pathres/proctree/transcript_time, never server.
"""
import os
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import config
import db
import proctree
import transcript_time
from auth import require_scopes
from pathres import _resolve_media_path
from webguard import _assert_same_site

router = APIRouter()

KEEP_DAYS = 7


class DragSourceBody(BaseModel):
    in_s: Optional[float] = None
    out_s: Optional[float] = None


def dragout_dir() -> Path:
    return config.PROJECT_ROOT / ".arkiv" / "dragout"


def _sweep(d: Path, now: Optional[float] = None) -> int:
    """Delete sub-clips older than KEEP_DAYS. Only files this endpoint wrote live here."""
    now = now or time.time()
    removed = 0
    for p in d.glob("*"):
        try:
            if p.is_file() and now - p.stat().st_mtime > KEEP_DAYS * 86400:
                p.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def _subclip_name(media_id: int, stem: str, ext: str, t_in: float, t_out: float) -> str:
    safe = re.sub(r"[^\w.-]+", "_", stem)[:80] or "clip"
    return "{0}_{1}_{2:.2f}-{3:.2f}{4}".format(safe, media_id, t_in, t_out, ext)


@router.post("/api/media/{media_id}/drag-source")
def drag_source(
    media_id: int,
    request: Request,
    body: Optional[DragSourceBody] = None,
    _tok: dict = Depends(require_scopes("media_read")),
):
    _assert_same_site(request)
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到這支素材")
    src = Path(_resolve_media_path(rec.get("path", "")))
    if not src.is_file():
        raise HTTPException(404, "原始檔不在原位（NAS 沒有連上，或檔案被移走了）")
    body = body or DragSourceBody()
    duration = float(rec.get("duration_s") or 0)
    t_in, t_out, has_trim = transcript_time.trim_window(body.in_s, body.out_s, duration)
    if not has_trim or duration <= 0:
        return {"path": str(src), "kind": "original", "keyframe_cut": False}

    out_dir = dragout_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    _sweep(out_dir)
    dst = out_dir / _subclip_name(media_id, src.stem, src.suffix, t_in, t_out)
    if not (dst.is_file() and dst.stat().st_size > 0 and dst.stat().st_mtime >= src.stat().st_mtime):
        tmp = dst.with_name(".part-" + dst.name)
        cmd = [config.FFMPEG_PATH, "-hide_banner", "-loglevel", "error", "-y",
               "-ss", "{0:.3f}".format(t_in), "-i", str(src), "-t", "{0:.3f}".format(t_out - t_in),
               "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", str(tmp)]
        try:
            r = proctree.run_tree(cmd, timeout=300, encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 — timeout or ffmpeg missing
            _unlink(tmp)
            raise HTTPException(500, "子片段切不出來：{0}".format(type(e).__name__))
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            _unlink(tmp)
            raise HTTPException(500, "子片段切不出來：{0}".format((r.stderr or "").strip()[:200]))
        os.replace(str(tmp), str(dst))
    os.utime(str(dst), None)  # a re-drag keeps it out of the next sweep
    return {"path": str(dst), "kind": "subclip", "keyframe_cut": True,
            "in_s": round(t_in, 3), "out_s": round(t_out, 3)}


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass
