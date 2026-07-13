"""Bulk media-record fetch helpers for the API layer.

R5-25 / round-5 #51: the APIRouter split peels the media route group into
routers/media.py, but two of its per-page bulk-fetch helpers — `_get_tags_bulk`
(audit H15) and `_get_light_records_by_ids` (audit H16) — are ALSO used by the
search group (`structured_query` / `search_all`) that stays in server.py for a
later peel. If media took them, server would `from routers.media import ...` and,
since server imports the routers, that's a router→server→router cycle
(partially-initialized module → ImportError).

The fix mirrors pathres.py: extract the shared helpers into a leaf module that
both routers/media.py and server.py import. This depends only on `db` (+ stdlib),
so it sits at the bottom of the import graph. server.py re-exports these names for
backward compatibility (existing call sites + tests referencing
`server._get_tags_bulk` etc. keep working unchanged).
"""
import db


def _get_tags_bulk(media_ids) -> dict:
    """audit H15: tags for many media ids in ONE query — the per-row db.get_tags
    loop opened a fresh SQLite connection per record (501 connections for a
    500-row page). Returns {media_id: [tag dicts]} matching db.get_tags shape."""
    out = {int(m): [] for m in media_ids}
    if not out:
        return out
    ids = list(out.keys())
    placeholders = ",".join("?" * len(ids))
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, media_id, name, source FROM tags "
            "WHERE media_id IN ({0}) ORDER BY name".format(placeholders),
            ids,
        ).fetchall()
    for r in rows:
        out[r["media_id"]].append({"id": r["id"], "name": r["name"], "source": r["source"]})
    return out


def _get_light_records_by_ids(media_ids) -> list:
    """audit H16: LIGHT_COLS records for many ids in ONE query, preserving input
    order. The semantic-search path did a per-hit SELECT * (words_json /
    segments_json — tens of MB across a page) plus one connection per hit."""
    ids = [int(m) for m in media_ids]
    if not ids:
        return []
    # fable-audit round-5 #21: chunk the IN() so a broad structured/semantic query
    # (thousands of matching ids) doesn't blow past SQLite's max variable count
    # (999 on old builds) → "too many SQL variables" HTTP 500.
    by_id = {}
    _CHUNK = 500
    with db.get_conn() as conn:
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start:start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT {db.LIGHT_COLS} FROM media WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            for r in rows:
                by_id[r["id"]] = dict(r)
    return [by_id[i] for i in ids if i in by_id]
