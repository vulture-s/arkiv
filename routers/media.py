"""Media route group (R5-25 / round-5 #51 router split).

The clip-centric surface: the media list (`/api/media` — filtered/sorted/paginated
+ the semantic/SQL search branches), the single-record detail, and the per-clip
sub-resources (waveform, scenes, chapters, rating, tags, remotion-props,
single-clip retranscribe, transcript archive + activate, retry-vision).

Route ORDER matters here: `/api/media/pool` and `/api/media/position/{media_id}`
are declared BEFORE the dynamic `/api/media/{media_id}` — otherwise the typed-int
`{media_id}` route shadows the literal `pool` segment (media_id="pool" → 422).

The two bulk-fetch helpers this group shares with the search group live in the
leaf module mediarecords.py; path resolution in pathres.py; the ?ids= parser in
reqopts.py — all imported directly, no server import, no cycle. `BASE_DIR`
(config) replaces server.ROOT for the waveform cache dir.
"""
import json
import logging as _logging
import math
import os
import re as _re
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

import config
import corrections
import db
import mediatypes
import tag_quality
from auth import require_scopes
from config import BASE_DIR
from mediarecords import _get_light_records_by_ids, _get_tags_bulk
from pathres import _display_path, _resolve_frame, _resolve_media_path, _resolve_record
from reqopts import _parse_ids_query
from scenes import _scenes_payload
from webguard import _assert_same_site

router = APIRouter()


# ── Models ───────────────────────────────────────────────────────────────────

class RatingUpdate(BaseModel):
    # Constrain to the stored vocabulary (or None to clear) — an arbitrary string
    # used to be persisted verbatim, corrupting rating stats and sort buckets.
    rating: Optional[Literal["good", "ng", "review"]] = None
    note: Optional[str] = None


class InOutUpdate(BaseModel):
    # Per-clip IN/OUT trim points in SECONDS (or None to clear). An arbitrary
    # NaN/inf/negative value would corrupt the export math downstream, so reject it.
    in_point: Optional[float] = None
    out_point: Optional[float] = None

    @field_validator("in_point", "out_point")
    @classmethod
    def _finite_nonneg(cls, v):
        if v is None:
            return v
        if not math.isfinite(v) or v < 0:
            raise ValueError("trim point must be a finite, non-negative number of seconds")
        return v


class TagCreate(BaseModel):
    name: str
    # Public callers may not mint 'auto' tags — those are owned by the vision
    # pipeline and wiped on re-ingest, so a client-supplied source='auto' tag
    # would silently vanish. Force 'manual'.
    source: Literal["manual"] = "manual"

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        # strip control chars (newlines etc.), collapse whitespace, reject empty,
        # cap length — an empty/whitespace/control-char/huge tag used to be stored.
        cleaned = " ".join("".join(c for c in (v or "") if c == " " or ord(c) >= 0x20 and c not in "\x7f").split())
        if not cleaned:
            raise ValueError("tag name must not be empty")
        if len(cleaned) > 100:
            raise ValueError("tag name too long (max 100)")
        return cleaned


class RetranscribeRequest(BaseModel):
    language: str = "zh"

    # audit M23: an arbitrary string used to flow straight into whisper → 500
    # with raw str(e) leaked to the client + a polluted lang column on partial
    # writes. Accept ISO-639-shaped codes only (whisper's set is 2-3 letters).
    @field_validator("language")
    @classmethod
    def _check_language(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if not _re.fullmatch(r"[a-z]{2,3}", v):
            raise ValueError("language must be a 2-3 letter ISO-639 code (e.g. 'zh', 'en')")
        return v


class ActivateLangRequest(BaseModel):
    lang: str


# audit H14: ext buckets mirror db._build_filter_clause's media_type sets so the
# search branch applies the SAME filter the SQL (non-search) path does. R5-24:
# both now come from the shared mediatypes source, so they can't drift apart.
_VIDEO_EXTS = mediatypes.VIDEO_EXT
_AUDIO_EXTS = mediatypes.AUDIO_EXT


@router.get("/api/media/position/{media_id}")
def media_position(
    media_id: int,
    sort: str = "date",
    lang: Optional[str] = None,
    rating: Optional[str] = None,
    media_type: Optional[str] = None,
    _tok: dict = Depends(require_scopes("videos_read")),
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
    # audit L12: single window-function query instead of fetching the whole view
    # into Python; and a clip NOT in the current view now says so explicitly
    # (in_view=false) instead of a fake offset 0 that is indistinguishable from
    # genuinely being first. offset stays 0 in that case so the existing UI
    # fallback (jump to page 0) keeps working.
    with db.get_conn() as conn:
        row = conn.execute(
            f"SELECT pos FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY {order}) - 1 AS pos "
            f"FROM media WHERE {where}) ranked WHERE id = ?",
            (*params, media_id),
        ).fetchone()
    if row is None:
        return {"id": media_id, "offset": 0, "in_view": False}
    return {"id": media_id, "offset": row["pos"], "in_view": True}


@router.get("/api/media/pool")
def media_pool(
    _tok: dict = Depends(require_scopes("videos_read")),
):
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


@router.get("/api/media")
def list_media(
    offset: int = 0,
    limit: int = 50,
    sort: str = "date",
    lang: Optional[str] = None,
    rating: Optional[str] = None,
    media_type: Optional[str] = None,
    q: Optional[str] = None,
    ids: Optional[str] = None,
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """List media with filters, sorting, and pagination."""
    # Clamp pagination: a negative limit becomes SQLite LIMIT -1 (unbounded full
    # dump) and a huge limit blows up the vector-search n_results.
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    # Deep-link from chat: #/main-live?ids=2,5,9 shows EXACTLY the relevant clips
    # chat returned (a filtered subset), in order. _parse_ids_query raises 400 on
    # malformed ids and returns None when no filter is requested (absent/empty ?ids=,
    # same convention as the export endpoints); [] means "filter, but no rows". Cap
    # so a huge ?ids can't unbound the batched fetch (H16).
    id_list = _parse_ids_query(ids)
    if id_list is not None:
        records = _get_light_records_by_ids(id_list[:500])
        for rec in records:
            _resolve_record(rec)
        tags_by_id = _get_tags_bulk([rec["id"] for rec in records])
        for rec in records:
            rec["tags"] = tags_by_id.get(rec["id"], [])
        return {"items": records, "total": len(records), "search": True}
    if q:
        enriched = []
        search_warning = None
        import vectordb as vdb
        # audit M19: search used to ignore `offset` entirely (page 2 == page 1).
        # Collect up to offset+limit matches, then slice the requested page.
        # Capped so a huge ?offset can't inflate n_results / SQL LIMIT (audit
        # H13-class DoS); search hits past 2000 are noise anyway.
        needed = min(offset + limit, 2000)

        def _passes_filters(rec: dict) -> bool:
            # Applied to the ENRICHED record (which has real lang/rating), not the
            # raw vector hit — vectordb results carry no `rating`, so filtering on
            # them dropped every semantic hit under `rating=good` and silently fell
            # through to an unfiltered SQL LIKE (H8).
            if lang and rec.get("lang") != lang:
                return False
            rv = rec.get("rating")
            if rating == "unrated" and rv is not None:
                return False
            if rating and rating != "unrated" and rv != rating:
                return False
            # audit H14: media_type was silently ignored on the search branch
            # (?q=...&media_type=video still returned audio). Mirror the SQL
            # path's ext buckets.
            ext = (rec.get("ext") or "").lower()
            if media_type == "video" and ext not in _VIDEO_EXTS:
                return False
            if media_type == "audio" and ext not in _AUDIO_EXTS:
                return False
            return True

        # Try semantic search first (requires vectordb with embeddings)
        try:
            raw = vdb.search(q, n_results=needed * 3)
            seen = set()
            ordered_ids = []
            hit_by_id = {}
            for r in raw:
                mid = int(r["media_id"])
                if mid in seen:
                    continue
                seen.add(mid)
                ordered_ids.append(mid)
                hit_by_id[mid] = r
            # audit H16: one batched LIGHT_COLS fetch instead of per-hit SELECT *
            for rec in _get_light_records_by_ids(ordered_ids):
                if not _passes_filters(rec):
                    continue
                _resolve_record(rec)
                hit = hit_by_id[rec["id"]]
                rec["score"] = hit.get("score", 0)
                rec["excerpt"] = hit.get("excerpt", "")
                enriched.append(rec)
                if len(enriched) >= needed:
                    break
        except vdb.EmbeddingDimensionMismatch as exc:
            # Don't silently SQL-degrade a dim mismatch — log it and surface a hint
            # so the operator knows semantic search is off until they rebuild.
            _logging.getLogger(__name__).warning("semantic search degraded: %s", exc)
            search_warning = str(exc)
        except Exception as exc:
            # audit L8: was a bare `except: pass` — Ollama being down degraded to
            # SQL search with zero signal anywhere. Log + flag the degradation.
            _logging.getLogger(__name__).warning(
                "semantic search failed, falling back to SQL: %s", exc
            )
            search_warning = "semantic search unavailable (SQL fallback used)"

        # Fallback: SQL text search (filename, transcript, tags) — same lang/rating
        # filter applied so a degraded search still honors the active filters.
        if not enriched:
            seen_ids = set()
            like = f"%{q}%"
            # audit H17: push the active filters into WHERE and bound the scan —
            # the old query LIKE-scanned the whole table, built every matching
            # record, then threw away everything past `limit`.
            filter_sql = ""
            filter_params: list = []
            if lang:
                filter_sql += " AND lang = ?"
                filter_params.append(lang)
            if rating == "unrated":
                filter_sql += " AND rating IS NULL"
            elif rating:
                filter_sql += " AND rating = ?"
                filter_params.append(rating)
            if media_type == "video":
                filter_sql += " AND ext IN ({0})".format(",".join("?" * len(_VIDEO_EXTS)))
                filter_params.extend(sorted(_VIDEO_EXTS))
            elif media_type == "audio":
                filter_sql += " AND ext IN ({0})".format(",".join("?" * len(_AUDIO_EXTS)))
                filter_params.extend(sorted(_AUDIO_EXTS))
            with db.get_conn() as conn:
                rows = conn.execute(
                    f"SELECT {db.LIGHT_COLS} FROM media "
                    "WHERE (filename LIKE ? OR transcript LIKE ?)" + filter_sql +
                    " ORDER BY id LIMIT ?",
                    (like, like, *filter_params, needed),
                ).fetchall()
                for r in rows:
                    rec = dict(r)
                    _resolve_record(rec)
                    enriched.append(rec)
                    seen_ids.add(rec["id"])

            # Also search by tag name (bounded — audit H17)
            if len(enriched) < needed:
                with db.get_conn() as conn:
                    tag_rows = conn.execute(
                        "SELECT DISTINCT media_id FROM tags WHERE name LIKE ? LIMIT ?",
                        (like, needed * 3),
                    ).fetchall()
                tag_ids = [tr["media_id"] for tr in tag_rows if tr["media_id"] not in seen_ids]
                for rec in _get_light_records_by_ids(tag_ids):
                    if not _passes_filters(rec):
                        continue
                    _resolve_record(rec)
                    enriched.append(rec)
                    seen_ids.add(rec["id"])
                    if len(enriched) >= needed:
                        break

        # audit M19: slice the requested page; total = bounded match count (the
        # same "items seen so far" semantic for both search sub-paths).
        items = enriched[offset:offset + limit]
        # audit H15: one bulk tag query for the returned page only
        tags_by_id = _get_tags_bulk([rec["id"] for rec in items])
        for rec in items:
            rec["tags"] = tags_by_id.get(rec["id"], [])
        resp = {"items": items, "total": len(enriched), "search": True}
        if search_warning:
            resp["search_degraded"] = True
            resp["warning"] = search_warning
        return resp

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
    # Attach tags to each record — one bulk query for the page instead of one
    # SQLite connection per row (audit H15).
    tags_by_id = _get_tags_bulk([rec["id"] for rec in records])
    for rec in records:
        _resolve_record(rec)
        rec["tags"] = tags_by_id.get(rec["id"], [])

    return {"items": records, "total": total, "search": False}


@router.get("/api/media/{media_id}")
def get_media_detail(
    media_id: int,
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """Get full media record with tags and frames."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    _resolve_record(rec)
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
    # User-facing tags: screen quality-defect noise + keep only the top-N most
    # confident (focus-weighted) tags, so a clip doesn't dump 10+ tags on the user.
    top = set(tag_quality.rank_media_tags(rec.get("frame_tags_parsed") or []))
    all_tags = tag_quality.filter_tag_records(db.get_tags(media_id))
    rec["tags"] = [t for t in all_tags if t["name"] in top] if top else all_tags
    # Optional LLM-canonicalized tag list (populated by `ingest.py --canonicalize-tags`).
    # Returned alongside raw tags so the UI can toggle raw ↔ canonical; null until run.
    if rec.get("canonical_tags"):
        try:
            rec["canonical_tags"] = json.loads(rec["canonical_tags"])
        except Exception:
            rec["canonical_tags"] = None
    # fable-audit round-5 #26 (codex-verified): words_json (word-level timing JSON,
    # multi-MB) has NO frontend consumer here — the inspector's transcript seek uses
    # segments_json (kept), and word-level data is served separately via
    # /api/media/{id}/remotion-props. Drop only words_json from this per-click
    # response so it isn't shipped over NAS/Tailscale on every arrow-key. The shared
    # db.get_record_by_id is untouched (export/retranscribe/remotion still need it).
    rec.pop("words_json", None)
    return rec


@router.get("/api/media/{media_id}/waveform")
def get_media_waveform(
    media_id: int,
    bins: int = 60,
    _tok: dict = Depends(require_scopes("media_read")),
):
    """Return pre-computed audio peaks (0..1) for the inspector waveform.
    Cached per (id, bins) under waveforms/<id>_<bins>.json."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    # Clamp BEFORE the no-audio early return — otherwise ?bins=999999 on a
    # no-audio clip allocates a ~8MB [0.0]*999999 list (DoS).
    bins = max(8, min(500, bins))
    if not rec.get("has_audio"):
        return {"media_id": media_id, "bins": bins, "peaks": [0.0] * bins}
    cache_dir = BASE_DIR / "waveforms"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{media_id}_{bins}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache_path.unlink(missing_ok=True)
    file_path = Path(_resolve_media_path(rec["path"]))
    if not file_path.exists():
        raise HTTPException(404, "找不到檔案")
    peaks = _compute_waveform(str(file_path), bins)
    if peaks is None:
        raise HTTPException(500, "波形計算失敗")
    payload = {"media_id": media_id, "bins": bins, "peaks": peaks}
    try:
        # audit L5: atomic write (tmp + os.replace) — a direct write_text could
        # leave a concurrent reader a torn/partial JSON. pid-suffixed tmp name so
        # two concurrent writers don't tear each other's tmp file either.
        tmp_path = cache_path.with_name("{0}.{1}.tmp".format(cache_path.name, os.getpid()))
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp_path, cache_path)
    except Exception:
        pass
    return payload


def _compute_waveform(path: str, bins: int):
    """Decode mono 8kHz PCM via ffmpeg and return `bins` peak-amplitude values 0..1."""
    import subprocess
    import numpy as np
    cmd = [
        config.FFMPEG_PATH, "-v", "quiet", "-i", path,
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


@router.get("/api/media/{media_id}/scenes")
def get_media_scenes(
    media_id: int,
    _tok: dict = Depends(require_scopes("media_read")),
):
    # Per-scene derivation lives in the scenes leaf — mcp_server needs the same
    # shape and cannot import this module (it refuses `server`/fastapi), so a
    # copy here would fork the contract. See scenes.py; the body is byte-frozen
    # by tests/test_scenes_contract.py.
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    return _scenes_payload(media_id, rec, db.get_frames(media_id))


def _segments_payload(segments_json):
    """Project the stored `segments_json` TEXT column onto the stable
    {start,end,text} subset. Never raises — a corrupt column degrades to [] rather
    than 500ing (mirrors mcp_server._json_rows and export.py's defensive parse)."""
    if not segments_json:
        return []
    try:
        rows = json.loads(segments_json)
    except (ValueError, TypeError):
        return []
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        seg = {"start": r.get("start"), "end": r.get("end"), "text": r.get("text")}
        # A4: surface speaker_id only when present, so non-diarized clips keep the
        # exact {start,end,text} shape (backward-compatible with existing consumers).
        if r.get("speaker_id"):
            seg["speaker_id"] = r.get("speaker_id")
        out.append(seg)
    return out


@router.get("/api/media/{media_id}/segments")
def get_media_segments(
    media_id: int,
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """Sentence-level transcript timecodes for one clip: `[{start,end,text}]`.

    The lightweight cutting surface for downstream edit agents (smart-edit). The
    detail route ships `segments_json` only as a raw string and drops words_json
    for transport size, so an agent that just needs to place an in/out on a quote
    had to re-parse the whole record. This returns the projected segment array and
    NOTHING else — no words (word-level lives in /remotion-props), no frames/tags.
    Sentence granularity is enough to locate and cut a line; word-level would only
    bloat the payload (the same reason MCP get_transcript defaults words off)."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    return _segments_payload(rec.get("segments_json"))


@router.get("/api/media/{media_id}/chapters")
def get_media_chapters(
    media_id: int,
    format: str = "youtube",
    _tok: dict = Depends(require_scopes("media_read")),
):
    """ProChapter-style chapter markers from the clip's scene frames.

    `format=youtube`   → `MM:SS Title` lines (first marker forced to 0:00).
    `format=ffmetadata` → ffmpeg chapter file (embed with -map_metadata).
    """
    if format not in ("youtube", "ffmetadata"):
        raise HTTPException(422, "format must be 'youtube' or 'ffmetadata'")
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    import export
    text = export.build_chapters(media_id, fmt=format)
    count = text.count("[CHAPTER]") if format == "ffmetadata" else (len(text.splitlines()) if text else 0)
    return {"media_id": media_id, "format": format, "chapters": text, "count": count}


@router.patch("/api/media/{media_id}/rating")
def update_rating(
    media_id: int,
    body: RatingUpdate,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Set or clear rating for a media asset.

    PATCH semantics (audit M20): a field OMITTED from the body is left
    untouched; an explicit null clears it. PATCH {rating:'good'} used to
    silently wipe the stored note (PUT semantics in a PATCH endpoint).
    """
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    provided = body.model_fields_set  # audit M20: omitted vs explicit-null
    sets, params = [], []
    if "rating" in provided:
        sets.append("rating = ?")
        params.append(body.rating)
    if "note" in provided:
        sets.append("rating_note = ?")
        params.append(body.note)
    if sets:
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE media SET {0} WHERE id = ?".format(", ".join(sets)),
                (*params, media_id),
            )
    new_rating = body.rating if "rating" in provided else rec.get("rating")
    new_note = body.note if "note" in provided else rec.get("rating_note")
    return {"ok": True, "rating": new_rating, "note": new_note}


@router.patch("/api/media/{media_id}/inout")
def update_inout(
    media_id: int,
    body: InOutUpdate,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    """Persist the inspector IN/OUT trim points (seconds) for a clip.

    The marks were UI-ephemeral — lost on clip-switch, and invisible to the
    timeline export. Persisting them lets the inspector restore a clip's range on
    re-open and lets the multi-clip export (D2) assemble a cut list from the marked
    sub-clips. PATCH semantics (same as rating): an OMITTED field is left untouched;
    an explicit null clears that mark.
    """
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    provided = body.model_fields_set
    new_in = body.in_point if "in_point" in provided else rec.get("in_point")
    new_out = body.out_point if "out_point" in provided else rec.get("out_point")
    # An inverted window (in ≥ out) exports an empty/negative range downstream —
    # reject it rather than silently persisting a range that yields nothing.
    if new_in is not None and new_out is not None and new_in >= new_out:
        raise HTTPException(422, "in_point 必須小於 out_point")
    sets, params = [], []
    if "in_point" in provided:
        sets.append("in_point = ?")
        params.append(body.in_point)
    if "out_point" in provided:
        sets.append("out_point = ?")
        params.append(body.out_point)
    if sets:
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE media SET {0} WHERE id = ?".format(", ".join(sets)),
                (*params, media_id),
            )
    return {"ok": True, "in_point": new_in, "out_point": new_out}


@router.get("/api/media/{media_id}/tags")
def get_tags(
    media_id: int,
    _tok: dict = Depends(require_scopes("videos_read")),
):
    return db.get_tags(media_id)


@router.post("/api/media/{media_id}/tags")
def add_tag(
    media_id: int,
    body: TagCreate,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    db.add_tag(media_id, body.name, body.source)
    return {"ok": True, "tags": db.get_tags(media_id)}


@router.delete("/api/media/{media_id}/tags/{tag_name}")
def remove_tag(
    media_id: int,
    tag_name: str,
    _tok: dict = Depends(require_scopes("videos_write")),
):
    db.remove_tag(media_id, tag_name)
    return {"ok": True, "tags": db.get_tags(media_id)}


@router.get("/api/media/{media_id}/remotion-props")
def get_remotion_props(
    media_id: int,
    _tok: dict = Depends(require_scopes("media_read")),
):
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


@router.post("/api/media/{media_id}/retranscribe")
def retranscribe_media(
    media_id: int,
    body: RetranscribeRequest,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Re-run Whisper with specified language."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    media_path = _resolve_media_path(rec.get("path", ""))
    if not Path(media_path).exists():
        # fable-audit 2026-07-12: don't leak the resolved absolute path in the
        # error body — surface the PROJECT_ROOT-relative/basename form (Phase 16.2).
        raise HTTPException(400, f"找不到媒體檔案：{_display_path(rec.get('path') or '')}")
    try:
        import transcribe as tr
        text, lang, segments, words = tr.transcribe(media_path, language=body.language)
    except Exception as e:
        # Extraction/transcription failed — leave the existing transcript untouched
        # rather than blanking it (audit H1/H2).
        raise HTTPException(500, "retranscribe 失敗：{0}".format(e))
    # An empty result means "no speech"; for a clip that already has a transcript
    # that's almost always a regression (transient decode failure), not intent —
    # refuse to overwrite a good transcript with nothing (audit H1).
    if not (text or "").strip() and (rec.get("transcript") or "").strip():
        raise HTTPException(422, "transcribe 回空，拒絕覆寫既有逐字稿（可能是音訊擷取失敗）")
    active_lang = lang or body.language
    seg_json = json.dumps(segments, ensure_ascii=False) if segments else None
    words_json = json.dumps(words, ensure_ascii=False) if words else None
    # fable-audit round-5 #17: when retranscribing into the SAME language, the G2
    # archive-outgoing below writes the old text to transcripts[(id, lang)] and then
    # the new-active archive overwrites that very row — so a hand-corrected transcript
    # would be destroyed with no recoverable copy. Snapshot it to the durable
    # correction-backups first (restorable via the same revert). Cross-language
    # retranscribes are already safe (the outgoing language's archive row survives).
    backup_name = None
    if active_lang == rec.get("lang") and (rec.get("transcript") or "").strip():
        backup_name = corrections._write_backup(
            [{"id": media_id, "transcript": rec.get("transcript"),
              "segments_json": rec.get("segments_json"),
              "words_json": rec.get("words_json"), "lang": rec.get("lang")}],
            [{"op": "retranscribe", "language": active_lang}],
        )
    with db.get_conn() as conn:
        # G2: archive the OUTGOING transcript first so retranscribing into a
        # different language preserves the previous one (else zh→en would lose zh).
        if (rec.get("transcript") or "").strip() and rec.get("lang"):
            db.upsert_transcript(media_id, rec["lang"], rec.get("transcript"),
                                 rec.get("segments_json"), rec.get("words_json"), _conn=conn)
        conn.execute(
            "UPDATE media SET transcript=?, lang=?, segments_json=?, words_json=? WHERE id=?",
            (text, active_lang, seg_json, words_json, media_id),
        )
        # archive the new active language too (its row mirrors media.*).
        db.upsert_transcript(media_id, active_lang, text, seg_json, words_json, _conn=conn)
    return {"ok": True, "transcript_length": len(text), "language": active_lang, "backup": backup_name}


@router.get("/api/media/{media_id}/transcripts")
def list_transcripts(media_id: int, _tok: dict = Depends(require_scopes("videos_read"))):
    """All archived transcript languages for a clip (Phase 9.7 G2). The active
    language (media.lang) shows the LIVE media.* content; others show their
    archived content. Lazily backfills the active language on first read so
    pre-feature / ingest-created transcripts appear without an explicit retranscribe."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    active_lang = rec.get("lang")
    rows = db.get_transcripts(media_id)
    have = {r["lang"] for r in rows}
    if (rec.get("transcript") or "").strip() and active_lang and active_lang not in have:
        db.upsert_transcript(media_id, active_lang, rec.get("transcript"),
                             rec.get("segments_json"), rec.get("words_json"))
        rows = db.get_transcripts(media_id)
    for r in rows:
        if r["lang"] == active_lang:
            # the active row mirrors the live cache (authoritative for search/export)
            r["transcript"] = rec.get("transcript")
            r["segments_json"] = rec.get("segments_json")
            r["words_json"] = rec.get("words_json")
            r["active"] = True
        else:
            r["active"] = False
    return {"active_lang": active_lang, "transcripts": rows}


@router.post("/api/media/{media_id}/transcript/activate")
def activate_transcript(
    media_id: int,
    body: ActivateLangRequest,
    request: Request,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
    """Make an archived language the active transcript (Phase 9.7 G2) — copies it
    into media.* so search / export / subtitles use it. The previously-active
    language stays archived and switchable."""
    _assert_same_site(request)
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    row = db.get_transcript(media_id, body.lang)
    if not row:
        raise HTTPException(404, "該語言尚無轉錄")
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE media SET transcript=?, lang=?, segments_json=?, words_json=? WHERE id=?",
            (row["transcript"], row["lang"], row["segments_json"], row["words_json"], media_id),
        )
    return {"ok": True, "active_lang": body.lang}


@router.post("/api/media/{media_id}/retry-vision")
def retry_vision(
    media_id: int,
    _tok: dict = Depends(require_scopes("ingest_write")),
):
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
    frame_paths = [_resolve_media_path(f["thumbnail_path"]) for f in empty_frames]

    # Phase 1: try primary vision model
    results = vis.describe_frames(frame_paths)
    failed = [i for i, r in enumerate(results) if r.get("error") or not r.get("description")]

    # Phase 2: fallback to lighter model for failed frames. Config-driven (unified
    # with ingest.py) and skipped gracefully when the fallback model isn't
    # installed, instead of 404-ing once per failed frame.
    if failed and config.VISION_FALLBACK_MODEL and vis.model_available(config.VISION_FALLBACK_MODEL):
        fallback_model = config.VISION_FALLBACK_MODEL
        # round-5 #50: pass the fallback model straight through to _call_vision.
        # The old vis.VISION_MODEL global-swap was dead (_call_vision re-read the
        # model from settings) — and being a module global it also raced across
        # concurrent retry-vision calls. Threading the arg fixes both.
        retry_paths = [frame_paths[i] for i in failed]
        retry_results = vis.describe_frames(retry_paths, model=fallback_model)
        for idx, retry_r in zip(failed, retry_results):
            if retry_r.get("description") and not retry_r.get("error"):
                results[idx] = retry_r

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
                        # _conn=conn: add_tag must reuse the open write txn, else it
                        # opens a 2nd connection that deadlocks on our own writer lock
                        # (audit C1 — 30s wait then the whole patch rolls back / 500).
                        db.add_tag(media_id, tag_name, source="auto", _conn=conn)
                patched += 1
        # Update legacy frame_tags. Read frames through the SAME conn so we see the
        # UPDATEs just written above, not a stale pre-txn snapshot (audit M1).
        all_frames = db.get_frames(media_id, _conn=conn)
        frame_tags = [{"description": f.get("description", ""), "tags": f.get("tags", "").split(",") if f.get("tags") else []} for f in all_frames]
        frame_tags_json = json.dumps(frame_tags, ensure_ascii=False)
        # max over all scored frames (not the first), and leave the prior score
        # untouched when nothing scored rather than nulling it (audit M1).
        scores = [db.compute_editability(fr) for fr in all_frames if fr.get("focus_score") is not None]
        editability_score = max(scores) if scores else None
        conn.execute(
            "UPDATE media SET frame_tags=?, editability_score=COALESCE(?, editability_score) WHERE id=?",
            (frame_tags_json, editability_score, media_id),
        )

    still_empty = sum(1 for vr in results if not vr.get("description") or vr.get("error"))
    return {
        "ok": still_empty == 0,
        "patched": patched,
        "still_empty": still_empty,
        "total_frames": len(empty_frames),
    }
