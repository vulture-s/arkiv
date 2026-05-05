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

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import codec
import config
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


def _resolve_record(rec: dict) -> dict:
    if rec.get("path"):
        rec["path"] = db.resolve_path(rec["path"])
    if rec.get("thumbnail_path"):
        rec["thumbnail_path"] = db.resolve_path(rec["thumbnail_path"])
    return rec


def _resolve_frame(frame: dict) -> dict:
    if frame.get("thumbnail_path"):
        frame["thumbnail_path"] = db.resolve_path(frame["thumbnail_path"])
    return frame


# ── Models ───────────────────────────────────────────────────────────────────

class RatingUpdate(BaseModel):
    rating: Optional[str] = None
    note: Optional[str] = None


class TagCreate(BaseModel):
    name: str
    source: str = "manual"


# ── API Routes ───────────────────────────────────────────────────────────────

@app.get("/api/media/position/{media_id}")
def media_position(
    media_id: int,
    sort: str = "date",
    lang: Optional[str] = None,
    rating: Optional[str] = None,
    media_type: Optional[str] = None,
):
    """Find the row offset of a media item in the current sort/filter view."""
    filters = {}
    if lang:
        filters["lang"] = lang
    if rating:
        filters["rating"] = rating
    if media_type:
        filters["media_type"] = media_type
    where, params = db._build_filter_clause(**filters)
    order = db.SORT_MAP.get(sort, "id")
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT id FROM media WHERE {where} ORDER BY {order}", params
        ).fetchall()
        for idx, row in enumerate(rows):
            if row["id"] == media_id:
                return {"id": media_id, "offset": idx}
    return {"id": media_id, "offset": 0}


@app.get("/api/media/pool")
def media_pool():
    """Lightweight full list for left sidebar media pool — grouped by folder."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, ext, duration_s, rating, path FROM media ORDER BY path, filename"
        ).fetchall()
    items = []
    for r in rows:
        p = r["path"] or ""
        # Use parent directory name as folder; skip generic names like "reels"
        parts = p.replace("\\", "/").rstrip("/").split("/")
        folder = parts[-2] if len(parts) >= 2 else ""
        if folder.lower() in ("reels", "clips", "raw", "media", "footage"):
            folder = parts[-3] if len(parts) >= 3 else folder
        items.append({
            "id": r["id"],
            "filename": r["filename"],
            "ext": r["ext"],
            "duration_s": r["duration_s"],
            "rating": r["rating"],
            "folder": folder,
        })
    return {"items": items, "total": len(items)}


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
        enriched = []
        # Try semantic search first (requires vectordb with embeddings)
        try:
            import vectordb as vdb
            raw = vdb.search(q, n_results=limit * 3)
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
            seen = set()
            for r in results:
                mid = int(r["media_id"])
                if mid in seen:
                    continue
                seen.add(mid)
                rec = db.get_record_by_id(mid)
                if rec:
                    _resolve_record(rec)
                    rec["score"] = r.get("score", 0)
                    rec["excerpt"] = r.get("excerpt", "")
                    rec["tags"] = db.get_tags(mid)
                    enriched.append(rec)
        except Exception:
            pass

        # Fallback: SQL text search (filename, transcript, tags)
        if not enriched:
            seen_ids = set()
            like = f"%{q}%"
            with db.get_conn() as conn:
                rows = conn.execute(
                    f"SELECT {db.LIGHT_COLS} FROM media "
                    "WHERE filename LIKE ? OR transcript LIKE ? "
                    "ORDER BY id",
                    (like, like),
                ).fetchall()
                for r in rows:
                    rec = dict(r)
                    _resolve_record(rec)
                    rec["tags"] = db.get_tags(rec["id"])
                    enriched.append(rec)
                    seen_ids.add(rec["id"])

                # Also search by tag name
                tag_rows = conn.execute(
                    "SELECT DISTINCT media_id FROM tags WHERE name LIKE ?",
                    (like,),
                ).fetchall()
                for tr in tag_rows:
                    mid = tr["media_id"]
                    if mid in seen_ids:
                        continue
                    rec = db.get_record_by_id(mid)
                    if rec:
                        _resolve_record(rec)
                        rec["tags"] = db.get_tags(mid)
                        enriched.append(rec)
                        seen_ids.add(mid)

        return {"items": enriched[:limit], "total": len(enriched), "search": True}

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
        _resolve_record(rec)
        rec["tags"] = db.get_tags(rec["id"])

    return {"items": records, "total": total, "search": False}


@app.get("/api/media/{media_id}")
def get_media_detail(media_id: int):
    """Get full media record with tags and frames."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    _resolve_record(rec)
    rec["tags"] = db.get_tags(media_id)
    # Structured frame analysis data
    rec["frames"] = [_resolve_frame(frame) for frame in db.get_frames(media_id)]
    if rec.get("editability_score") is None:
        for frame in rec["frames"]:
            if frame.get("focus_score") is not None:
                rec["editability_score"] = db.compute_editability(frame)
                break
    # Legacy frame_tags_parsed for backwards compat
    if rec.get("frame_tags"):
        try:
            rec["frame_tags_parsed"] = json.loads(rec["frame_tags"])
        except Exception:
            rec["frame_tags_parsed"] = []
    return rec


@app.get("/api/media/{media_id}/waveform")
def get_media_waveform(media_id: int, bins: int = 60):
    """Return pre-computed audio peaks (0..1) for the inspector waveform.
    Cached per (id, bins) under waveforms/<id>_<bins>.json."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    if not rec.get("has_audio"):
        return {"media_id": media_id, "bins": bins, "peaks": [0.0] * max(8, bins)}
    bins = max(8, min(500, bins))
    cache_dir = ROOT / "waveforms"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{media_id}_{bins}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache_path.unlink(missing_ok=True)
    file_path = Path(db.resolve_path(rec["path"]))
    if not file_path.exists():
        raise HTTPException(404, "找不到檔案")
    peaks = _compute_waveform(str(file_path), bins)
    if peaks is None:
        raise HTTPException(500, "波形計算失敗")
    payload = {"media_id": media_id, "bins": bins, "peaks": peaks}
    try:
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass
    return payload


def _compute_waveform(path: str, bins: int):
    """Decode mono 8kHz PCM via ffmpeg and return `bins` peak-amplitude values 0..1."""
    import subprocess
    import numpy as np
    cmd = [
        "ffmpeg", "-v", "quiet", "-i", path,
        "-ac", "1", "-ar", "8000", "-f", "s16le", "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0 or not r.stdout:
            return None
        samples = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return [0.0] * bins
        splits = np.array_split(samples, bins)
        return [float(np.abs(s).max()) / 32768.0 if s.size else 0.0 for s in splits]
    except Exception:
        return None


@app.get("/api/media/{media_id}/scenes")
def get_media_scenes(media_id: int):
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    frames = db.get_frames(media_id)
    scenes = []
    for frame in frames:
        scene = {
            "frame_index": frame["frame_index"],
            "timestamp_s": frame["timestamp_s"],
            "description": frame.get("description", ""),
            "content_type": frame.get("content_type"),
            "focus_score": frame.get("focus_score"),
            "atmosphere": frame.get("atmosphere"),
            "energy": frame.get("energy"),
            "edit_position": frame.get("edit_position"),
            "edit_reason": frame.get("edit_reason"),
        }
        if frame.get("thumbnail_path"):
            scene["thumbnail_url"] = "/thumbnails/{0}".format(
                Path(db.resolve_path(frame["thumbnail_path"])).name
            )
        scenes.append(scene)
    return {"media_id": media_id, "scenes": scenes, "total": len(scenes)}


@app.patch("/api/media/{media_id}/rating")
def update_rating(media_id: int, body: RatingUpdate):
    """Set or clear rating for a media asset."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    db.set_rating(media_id, body.rating, body.note)
    return {"ok": True, "rating": body.rating, "note": body.note}


@app.get("/api/media/{media_id}/tags")
def get_tags(media_id: int):
    return db.get_tags(media_id)


@app.post("/api/media/{media_id}/tags")
def add_tag(media_id: int, body: TagCreate):
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
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


# ── Metadata Export (Phase 7.6) ──────────────────────────────────────────────

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Defuse CSV formula injection (Excel/Sheets execute leading =/+/-/@/TAB/CR).
    DaVinci 不執行公式，但 user 在 Excel preview 會中招。Prefix 一個 single quote
    是 Excel/Sheets 標準 escape — DaVinci import 時會把整段當成字串收進 metadata。
    """
    if not value:
        return value
    if value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


@app.get("/api/export/metadata-csv")
def export_metadata_csv():
    """DaVinci Resolve metadata CSV — File Name as match key.

    Import in Resolve: File → Import Metadata from CSV.
    Matches Media Pool clip by filename, populates Description / Keywords /
    Comments / Scene panels so Smart Bins can filter by tag/content_type.
    """
    import csv
    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["File Name", "Description", "Keywords", "Comments", "Scene"])

    with db.get_conn() as conn:
        media_rows = conn.execute(
            "SELECT id, filename, transcript, frame_tags, content_type, "
            "atmosphere, energy, edit_position FROM media ORDER BY id"
        ).fetchall()
        for row in media_rows:
            tag_rows = conn.execute(
                "SELECT name FROM tags WHERE media_id=? ORDER BY name", (row["id"],)
            ).fetchall()
            tags = [t["name"] for t in tag_rows]

            # Description: prefer vision frame_tags first line, fallback to transcript prefix
            desc = ""
            if row["frame_tags"]:
                desc = row["frame_tags"].split("\n")[0].strip()[:200]
            elif row["transcript"]:
                desc = row["transcript"].strip()[:200]

            # Keywords: manual tags + content_type (semicolon-separated for Resolve).
            # Dedup case-insensitively: tags 強制 lower (db.py:340)，content_type 由
            # vision.py 寫入有大寫（如「B-Roll」），naive dedup 會兩個都吐出來。
            keywords = list(tags)
            if row["content_type"]:
                ct_lower = row["content_type"].lower()
                if not any(k.lower() == ct_lower for k in keywords):
                    keywords.append(row["content_type"])
            keyword_str = "; ".join(keywords)

            # Comments: atmosphere / energy / edit_position annotations
            comment_parts = []
            if row["atmosphere"]:
                comment_parts.append(f"atmosphere:{row['atmosphere']}")
            if row["energy"]:
                comment_parts.append(f"energy:{row['energy']}")
            if row["edit_position"]:
                comment_parts.append(f"edit:{row['edit_position']}")
            comments = " | ".join(comment_parts)

            # Scene: full multi-line frame_tags collapsed to single line for CSV cell
            scene = ""
            if row["frame_tags"]:
                scene = row["frame_tags"].replace("\n", " ").replace("\r", " ").strip()

            writer.writerow([
                _csv_safe(row["filename"]),
                _csv_safe(desc),
                _csv_safe(keyword_str),
                _csv_safe(comments),
                _csv_safe(scene),
            ])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="arkiv_davinci_metadata.csv"',
        },
    )


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
        raise HTTPException(400, "路徑不是有效的目錄")
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
        raise HTTPException(400, "路徑不是有效的目錄")
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
        return {"ok": False, "error": "匯入逾時（>30 分鐘）"}


# ── Re-transcribe ─────────────────────────────────────────────────────────────

class RetranscribeRequest(BaseModel):
    language: str = "zh"

@app.get("/api/media/{media_id}/remotion-props")
def get_remotion_props(media_id: int):
    """Export word-level timestamps as Remotion CellPhoneReel props."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    words = json.loads(rec.get("words_json") or "[]")
    return {
        "captions": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in words],
        "duration": rec.get("duration_s", 0),
        "filename": rec.get("filename", ""),
    }

@app.post("/api/media/{media_id}/retranscribe")
def retranscribe_media(media_id: int, body: RetranscribeRequest):
    """Re-run Whisper with specified language."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    media_path = db.resolve_path(rec.get("path", ""))
    if not Path(media_path).exists():
        raise HTTPException(400, f"找不到媒體檔案：{media_path}")
    try:
        import transcribe as tr
        text, lang, segments, words = tr.transcribe(media_path, language=body.language)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE media SET transcript=?, lang=?, segments_json=?, words_json=? WHERE id=?",
                (
                    text,
                    body.language,
                    json.dumps(segments, ensure_ascii=False) if segments else None,
                    json.dumps(words, ensure_ascii=False) if words else None,
                    media_id,
                )
            )
        return {"ok": True, "transcript_length": len(text), "language": body.language}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/media/{media_id}/retry-vision")
def retry_vision(media_id: int):
    """Retry vision analysis on frames with empty descriptions.
    Two-phase fallback: primary model → lighter fallback model."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    frames = db.get_frames(media_id)
    empty_frames = [f for f in frames if not f.get("description")]
    if not empty_frames:
        return {"ok": True, "message": "所有幀都已有描述", "patched": 0}

    import vision as vis
    frame_paths = [db.resolve_path(f["thumbnail_path"]) for f in empty_frames]

    # Phase 1: try primary vision model
    results = vis.describe_frames(frame_paths)
    failed = [i for i, r in enumerate(results) if r.get("error") or not r.get("description")]

    # Phase 2: fallback to lighter model for failed frames
    if failed:
        fallback_model = "moondream2:latest"
        original_model = vis.VISION_MODEL
        try:
            vis.VISION_MODEL = fallback_model
            retry_paths = [frame_paths[i] for i in failed]
            retry_results = vis.describe_frames(retry_paths)
            for idx, retry_r in zip(failed, retry_results):
                if retry_r.get("description") and not retry_r.get("error"):
                    results[idx] = retry_r
        finally:
            vis.VISION_MODEL = original_model

    # Write results to DB
    patched = 0
    with db.get_conn() as conn:
        for f, vr in zip(empty_frames, results):
            desc = vr.get("description", "")
            tags = ",".join(vr.get("tags", []))
            if desc:
                conn.execute(
                    """
                    UPDATE frames
                    SET description=?, tags=?, content_type=?, focus_score=?, exposure=?,
                        stability=?, audio_quality=?, atmosphere=?, energy=?,
                        edit_position=?, edit_reason=?
                    WHERE media_id=? AND frame_index=?
                    """,
                    (
                        desc,
                        tags,
                        vr.get("content_type"),
                        vr.get("focus_score"),
                        vr.get("exposure"),
                        vr.get("stability"),
                        vr.get("audio_quality"),
                        vr.get("atmosphere"),
                        vr.get("energy"),
                        vr.get("edit_position"),
                        vr.get("edit_reason"),
                        media_id,
                        f["frame_index"],
                    )
                )
                for tag_name in vr.get("tags", []):
                    tag_name = tag_name.strip()
                    if tag_name and tag_name != "```":
                        db.add_tag(media_id, tag_name, source="auto")
                patched += 1
        # Update legacy frame_tags
        all_frames = db.get_frames(media_id)
        frame_tags = [{"description": f.get("description", ""), "tags": f.get("tags", "").split(",") if f.get("tags") else []} for f in all_frames]
        frame_tags_json = json.dumps(frame_tags, ensure_ascii=False)
        editability_score = None
        for frame in all_frames:
            if frame.get("focus_score") is not None:
                editability_score = db.compute_editability(frame)
                break
        conn.execute(
            "UPDATE media SET frame_tags=?, editability_score=? WHERE id=?",
            (frame_tags_json, editability_score, media_id),
        )

    still_empty = sum(1 for vr in results if not vr.get("description") or vr.get("error"))
    return {
        "ok": still_empty == 0,
        "patched": patched,
        "still_empty": still_empty,
        "total_frames": len(empty_frames),
    }


@app.post("/api/media/{media_id}/reingest")
def reingest_media(media_id: int):
    """Re-run full ingest pipeline: probe + whisper + thumbnail + llava + embed."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    media_path = db.resolve_path(rec.get("path", ""))
    if not Path(media_path).exists():
        raise HTTPException(400, f"找不到媒體檔案：{media_path}")
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
        return {"ok": False, "error": "重新處理逾時（>10 分鐘）"}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Cache Management ──────────────────────────────────────────────────────────

def _dir_size_mb(p: Path) -> int:
    return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1048576)


@app.get("/api/cache/info")
def cache_info():
    """Show cache sizes."""
    caches = {}
    # HuggingFace model cache
    hf_cache = Path.home() / ".cache" / "huggingface"
    if hf_cache.exists():
        caches["huggingface"] = {"path": str(hf_cache), "size_mb": _dir_size_mb(hf_cache)}
    # Ollama models
    ollama_dir = Path.home() / ".ollama" / "models"
    if ollama_dir.exists():
        caches["ollama"] = {"path": str(ollama_dir), "size_mb": _dir_size_mb(ollama_dir)}
    # ChromaDB
    if config.CHROMA_PATH.exists():
        caches["chromadb"] = {"path": str(config.CHROMA_PATH), "size_mb": _dir_size_mb(config.CHROMA_PATH)}
    # Thumbnails
    if config.THUMBNAILS_DIR.exists():
        count = sum(1 for _ in config.THUMBNAILS_DIR.glob("*"))
        caches["thumbnails"] = {"path": str(config.THUMBNAILS_DIR), "count": count, "size_mb": _dir_size_mb(config.THUMBNAILS_DIR)}
    # Browser-playback proxies (generated by ingest for HEVC/ProRes sources)
    if config.PROXIES_DIR.exists():
        count = sum(1 for _ in config.PROXIES_DIR.glob("*.mp4"))
        caches["proxies"] = {"path": str(config.PROXIES_DIR), "count": count, "size_mb": _dir_size_mb(config.PROXIES_DIR)}
    # Waveform peak cache
    waveforms_dir = ROOT / "waveforms"
    if waveforms_dir.exists():
        count = sum(1 for _ in waveforms_dir.glob("*.json"))
        caches["waveforms"] = {"path": str(waveforms_dir), "count": count, "size_mb": _dir_size_mb(waveforms_dir)}
    # Python __pycache__
    pycache = ROOT / "__pycache__"
    if pycache.exists():
        caches["pycache"] = {"path": str(pycache), "size_mb": _dir_size_mb(pycache)}
    # Total
    total_mb = sum(c.get("size_mb", 0) for c in caches.values())
    return {"caches": caches, "total_mb": total_mb}


@app.post("/api/cache/clear")
def clear_cache(target: str = Query("app", description="app|thumbnails|chromadb|waveforms|all")):
    """Clear caches. target: app (pycache+thumbnails+waveforms), thumbnails, chromadb, waveforms, all."""
    import shutil
    cleared = []
    if target in ("app", "thumbnails", "all"):
        thumbs = config.THUMBNAILS_DIR
        if thumbs.exists():
            files = list(thumbs.iterdir())
            for f in files:
                f.unlink(missing_ok=True)
            cleared.append(f"thumbnails ({len(files)} removed)")
    if target in ("app", "waveforms", "all"):
        waveforms_dir = ROOT / "waveforms"
        if waveforms_dir.exists():
            files = list(waveforms_dir.glob("*.json"))
            for f in files:
                f.unlink(missing_ok=True)
            cleared.append(f"waveforms ({len(files)} removed)")
    if target in ("app", "all"):
        pycache = ROOT / "__pycache__"
        if pycache.exists():
            shutil.rmtree(pycache, ignore_errors=True)
            cleared.append("__pycache__")
    if target in ("chromadb", "all"):
        if config.CHROMA_PATH.exists():
            shutil.rmtree(config.CHROMA_PATH, ignore_errors=True)
            cleared.append("chromadb")
    return {"ok": True, "cleared": cleared}


# ── Export ────────────────────────────────────────────────────────────────────

@app.get("/api/media/{media_id}/export/{fmt}")
def export_media(
    media_id: int,
    fmt: str,
    in_s: Optional[float] = None,
    out_s: Optional[float] = None,
):
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    transcript = rec.get("transcript", "") or ""
    filename = rec.get("filename", f"media_{media_id}")
    stem = filename.rsplit(".", 1)[0]
    full_duration = rec.get("duration_s", 0) or 0

    # Normalize trim window: [trim_in, trim_out] in seconds, duration = trim_out - trim_in
    trim_in = max(0.0, float(in_s)) if in_s is not None else 0.0
    trim_out = min(full_duration, float(out_s)) if out_s is not None else full_duration
    if trim_out <= trim_in:
        trim_in, trim_out = 0.0, full_duration
    has_trim = trim_in > 0.05 or trim_out < full_duration - 0.05
    duration = trim_out - trim_in

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

    # Try to use segment-aligned timestamps if available
    import json as _json
    _seg_json = rec.get("segments_json")
    _segments = []
    if _seg_json:
        try:
            _segments = _json.loads(_seg_json)
        except Exception:
            pass

    # When trimmed, keep only segments that overlap [trim_in, trim_out] and
    # rebase their timestamps so the output starts at 0.
    if has_trim and _segments:
        trimmed = []
        for seg in _segments:
            s, e = seg.get("start", 0), seg.get("end", 0)
            if e <= trim_in or s >= trim_out:
                continue
            trimmed.append({
                **seg,
                "start": max(0.0, s - trim_in),
                "end": min(duration, e - trim_in),
            })
        _segments = trimmed

    if fmt == "txt":
        if has_trim:
            # Only text from segments within the trim window. With no segment data
            # we can't trim plain text by time, so the export is empty by design.
            content = "\n".join(seg.get("text", "").strip() for seg in _segments if seg.get("text"))
        else:
            content = transcript
        return HTMLResponse(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.txt"'},
        )

    if fmt == "srt":
        srt = ""
        if _segments:
            # Segment-aligned timestamps (precise)
            for i, seg in enumerate(_segments, 1):
                srt += f"{i}\n{_ts(seg['start'])} --> {_ts(seg['end'])}\n{seg['text']}\n\n"
        else:
            # Fallback: evenly distributed
            lines = [l.strip() for l in transcript.split("\n") if l.strip()]
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
        vtt = "WEBVTT\n\n"
        if _segments:
            for i, seg in enumerate(_segments, 1):
                vtt += f"{_ts(seg['start'], '.')} --> {_ts(seg['end'], '.')}\n{seg['text']}\n\n"
        else:
            lines = [l.strip() for l in transcript.split("\n") if l.strip()]
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

        # Source TC = camera start TC + offset into clip (shifted by trim_in when trimmed)
        src_start = _edl_tc(start_tc_offset + trim_in, clip_fps, is_df)
        src_end = _edl_tc(start_tc_offset + trim_in + duration, clip_fps, is_df)
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
            kept = 0
            for fr in frames:
                marker_offset = fr["timestamp_s"]
                if marker_offset < trim_in or marker_offset > trim_out:
                    continue
                rtc = _edl_tc(rec_base + (marker_offset - trim_in), clip_fps, is_df)
                # Strip non-ASCII for DaVinci compatibility (no UTF-8 in EDL markers)
                desc = (fr.get("description") or f"Frame {fr['frame_index']+1}")
                desc = desc.encode("ascii", "replace").decode("ascii")[:60]
                color = colors[kept % len(colors)]
                edl += f"* LOC: {rtc} {color} {desc}\n"
                kept += 1

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

        # Asset references the full file on disk; the timeline clip uses the trim window.
        asset_dur_frames = round(full_duration * clip_fps)
        clip_dur_frames = round(duration * clip_fps)

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

        from xml.sax.saxutils import escape as xml_esc
        import pathlib

        # Build file URI with proper file:/// prefix
        raw_path = db.resolve_path(rec.get("path", ""))
        file_uri = pathlib.PurePosixPath(raw_path.replace("\\", "/"))
        if not str(file_uri).startswith("/"):
            file_uri = pathlib.PurePosixPath("/" + str(file_uri))
        file_uri_str = xml_esc(f"file://{file_uri}")

        # Build marker elements from frame analysis (filter to trim window, rebase to clip start)
        markers_xml = ""
        frames = db.get_frames(media_id)
        colors = ["Blue", "Red", "Green", "Cyan", "Magenta", "Yellow", "White"]
        kept = 0
        for fr in frames:
            ts = fr["timestamp_s"]
            if ts < trim_in or ts > trim_out:
                continue
            offset_frames = round((ts - trim_in) * clip_fps)
            desc = xml_esc((fr.get("description") or f"Frame {fr['frame_index']+1}")[:60],
                           {'"': '&quot;'})
            color = colors[kept % len(colors)]
            markers_xml += f'                <marker start="{offset_frames * int(_num)}/{_den}s" duration="{_num}/{_den}s" value="{desc}" />\n'
            kept += 1

        # asset-clip start = where in the asset to begin reading (camera TC + trim_in)
        clip_start_frames = round((start_tc_offset + trim_in) * clip_fps)

        fcpxml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
    <resources>
        <format id="r1" frameDuration="{_num}/{_den}s" width="{rec.get('width') or 1920}" height="{rec.get('height') or 1080}" />
        <asset id="r2" name="{xml_esc(stem)}" src="{file_uri_str}" start="0s" duration="{asset_dur_frames * int(_num)}/{_den}s" format="r1" hasAudio="1" hasVideo="1" />
    </resources>
    <library>
        <event name="arkiv Export">
            <project name="{xml_esc(stem)}">
                <sequence format="r1" tcStart="0s" tcFormat="{tc_fmt}" duration="{clip_dur_frames * int(_num)}/{_den}s">
                    <spine>
                        <asset-clip ref="r2" name="{xml_esc(filename)}" offset="0s" duration="{clip_dur_frames * int(_num)}/{_den}s" start="{clip_start_frames * int(_num)}/{_den}s" tcFormat="{tc_fmt}">
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

    raise HTTPException(400, f"不支援的格式：{fmt}。請使用 srt/vtt/txt/edl/edl-markers/fcpxml")


class ExportToRequest(BaseModel):
    fmt: str
    dest: str
    in_s: Optional[float] = None
    out_s: Optional[float] = None

@app.post("/api/media/{media_id}/export-to")
def export_to_file(media_id: int, body: ExportToRequest):
    """Export and write directly to a local path (for Tauri native save dialog)."""
    resp = export_media(media_id, body.fmt, in_s=body.in_s, out_s=body.out_s)
    content = resp.body.decode("utf-8")
    dest = Path(body.dest).expanduser().resolve()
    # Block writes to sensitive system directories
    _blocked = [Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"),
                Path("C:/Windows"), Path("C:/Program Files")]
    for b in _blocked:
        try:
            dest.relative_to(b.resolve())
            raise HTTPException(403, "不允許匯出到系統目錄")
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
        raise HTTPException(400, f"找不到路徑：{body.path}")
    asyncio.create_task(_run_ingest_with_ws(target, body.limit))
    return {"ok": True, "message": "已開始匯入 — 連線 /ws/ingest 取得進度"}


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
    """Stream a media file with range request support for seeking.
    Serves H.264 proxy if available (for browser-incompatible codecs like ProRes/HEVC).
    """
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到媒體")
    # Proxy filename is hash-scoped by absolute source path so that a
    # proxies/ directory copied between installations cannot serve another
    # user's content under the same media_id.
    resolved_src = db.resolve_path(rec["path"])
    proxy_path = config.proxy_path_for(media_id, resolved_src)
    if proxy_path.exists():
        return FileResponse(
            path=str(proxy_path),
            media_type="video/mp4",
            filename=Path(resolved_src).stem + "_proxy.mp4",
        )
    file_path = Path(resolved_src)
    if not file_path.exists():
        raise HTTPException(404, "找不到檔案")
    # Only serve known media extensions
    if file_path.suffix.lower() not in MEDIA_EXTS:
        raise HTTPException(403, "不是媒體檔案")

    # Phase 7.7g: HEVC/ProRes 沒對應 proxy 時不要 silently 送原檔（Chrome/WKWebView
    # 都播不出來，使用者只看到「無法播放」），改回 409 + JSON，前端可 surface
    # 「需先建 proxy」的引導 + POST /api/proxy/build 觸發背景生成。
    # tri-state: NEEDED → 409；NOT_NEEDED / UNKNOWN（ffprobe 失敗、binary 缺、
    # NAS unreachable）→ fall through，維持送原檔的舊 fallback 行為。
    if codec.needs_proxy(str(file_path)) == codec.NEEDED:
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


# ── Proxy Management ─────────────────────────────────────────────────────────

# PROXY_CODECS lives in codec.py — single source of truth.

@app.get("/api/proxy/status")
def proxy_status():
    """Check proxy status for all media files."""
    proxy_dir = config.PROXIES_DIR
    proxy_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM media").fetchall()
    proxied = sum(
        1 for r in rows
        if config.proxy_path_for(r["id"], db.resolve_path(r["path"])).exists()
    )
    size_mb = round(sum(p.stat().st_size for p in proxy_dir.glob("*.mp4")) / 1048576, 1)
    return {"total": len(rows), "proxied": proxied, "size_mb": size_mb}


@app.post("/api/proxy/build")
def proxy_build(background_tasks: BackgroundTasks):
    """Queue proxy generation for all HEVC/ProRes files without proxy."""
    proxy_dir = config.PROXIES_DIR
    proxy_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM media").fetchall()
    to_build = [
        dict(r) for r in rows
        if not config.proxy_path_for(r["id"], db.resolve_path(r["path"])).exists()
    ]
    if not to_build:
        return {"message": "全部 proxy 已存在", "queued": 0}
    background_tasks.add_task(_build_proxies, to_build)
    return {"message": f"開始生成 {len(to_build)} 個 proxy（背景執行）", "queued": len(to_build)}


@app.post("/api/proxy/build/{media_id}")
def proxy_build_one(media_id: int, background_tasks: BackgroundTasks):
    """Per-id proxy build — surface 自 7.7g 409 「生成 proxy」按鈕，使用者點到
    哪個 HEVC 就只建那個，避免 build all 整庫拖時間。"""
    proxy_dir = config.PROXIES_DIR
    proxy_dir.mkdir(parents=True, exist_ok=True)
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到媒體")
    src = db.resolve_path(rec["path"])
    if config.proxy_path_for(media_id, src).exists():
        return {"message": "proxy 已存在", "queued": 0, "media_id": media_id}
    background_tasks.add_task(_build_proxies, [{"id": media_id, "path": rec["path"]}])
    return {
        "message": f"開始生成 proxy（背景執行）",
        "queued": 1,
        "media_id": media_id,
        "filename": rec.get("filename"),
    }


def _build_proxies(items: list):
    """Background task: generate H.264 proxy for each file."""
    import ingest
    for item in items:
        src = db.resolve_path(item["path"])
        try:
            result = ingest.generate_proxy(item["id"], src)
            if not result:
                print(f"[proxy] Failed {item['id']}")
        except Exception as e:
            print(f"[proxy] Failed {item['id']}: {e}")


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
        raise HTTPException(403, "只能開啟已索引的媒體檔案")
    resolved_path = db.resolve_path(file_path)
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
