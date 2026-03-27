"""
Media Asset Manager — FastAPI Backend
Serves the UI (index.html) and provides REST API for all CRUD operations.

Usage:
    cd tools/media-manager
    uvicorn server:app --host 0.0.0.0 --port 8501 --reload
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db

# ── Init ─────────────────────────────────────────────────────────────────────
db.init_db()

app = FastAPI(title="Media Asset Manager API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT = Path(__file__).parent

# Serve thumbnails as static files
thumbs_dir = ROOT / "thumbnails"
if thumbs_dir.exists():
    app.mount("/thumbnails", StaticFiles(directory=str(thumbs_dir)), name="thumbnails")


# ── Models ───────────────────────────────────────────────────────────────────

class RatingUpdate(BaseModel):
    rating: str | None = None
    note: str | None = None


class TagCreate(BaseModel):
    name: str
    source: str = "manual"


# ── API Routes ───────────────────────────────────────────────────────────────

@app.get("/api/media")
def list_media(
    offset: int = 0,
    limit: int = 50,
    sort: str = "date",
    lang: str | None = None,
    rating: str | None = None,
    media_type: str | None = None,
    q: str | None = None,
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
        except Exception as e:
            raise HTTPException(500, f"Search error: {e}")

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
    """Get full media record with tags."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "Not found")
    rec["tags"] = db.get_tags(media_id)
    # Parse frame_tags JSON
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

MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}

@app.post("/api/ingest/scan")
def scan_media(body: ScanRequest):
    """Quick scan — return file list without processing."""
    target = Path(body.path).expanduser()
    if not target.exists():
        raise HTTPException(400, f"Path not found: {body.path}")
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
    target = Path(body.path).expanduser()
    if not target.exists():
        raise HTTPException(400, f"Path not found: {body.path}")
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
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"

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

    if fmt == "edl":
        # Simple CMX3600 EDL
        edl = f"TITLE: {stem}\nFCM: NON-DROP FRAME\n\n"
        edl += f"001  AX       V     C        00:00:00:00 {_ts(duration, ':').replace(',', ':')} 00:00:00:00 {_ts(duration, ':').replace(',', ':')}\n"
        edl += f"* FROM CLIP NAME: {filename}\n"
        return HTMLResponse(
            content=edl,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.edl"'},
        )

    raise HTTPException(400, f"Unsupported format: {fmt}. Use srt/vtt/txt/edl")


# ── Serve Frontend ───────────────────────────────────────────────────────────

# Cache index.html at startup to avoid fd leak on repeated read_text()
_INDEX_HTML: str | None = None

def _load_index() -> str:
    global _INDEX_HTML
    index = ROOT / "index.html"
    if index.exists():
        _INDEX_HTML = index.read_text(encoding="utf-8")
    return _INDEX_HTML or "<h1>arkiv</h1><p>index.html not found</p>"

_load_index()

@app.get("/", response_class=HTMLResponse)
def serve_index():
    return _INDEX_HTML or _load_index()
