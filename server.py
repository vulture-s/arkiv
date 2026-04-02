"""
Media Asset Manager — FastAPI Backend
Serves the UI (index.html) and provides REST API for all CRUD operations.

Usage:
    uvicorn server:app --host 0.0.0.0 --port 8501
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from pathlib import Path
from typing import Optional, Set

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db


# ── WebSocket connection manager ────────────────────────────────────────────
class IngestBroadcaster:
    """Manages WebSocket connections for ingest progress updates."""
    def __init__(self):
        self.connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket):
        self.connections.discard(ws)

    async def broadcast(self, data: dict):
        dead = set()
        for ws in self.connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self.connections -= dead

ingest_ws = IngestBroadcaster()

# ── Init ─────────────────────────────────────────────────────────────────────
db.init_db()

app = FastAPI(title="Media Asset Manager API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "https://tauri.localhost",   # Tauri webview
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT = Path(__file__).parent

# Serve thumbnails as static files (create dir if missing so mount always works)
thumbs_dir = ROOT / "thumbnails"
thumbs_dir.mkdir(exist_ok=True)
app.mount("/thumbnails", StaticFiles(directory=str(thumbs_dir)), name="thumbnails")


# ── Models ───────────────────────────────────────────────────────────────────

class RatingUpdate(BaseModel):
    rating: Optional[str] = None
    note: Optional[str] = None


class TagCreate(BaseModel):
    name: str
    source: str = "manual"


# ── API Routes ───────────────────────────────────────────────────────────────

@app.get("/api/media")
def list_media(
    offset: int = 0,
    limit: int = 50,
    sort: str = "date",
    lang: Optional[str] = None,
    rating: Optional[str] = None,
    media_type: Optional[str] = None,
    q: Optional[str] = None,
):
    """List media with filters, sorting, and pagination."""
    if q:
        # Semantic search (requires vectordb)
        try:
            import vectordb as vdb
            raw = vdb.search(q, n_results=limit * 3)
            # Post-filter
            results = []
            for r in raw:
                if lang and r.get("lang") != lang:
                    continue
                if rating == "unrated" and r.get("rating") is not None:
                    continue
                elif rating and rating != "unrated" and r.get("rating") != rating:
                    continue
                results.append(r)
            results = results[:limit]
            # Enrich with full record data
            enriched = []
            seen = set()
            for r in results:
                mid = int(r["media_id"])
                if mid in seen:
                    continue
                seen.add(mid)
                rec = db.get_record_by_id(mid)
                if rec:
                    rec["score"] = r.get("score", 0)
                    rec["excerpt"] = r.get("excerpt", "")
                    # Add tags
                    rec["tags"] = db.get_tags(mid)
                    enriched.append(rec)
            return {"items": enriched, "total": len(enriched), "search": True}
        except Exception:
            raise HTTPException(500, "Search error")

    filters = {}
    if lang:
        filters["lang"] = lang
    if rating:
        filters["rating"] = rating
    if media_type:
        filters["media_type"] = media_type

    records, total = db.get_media_filtered(
        offset=offset, limit=limit, sort=sort, **filters,
    )
    # Attach tags to each record
    for rec in records:
        rec["tags"] = db.get_tags(rec["id"])

    return {"items": records, "total": total, "search": False}


@app.get("/api/media/{media_id}")
def get_media_detail(media_id: int):
    """Get full media record with tags and frames."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "Not found")
    rec["tags"] = db.get_tags(media_id)
    # Structured frame analysis data
    rec["frames"] = db.get_frames(media_id)
    # Legacy frame_tags_parsed for backwards compat
    if rec.get("frame_tags"):
        try:
            rec["frame_tags_parsed"] = json.loads(rec["frame_tags"])
        except Exception:
            rec["frame_tags_parsed"] = []
    return rec


@app.patch("/api/media/{media_id}/rating")
def update_rating(media_id: int, body: RatingUpdate):
    """Set or clear rating for a media asset."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "Not found")
    db.set_rating(media_id, body.rating, body.note)
    return {"ok": True, "rating": body.rating, "note": body.note}


@app.get("/api/media/{media_id}/tags")
def get_tags(media_id: int):
    return db.get_tags(media_id)


@app.post("/api/media/{media_id}/tags")
def add_tag(media_id: int, body: TagCreate):
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "Not found")
    db.add_tag(media_id, body.name, body.source)
    return {"ok": True, "tags": db.get_tags(media_id)}


@app.delete("/api/media/{media_id}/tags/{tag_name}")
def remove_tag(media_id: int, tag_name: str):
    db.remove_tag(media_id, tag_name)
    return {"ok": True, "tags": db.get_tags(media_id)}


@app.get("/api/stats")
def get_stats():
    """Aggregate stats for dashboard."""
    stats = db.get_stats()
    stats["rating"] = db.get_rating_stats()
    stats["top_tags"] = db.get_top_tags(10)
    return stats


@app.get("/api/tags")
def get_all_tags():
    """All unique tag names for autocomplete."""
    return db.get_all_tag_names()


@app.get("/api/duration-by-lang")
def duration_by_lang():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT lang, SUM(duration_s) as total_s, COUNT(*) as count "
            "FROM media WHERE lang IS NOT NULL GROUP BY lang ORDER BY total_s DESC"
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/size-by-ext")
def size_by_ext():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT ext, SUM(size_mb) as total_mb, COUNT(*) as count "
            "FROM media GROUP BY ext ORDER BY total_mb DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Ingest ────────────────────────────────────────────────────────────────────

class IngestRequest(BaseModel):
    path: str
    limit: int = 0

class ScanRequest(BaseModel):
    path: str

MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}

@app.post("/api/ingest/scan")
def scan_media(body: ScanRequest):
    """Quick scan — return file list without processing."""
    target = Path(body.path).expanduser().resolve()
    if not target.is_dir():
        raise HTTPException(400, "Path is not a valid directory")
    files = []
    for f in sorted(target.rglob("*")):
        if f.suffix.lower() in MEDIA_EXTS:
            already = db.is_processed(str(f)) if hasattr(db, 'is_processed') else False
            files.append({"name": f.name, "size_mb": round(f.stat().st_size / 1048576, 1), "path": str(f), "already": already})
    return {"total": len(files), "new": sum(1 for f in files if not f["already"]), "files": files}

@app.post("/api/ingest")
def ingest_media(body: IngestRequest):
    """Trigger ingest from the web UI — runs ingest.py as subprocess."""
    import subprocess, sys
    target = Path(body.path).expanduser().resolve()
    if not target.is_dir():
        raise HTTPException(400, "Path is not a valid directory")
    cmd = [sys.executable, str(ROOT / "ingest.py"), "--dir", str(target)]
    if body.limit > 0:
        cmd += ["--limit", str(body.limit)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Ingest timeout (>30min)"}


# ── Re-transcribe ─────────────────────────────────────────────────────────────

class RetranscribeRequest(BaseModel):
    language: str = "zh"

@app.post("/api/media/{media_id}/retranscribe")
def retranscribe_media(media_id: int, body: RetranscribeRequest):
    """Re-run Whisper with specified language."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "Not found")
    media_path = rec.get("path", "")
    if not Path(media_path).exists():
        raise HTTPException(400, f"Media file not found: {media_path}")
    try:
        import transcribe as tr
        text, lang = tr.transcribe(media_path, language=body.language)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE media SET transcript=?, lang=? WHERE id=?",
                (text, body.language, media_id)
            )
        return {"ok": True, "transcript_length": len(text), "language": body.language}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/media/{media_id}/reingest")
def reingest_media(media_id: int):
    """Re-run full ingest pipeline: probe + whisper + thumbnail + llava + embed."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "Not found")
    media_path = rec.get("path", "")
    if not Path(media_path).exists():
        raise HTTPException(400, f"Media file not found: {media_path}")
    import subprocess, sys
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "ingest.py"), "--dir", str(Path(media_path).parent),
             "--limit", "1", "--refresh"],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT)
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout[-1000:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Reingest timeout (>10min)"}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Cache Management ──────────────────────────────────────────────────────────

@app.get("/api/cache/info")
def cache_info():
    """Show cache sizes."""
    import shutil
    caches = {}
    # HuggingFace model cache
    hf_cache = Path.home() / ".cache" / "huggingface"
    if hf_cache.exists():
        size = sum(f.stat().st_size for f in hf_cache.rglob("*") if f.is_file())
        caches["huggingface"] = {"path": str(hf_cache), "size_mb": round(size / 1048576)}
    # Ollama models
    ollama_dir = Path.home() / ".ollama" / "models"
    if ollama_dir.exists():
        size = sum(f.stat().st_size for f in ollama_dir.rglob("*") if f.is_file())
        caches["ollama"] = {"path": str(ollama_dir), "size_mb": round(size / 1048576)}
    # ChromaDB
    chroma = ROOT / "chroma_db"
    if chroma.exists():
        size = sum(f.stat().st_size for f in chroma.rglob("*") if f.is_file())
        caches["chromadb"] = {"path": str(chroma), "size_mb": round(size / 1048576)}
    # Thumbnails
    thumbs = ROOT / "thumbnails"
    if thumbs.exists():
        count = len(list(thumbs.glob("*")))
        size = sum(f.stat().st_size for f in thumbs.rglob("*") if f.is_file())
        caches["thumbnails"] = {"path": str(thumbs), "count": count, "size_mb": round(size / 1048576)}
    # Python __pycache__
    pycache = ROOT / "__pycache__"
    if pycache.exists():
        size = sum(f.stat().st_size for f in pycache.rglob("*") if f.is_file())
        caches["pycache"] = {"path": str(pycache), "size_mb": round(size / 1048576)}
    # Total
    total_mb = sum(c.get("size_mb", 0) for c in caches.values())
    return {"caches": caches, "total_mb": total_mb}


@app.post("/api/cache/clear")
def clear_cache(target: str = Query("app", description="app|thumbnails|chromadb|all")):
    """Clear caches. target: app (pycache+thumbnails), thumbnails, chromadb, all."""
    import shutil
    cleared = []
    if target in ("app", "thumbnails", "all"):
        thumbs = ROOT / "thumbnails"
        if thumbs.exists():
            for f in thumbs.iterdir():
                f.unlink(missing_ok=True)
            cleared.append(f"thumbnails ({len(list(thumbs.glob('*')))} removed)")
    if target in ("app", "all"):
        pycache = ROOT / "__pycache__"
        if pycache.exists():
            shutil.rmtree(pycache, ignore_errors=True)
            cleared.append("__pycache__")
    if target in ("chromadb", "all"):
        chroma = ROOT / "chroma_db"
        if chroma.exists():
            shutil.rmtree(chroma, ignore_errors=True)
            cleared.append("chromadb")
    return {"ok": True, "cleared": cleared}


# ── Export ────────────────────────────────────────────────────────────────────

@app.get("/api/media/{media_id}/export/{fmt}")
def export_media(media_id: int, fmt: str):
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "Not found")
    transcript = rec.get("transcript", "") or ""
    filename = rec.get("filename", f"media_{media_id}")
    stem = filename.rsplit(".", 1)[0]
    duration = rec.get("duration_s", 0) or 0

    def _ts(seconds: float, sep: str = ",") -> str:
        """Subtitle timecode (SRT/VTT): HH:MM:SS,mmm"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"

    def _edl_tc(seconds: float, fps: float, drop_frame: bool = False) -> str:
        """EDL timecode: HH:MM:SS:FF (NDF) or HH:MM:SS;FF (DF)"""
        if fps <= 0:
            fps = 30.0
        # Round to nearest frame
        int_fps = round(fps)
        total_frames = round(seconds * fps)

        if drop_frame and int_fps in (30, 60):
            # Drop-frame: skip frame 0,1 (30p) or 0,1,2,3 (60p) each minute except every 10th
            d = 2 if int_fps == 30 else 4
            frames_per_min = int_fps * 60 - d
            frames_per_10min = frames_per_min * 10 + d

            tens = total_frames // frames_per_10min
            rem = total_frames % frames_per_10min

            if rem < int_fps * 60:
                adjusted = total_frames + d * 9 * tens
            else:
                adjusted = total_frames + d * 9 * tens + d * ((rem - int_fps * 60) // frames_per_min + 1)

            ff = adjusted % int_fps
            ss = (adjusted // int_fps) % 60
            mm = (adjusted // (int_fps * 60)) % 60
            hh = adjusted // (int_fps * 3600)
        else:
            ff = total_frames % int_fps
            remaining = total_frames // int_fps
            ss = remaining % 60
            remaining //= 60
            mm = remaining % 60
            hh = remaining // 60

        sep = ";" if drop_frame else ":"
        return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"

    if fmt == "txt":
        return HTMLResponse(
            content=transcript,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.txt"'},
        )

    if fmt == "srt":
        lines = [l.strip() for l in transcript.split("\n") if l.strip()]
        srt = ""
        for i, line in enumerate(lines, 1):
            t_start = (i - 1) * (duration / max(len(lines), 1))
            t_end = i * (duration / max(len(lines), 1))
            srt += f"{i}\n{_ts(t_start)} --> {_ts(t_end)}\n{line}\n\n"
        return HTMLResponse(
            content=srt,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.srt"'},
        )

    if fmt == "vtt":
        lines = [l.strip() for l in transcript.split("\n") if l.strip()]
        vtt = "WEBVTT\n\n"
        for i, line in enumerate(lines, 1):
            t_start = (i - 1) * (duration / max(len(lines), 1))
            t_end = i * (duration / max(len(lines), 1))
            vtt += f"{_ts(t_start, '.')} --> {_ts(t_end, '.')}\n{line}\n\n"
        return HTMLResponse(
            content=vtt,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.vtt"'},
        )

    if fmt in ("edl", "edl-markers"):
        # CMX3600 EDL — full clip + optional frame markers
        clip_fps = rec.get("fps") or 30.0
        # 29.97/59.94 are drop-frame by convention
        is_df = round(clip_fps, 2) in (29.97, 59.94)
        fcm = "DROP FRAME" if is_df else "NON-DROP FRAME"

        # Camera body start timecode (may not be 00:00:00:00)
        start_tc_str = rec.get("start_tc") or ""
        start_tc_offset = 0.0
        if start_tc_str:
            # Parse HH:MM:SS:FF or HH:MM:SS;FF into seconds
            _tc = start_tc_str.replace(";", ":").split(":")
            if len(_tc) == 4:
                try:
                    _h, _m, _s, _f = int(_tc[0]), int(_tc[1]), int(_tc[2]), int(_tc[3])
                    start_tc_offset = _h * 3600 + _m * 60 + _s + _f / clip_fps
                except (ValueError, ZeroDivisionError):
                    start_tc_offset = 0.0

        # Source TC = camera start TC + offset into clip
        src_start = _edl_tc(start_tc_offset, clip_fps, is_df)
        src_end = _edl_tc(start_tc_offset + duration, clip_fps, is_df)
        # Record TC = timeline position (starts at 01:00:00:00 by convention)
        rec_base = 3600.0  # 01:00:00:00
        rec_start = _edl_tc(rec_base, clip_fps, is_df)
        rec_end = _edl_tc(rec_base + duration, clip_fps, is_df)

        edl = f"TITLE: {stem}\nFCM: {fcm}\n\n"
        # Use filename stem as reel name (max 8 chars for CMX3600 compat)
        reel = stem[:8].ljust(8)
        edl += f"001  {reel} V     C        {src_start} {src_end} {rec_start} {rec_end}\n"
        edl += f"* FROM CLIP NAME: {filename}\n"
        if start_tc_str:
            edl += f"* SOURCE START TC: {start_tc_str}\n"
        edl += "\n"

        if fmt == "edl-markers":
            # LOC comments — DaVinci reads these via "Import > Timeline Markers from EDL"
            colors = ["RED", "BLUE", "GREEN", "CYAN", "MAGENTA", "YELLOW", "WHITE"]
            frames = db.get_frames(media_id)
            for i, fr in enumerate(frames):
                marker_offset = fr["timestamp_s"]
                rtc = _edl_tc(rec_base + marker_offset, clip_fps, is_df)
                # Strip non-ASCII for DaVinci compatibility (no UTF-8 in EDL markers)
                desc = (fr.get("description") or f"Frame {fr['frame_index']+1}")
                desc = desc.encode("ascii", "replace").decode("ascii")[:60]
                color = colors[i % len(colors)]
                edl += f"* LOC: {rtc} {color} {desc}\n"

        return HTMLResponse(
            content=edl,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.edl"'},
        )

    if fmt == "fcpxml":
        # FCPXML 1.8 — max compatibility: FCPX 10.4+, DaVinci 17+, Premiere via XtoCC
        clip_fps = rec.get("fps") or 30.0

        # Rational frame duration for FCPXML (must be exact, not rounded)
        _fps_map = {
            23.98: ("1001", "24000"), 23.976: ("1001", "24000"),
            29.97: ("1001", "30000"), 59.94: ("1001", "60000"),
        }
        rounded_fps = round(clip_fps, 2)
        if rounded_fps in _fps_map:
            _num, _den = _fps_map[rounded_fps]
        else:
            _num, _den = "1", str(round(clip_fps))

        # Drop frame for NTSC rates
        is_df = rounded_fps in (29.97, 59.94)
        tc_fmt = "DF" if is_df else "NDF"

        # Duration in rational time
        dur_frames = round(duration * clip_fps)

        # Camera body start timecode
        start_tc_str = rec.get("start_tc") or "00:00:00:00"
        start_tc_offset = 0.0
        _tc = start_tc_str.replace(";", ":").split(":")
        if len(_tc) == 4:
            try:
                _h, _m, _s, _f = int(_tc[0]), int(_tc[1]), int(_tc[2]), int(_tc[3])
                start_tc_offset = _h * 3600 + _m * 60 + _s + _f / clip_fps
            except (ValueError, ZeroDivisionError):
                pass

        from xml.sax.saxutils import escape as xml_esc, quoteattr as xml_qa
        import pathlib

        # Build file URI with proper file:/// prefix
        raw_path = rec.get("path", "")
        file_uri = pathlib.PurePosixPath(raw_path.replace("\\", "/"))
        if not str(file_uri).startswith("/"):
            file_uri = pathlib.PurePosixPath("/" + str(file_uri))
        file_uri_str = xml_esc(f"file://{file_uri}")

        # Build marker elements from frame analysis
        markers_xml = ""
        frames = db.get_frames(media_id)
        colors = ["Blue", "Red", "Green", "Cyan", "Magenta", "Yellow", "White"]
        for i, fr in enumerate(frames):
            offset_frames = round(fr["timestamp_s"] * clip_fps)
            desc = xml_esc((fr.get("description") or f"Frame {fr['frame_index']+1}")[:60],
                           {'"': '&quot;'})
            color = colors[i % len(colors)]
            markers_xml += f'                <marker start="{offset_frames * int(_num)}/{_den}s" duration="{_num}/{_den}s" value="{desc}" />\n'

        start_frames = round(start_tc_offset * clip_fps)

        fcpxml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
    <resources>
        <format id="r1" frameDuration="{_num}/{_den}s" width="{rec.get('width') or 1920}" height="{rec.get('height') or 1080}" />
        <asset id="r2" name="{xml_esc(stem)}" src="{file_uri_str}" start="0s" duration="{dur_frames * int(_num)}/{_den}s" format="r1" hasAudio="1" hasVideo="1" />
    </resources>
    <library>
        <event name="arkiv Export">
            <project name="{xml_esc(stem)}">
                <sequence format="r1" tcStart="0s" tcFormat="{tc_fmt}" duration="{dur_frames * int(_num)}/{_den}s">
                    <spine>
                        <asset-clip ref="r2" name="{xml_esc(filename)}" offset="0s" duration="{dur_frames * int(_num)}/{_den}s" start="{start_frames * int(_num)}/{_den}s" tcFormat="{tc_fmt}">
{markers_xml}                        </asset-clip>
                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>"""

        return HTMLResponse(
            content=fcpxml,
            media_type="application/xml; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.fcpxml"'},
        )

    raise HTTPException(400, f"Unsupported format: {fmt}. Use srt/vtt/txt/edl/edl-markers/fcpxml")


class ExportToRequest(BaseModel):
    fmt: str
    dest: str

@app.post("/api/media/{media_id}/export-to")
def export_to_file(media_id: int, body: ExportToRequest):
    """Export and write directly to a local path (for Tauri native save dialog)."""
    resp = export_media(media_id, body.fmt)
    content = resp.body.decode("utf-8")
    dest = Path(body.dest).expanduser().resolve()
    # Block writes to sensitive system directories
    _blocked = [Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"),
                Path("C:/Windows"), Path("C:/Program Files")]
    for b in _blocked:
        try:
            dest.relative_to(b.resolve())
            raise HTTPException(403, "Export to system directories is not allowed")
        except ValueError:
            pass  # not under blocked path — OK
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(dest), "size": dest.stat().st_size}


# ── WebSocket: Ingest Progress ───────────────────────────────────────────

@app.websocket("/ws/ingest")
async def ws_ingest(ws: WebSocket):
    """WebSocket endpoint for real-time ingest progress updates."""
    await ingest_ws.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep alive, client can send pings
    except WebSocketDisconnect:
        ingest_ws.disconnect(ws)


async def _run_ingest_with_ws(target: Path, limit: int):
    """Run ingest as a single subprocess, parse stdout for progress."""
    import re, sys

    cmd = [sys.executable, str(ROOT / "ingest.py"), "--dir", str(target)]
    if limit > 0:
        cmd += ["--limit", str(limit)]

    await ingest_ws.broadcast({"type": "start", "total": limit or 0})

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=str(ROOT)
    )

    ok, skipped, failed = 0, 0, 0
    file_re = re.compile(r"\[(\d+)/(\d+)\]\s+(SKIP\s+)?(\S+)\s+>")
    done_re = re.compile(r"\[(\d+)/(\d+)\]\s+(\S+)\s+.+\[OK\]")

    async for line in proc.stdout:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        print(f"[ingest-ws] {text}", flush=True)

        # Parse progress lines like "[1/3] FX30.5365.MP4 >probe >whisper..."
        m = file_re.match(text)
        if m:
            idx, total, skip, fname = m.group(1), m.group(2), m.group(3), m.group(4)
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
            await ingest_ws.broadcast({
                "type": "file", "index": int(d.group(1)), "total": int(d.group(2)),
                "filename": d.group(3).strip(), "status": "done"
            })

        # Parse "Found N media files"
        if text.startswith("Found "):
            fm = re.search(r"Processing (\d+)", text)
            if fm:
                await ingest_ws.broadcast({"type": "start", "total": int(fm.group(1))})

    await proc.wait()
    failed = (limit or 0) - ok - skipped if limit else 0
    print(f"[ingest-ws] COMPLETE ok={ok} skipped={skipped} failed={failed}", flush=True)

    await ingest_ws.broadcast({
        "type": "complete", "ok": ok, "skipped": skipped, "failed": failed
    })


@app.post("/api/ingest/ws")
async def ingest_media_ws(body: IngestRequest):
    """Trigger ingest with WebSocket progress broadcasting."""
    target = Path(body.path).expanduser()
    if not target.exists():
        raise HTTPException(400, f"Path not found: {body.path}")
    asyncio.create_task(_run_ingest_with_ws(target, body.limit))
    return {"ok": True, "message": "Ingest started — connect to /ws/ingest for progress"}


# ── Tailwind CDN proxy (cached locally so Tauri WKWebView never blocks) ────────
_TAILWIND_CDN_URL = "https://cdn.tailwindcss.com"
_tailwind_js: Optional[bytes] = None

def _fetch_tailwind() -> bytes:
    """Download Tailwind CDN JS once and cache on disk. Skip empty cache files."""
    cache_path = ROOT / "tailwind.cdn.js"
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        return cache_path.read_bytes()
    try:
        req = urllib.request.Request(_TAILWIND_CDN_URL,
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        if len(data) > 1000:
            cache_path.write_bytes(data)
            return data
    except Exception as e:
        print(f"[arkiv] Tailwind CDN download failed: {e}")
    return b"/* tailwind cdn unavailable */"

# Pre-fetch at import time (runs before uvicorn starts serving)
_tailwind_js = _fetch_tailwind()

@app.get("/tailwind.cdn.js")
def serve_tailwind():
    return Response(content=_tailwind_js, media_type="text/javascript",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/tailwind-static.css")
def serve_tailwind_static():
    css_path = ROOT / "tailwind-static.css"
    if css_path.exists():
        return Response(content=css_path.read_bytes(), media_type="text/css",
                        headers={"Cache-Control": "no-cache"})
    return Response(content=b"/* tailwind-static.css not found */", media_type="text/css")


# ── Video Streaming ──────────────────────────────────────────────────────────

import mimetypes

@app.get("/api/stream/{media_id}")
def stream_media(media_id: int):
    """Stream a media file with range request support for seeking."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "Media not found")
    file_path = Path(rec["path"])
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    # Only serve known media extensions
    if file_path.suffix.lower() not in MEDIA_EXTS:
        raise HTTPException(403, "Not a media file")
    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        mime = "video/mp4"
    return FileResponse(
        path=str(file_path),
        media_type=mime,
        filename=file_path.name,
    )


# ── Serve Frontend ───────────────────────────────────────────────────────────

# Dev mode: always read fresh index.html (no cache)
def _load_index() -> str:
    index = ROOT / "index.html"
    if index.exists():
        return index.read_text(encoding="utf-8")
    return "<h1>arkiv</h1><p>index.html not found</p>"

@app.post("/api/open-file")
async def open_file(request: __import__('starlette.requests', fromlist=['Request']).Request):
    """Open file in OS default app or reveal in file manager. Only allows known media files from DB."""
    import subprocess, platform
    body = await request.json()
    file_path = body.get("path", "")
    reveal = body.get("reveal", False)
    # Validate: only allow paths that exist in our database
    if not db.is_processed(file_path):
        raise HTTPException(403, "Only indexed media files can be opened")
    if not Path(file_path).exists():
        raise HTTPException(404, "File not found")
    system = platform.system()
    if reveal:
        if system == "Darwin":
            subprocess.Popen(["open", "-R", file_path])
        elif system == "Windows":
            subprocess.Popen(["explorer", "/select,", file_path])
        else:
            subprocess.Popen(["xdg-open", str(Path(file_path).parent)])
    else:
        if system == "Darwin":
            subprocess.Popen(["open", file_path])
        elif system == "Windows":
            os.startfile(file_path)
        else:
            subprocess.Popen(["xdg-open", file_path])
    return {"ok": True}


@app.post("/api/client-log")
async def client_log(request: __import__('starlette.requests', fromlist=['Request']).Request):
    """Receive client-side logs (errors, info) and print to server terminal."""
    body = await request.json()
    level = body.get("level", "info").upper()
    msg = body.get("msg", "")
    print(f"[WebView {level}] {msg}", flush=True)
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def serve_index():
    return _load_index()
