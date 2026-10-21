"""Drag a clip out of arkiv into an NLE / Finder (roadmap 12.6).

The desktop shell starts a NATIVE file drag (tauri-plugin-drag) — a WebView's
HTML5 drag can only carry text/URLs, never a real file Resolve will accept. The
shell needs an absolute path on disk, and only the backend can resolve a record's
stored (often root-relative) path, so this endpoint hands it over:

  * no IN/OUT (or a window covering the whole clip) → the ORIGINAL file, untouched;
  * an IN/OUT window → a sub-clip in `<project>/.arkiv/dragout/`, reused while it
    exists, swept after 7 days:
      - up to ACCURATE_MAX_S (2 min): RE-ENCODED, so it starts exactly on IN
        (Hevin 2026-10-21: 「要剛好從 IN 開始」). VideoToolbox on Apple Silicon
        (seconds), libx264 elsewhere or when the hardware encoder refuses;
      - longer: ffmpeg stream copy — instant and lossless, but it can only cut on a
        keyframe, so it may start a little before IN (`keyframe_cut: true`, and the
        UI says so). Re-encoding 10 minutes of 4K on a drag would be the wrong trade.
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
ACCURATE_MAX_S = 120.0


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


def _accurate_cmds(src: Path, tmp: Path, t_in: float, dur: float) -> list:
    """ffmpeg commands to try in order for a frame-accurate cut (re-encode)."""
    head = [config.FFMPEG_PATH, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "{0:.3f}".format(t_in), "-i", str(src), "-t", "{0:.3f}".format(dur),
            "-map", "0:v:0", "-map", "0:a?", "-c:a", "aac", "-b:a", "320k"]
    tail = ["-movflags", "+faststart", str(tmp)]
    sw = head + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p"] + tail
    if getattr(config, "_IS_MLX", False):
        hw = head + ["-c:v", "h264_videotoolbox", "-q:v", "70", "-pix_fmt", "yuv420p"] + tail
        return [hw, sw]
    return [sw]


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
    dur = t_out - t_in
    accurate = dur <= ACCURATE_MAX_S
    ext = ".mp4" if accurate else src.suffix
    dst = out_dir / _subclip_name(media_id, src.stem + ("" if accurate else "_kf"), ext, t_in, t_out)
    if not (dst.is_file() and dst.stat().st_size > 0 and dst.stat().st_mtime >= src.stat().st_mtime):
        tmp = dst.with_name(".part-" + dst.name)
        if accurate:
            cmds = _accurate_cmds(src, tmp, t_in, dur)
        else:
            cmds = [[config.FFMPEG_PATH, "-hide_banner", "-loglevel", "error", "-y",
                     "-ss", "{0:.3f}".format(t_in), "-i", str(src), "-t", "{0:.3f}".format(dur),
                     "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", str(tmp)]]
        err = ""
        for cmd in cmds:
            try:
                r = proctree.run_tree(cmd, timeout=600, encoding="utf-8", errors="replace")
            except Exception as e:  # noqa: BLE001 — timeout or ffmpeg missing
                err = type(e).__name__
                _unlink(tmp)
                continue
            if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
                break
            err = (r.stderr or "").strip()[:200]
            _unlink(tmp)
        else:
            raise HTTPException(500, "子片段切不出來：{0}".format(err))
        os.replace(str(tmp), str(dst))
    os.utime(str(dst), None)  # a re-drag keeps it out of the next sweep
    return {"path": str(dst), "kind": "subclip", "keyframe_cut": not accurate,
            "in_s": round(t_in, 3), "out_s": round(t_out, 3)}


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass
