"""Dashboard analytics routes (R5-25 / round-5 #51 router split).

The read-only aggregate endpoints behind the dashboard: library stats (+ real
disk usage + top tags + registered project name), the full tag cloud, Smart
Collections, and the duration-by-lang / size-by-ext breakdowns. Group-local
helpers `_current_project_registry_name` (get_stats) and `_thumb_url`
(list_collections) move here with them. `BASE_DIR` (config) replaces
server.ROOT for the disk-usage fallback. Imports auth + db + config +
tag_quality/tag_aliases/smart_collections/projects — no server import, no cycle.
"""
from fastapi import APIRouter, Depends

import config
import db
import projects as project_registry
import smart_collections
import tag_aliases
import tag_quality
from auth import require_scopes
from config import BASE_DIR

router = APIRouter()


def _current_project_registry_name():
    try:
        root_key = str(config.PROJECT_ROOT.expanduser().resolve(strict=False)).casefold()
        for p in project_registry.discover_projects():
            if p.key() == root_key:
                return p.name
    except Exception:
        pass
    return None


def _thumb_url(thumbnail_path):
    """Absolute fs thumbnail_path → served /thumbnails/<basename> URL (or None)."""
    if not thumbnail_path:
        return None
    base = str(thumbnail_path).replace("\\", "/").rsplit("/", 1)[-1]
    return "/thumbnails/{0}".format(base)


@router.get("/api/stats")
def get_stats(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """Aggregate stats for dashboard."""
    stats = db.get_stats()
    stats["rating"] = db.get_rating_stats()
    # Real disk usage of the volume holding the arkiv data — feeds the sidebar's
    # Storage footer (was a hardcoded "4.8 TB · 12 TB · 40%" placeholder). Best
    # effort: a stat() failure (e.g. path vanished) must not 500 the dashboard.
    try:
        import shutil
        _db_path = db.get_db_path()  # R5-23 (#54): SSOT accessor, not config value
        du = shutil.disk_usage(_db_path.parent if _db_path else BASE_DIR)
        stats["disk"] = {
            "used_gb": round(du.used / 1e9, 1),
            "total_gb": round(du.total / 1e9, 1),
            "pct": round(du.used / du.total * 100) if du.total else 0,
        }
    except Exception:
        stats["disk"] = None
    # Screen quality-defect tags here too (Codex review P2) — index.html and any
    # stats-driven cloud read top_tags. Over-fetch then filter so we still get 10
    # real tags even if some of the top entries were noise.
    stats["top_tags"] = tag_quality.filter_tag_records(db.get_top_tags(40))[:10]
    # Real project name (basename of PROJECT_ROOT) so the UI shows the loaded
    # library instead of a hardcoded demo name. Multi-library installs (one .arkiv
    # per project) each report their own name.
    stats["project"] = config.PROJECT_ROOT.name if config.PROJECT_ROOT else None
    # The REGISTRY name for the currently-loaded project (may differ from the dir
    # basename — a library registered as "恬馨庫" can live in a dir named "tianxin").
    # A cross-library 精選集 item is keyed by registry name, so the grid's
    # "加入精選集" needs this — null when the current project isn't registered
    # (then a grid-added item couldn't be resolved back → the UI disables the add).
    stats["project_registered_name"] = _current_project_registry_name()
    return stats


@router.get("/api/tags")
def get_all_tags(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    """All unique tag names with counts. Quality-defect tags (模糊/低解析度…)
    are screened out, variant-char spellings (人群/人羣) merged (tag_quality), then
    near-synonyms folded to their preferred label via the reviewed alias map
    (tag_aliases) — a no-op until `ingest --propose-aliases`/`--apply-aliases`."""
    return tag_aliases.fold_records(tag_quality.merge_tag_records(db.get_all_tag_names()))


@router.get("/api/collections")
def list_collections(
    _tok: dict = Depends(require_scopes("collections_read")),
):
    """Smart Collections — classify every media item against the Tier-1
    definitions (smart_collections.DEFAULT_COLLECTIONS) and group the results.

    Rule-driven (not ML clustering): see smart_collections.py. Returns one entry
    per collection that has >=1 member, each with its member media (id/filename/
    thumb/duration/score), sorted by score desc. Membership is non-exclusive.
    """
    defs = smart_collections.DEFAULT_COLLECTIONS
    buckets = {c.key: {"key": c.key, "title": c.title, "category": c.category, "items": []} for c in defs}

    # audit L13: classify reads only these columns (frame_tags + media-level
    # aggregates + gps + duration/audio) — get_all_records() was SELECT *,
    # hauling words_json/segments_json/transcript for the entire library.
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, thumbnail_path, duration_s, has_audio, "
            "frame_tags, content_type, atmosphere, energy, gps_lat, gps_lon, "
            "rating, processed_at "
            "FROM media ORDER BY id"
        ).fetchall()
    for rec in (dict(r) for r in rows):
        for hit in smart_collections.classify(rec, defs):
            buckets[hit["key"]]["items"].append({
                "id": rec["id"],
                "filename": rec.get("filename"),
                "thumb": _thumb_url(rec.get("thumbnail_path")),
                "duration_s": rec.get("duration_s"),
                "score": hit["score"],
            })

    out = []
    for b in buckets.values():
        if not b["items"]:
            continue
        b["items"].sort(key=lambda r: r["score"], reverse=True)
        b["count"] = len(b["items"])
        out.append(b)
    out.sort(key=lambda c: c["count"], reverse=True)
    return {"collections": out, "total": len(out)}


@router.get("/api/duration-by-lang")
def duration_by_lang(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT lang, SUM(duration_s) as total_s, COUNT(*) as count "
            "FROM media WHERE lang IS NOT NULL GROUP BY lang ORDER BY total_s DESC"
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/api/size-by-ext")
def size_by_ext(
    _tok: dict = Depends(require_scopes("videos_read")),
):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT ext, SUM(size_mb) as total_mb, COUNT(*) as count "
            "FROM media GROUP BY ext ORDER BY total_mb DESC"
        ).fetchall()
        return [dict(r) for r in rows]
