"""Phase 9.7 G6 — structured query builder.

Turns a JSON query spec into a parameterized SQL WHERE clause (+ a list of
semantic terms the caller runs through the vector index separately). Every
field maps to a known media column or the tags table — there is no free-form
column name, and every value is bound as a parameter, so a hostile spec can't
inject SQL or read arbitrary columns.

Spec shape::

    {
      "match": "all" | "any",          # AND / OR across conditions (default all)
      "conditions": [
        {"field": "tag",        "op": "contains", "value": "貓"},
        {"field": "transcript", "op": "contains", "value": "咖啡"},
        {"field": "camera",     "op": "eq",       "value": "ILME-FX30"},
        {"field": "rating",     "op": "eq",       "value": "good"},   # 'unrated' = NULL
        {"field": "duration",   "op": "range",    "value": [10, 120]},  # seconds
        {"field": "date",       "op": "range",    "value": ["2026-01-01", null]},
        {"field": "media_type", "op": "eq",       "value": "video"},
        {"field": "semantic",   "op": "contains", "value": "海邊的日落"}
      ]
    }
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class QueryError(ValueError):
    """Raised on an unknown field, disallowed op, or malformed value."""


# field -> (column, {allowed ops}, kind). kind drives value handling.
#   text    : contains (LIKE %v%) / eq (= v)
#   numeric : range ([min,max], either bound may be null)
#   enum    : eq, special NULL handling for rating='unrated'
#   tag     : subquery against tags
#   bucket  : ext IN (video/audio buckets)
#   semantic: not SQL — collected for the vector index
_FIELDS: Dict[str, Tuple[Optional[str], set, str]] = {
    "tag": (None, {"contains", "eq"}, "tag"),
    "transcript": ("transcript", {"contains"}, "text"),
    "filename": ("filename", {"contains", "eq"}, "text"),
    "camera": ("camera_model", {"contains", "eq"}, "text"),
    "content_type": ("content_type", {"contains", "eq"}, "text"),
    "lang": ("lang", {"eq"}, "text"),
    "rating": ("rating", {"eq"}, "enum"),
    "iso": ("iso", {"range"}, "numeric"),
    "duration": ("duration_s", {"range"}, "numeric"),
    "date": ("processed_at", {"range"}, "numeric"),
    "media_type": (None, {"eq"}, "bucket"),
    "semantic": (None, {"contains"}, "semantic"),
}

# ext buckets — kept in sync with server._VIDEO_EXTS / _AUDIO_EXTS.
_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".insv", ".360")
_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg")


def _one_condition(cond: Dict[str, Any]) -> Tuple[Optional[str], List[Any], Optional[str]]:
    """Compile one condition → (sql_fragment, params, semantic_term).

    Exactly one of (sql_fragment, semantic_term) is non-None.
    """
    if not isinstance(cond, dict):
        raise QueryError("each condition must be an object")
    field = cond.get("field")
    op = cond.get("op")
    value = cond.get("value")
    if field not in _FIELDS:
        raise QueryError("unknown field: {0}".format(field))
    column, allowed_ops, kind = _FIELDS[field]
    if op not in allowed_ops:
        raise QueryError("op '{0}' not allowed for field '{1}'".format(op, field))

    if kind == "semantic":
        term = str(value or "").strip()
        if not term:
            raise QueryError("semantic condition needs a non-empty value")
        return None, [], term

    if kind == "tag":
        v = str(value or "").strip()
        if not v:
            raise QueryError("tag condition needs a non-empty value")
        if op == "eq":
            return "id IN (SELECT media_id FROM tags WHERE name = ?)", [v], None
        return "id IN (SELECT media_id FROM tags WHERE name LIKE ?)", ["%{0}%".format(v)], None

    if kind == "bucket":
        v = str(value or "").lower()
        if v == "video":
            exts = _VIDEO_EXTS
        elif v == "audio":
            exts = _AUDIO_EXTS
        else:
            raise QueryError("media_type must be 'video' or 'audio'")
        placeholders = ",".join("?" * len(exts))
        return "LOWER(ext) IN ({0})".format(placeholders), list(exts), None

    if kind == "enum":  # rating
        v = str(value or "").strip()
        if v == "unrated":
            return "rating IS NULL", [], None
        return "{0} = ?".format(column), [v], None

    if kind == "text":
        v = str(value or "").strip()
        if not v:
            raise QueryError("{0} condition needs a non-empty value".format(field))
        if op == "eq":
            return "{0} = ?".format(column), [v], None
        return "{0} LIKE ?".format(column), ["%{0}%".format(v)], None

    if kind == "numeric":  # range, value = [min, max] (either may be null)
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise QueryError("{0} range needs a [min, max] pair".format(field))
        lo, hi = value
        frags, params = [], []
        if lo is not None:
            frags.append("{0} >= ?".format(column))
            params.append(lo)
        if hi is not None:
            frags.append("{0} <= ?".format(column))
            params.append(hi)
        if not frags:
            raise QueryError("{0} range needs at least one bound".format(field))
        return "(" + " AND ".join(frags) + ")", params, None

    raise QueryError("unsupported field kind: {0}".format(kind))  # pragma: no cover


def compile_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Compile a full spec → {where, params, semantic_terms, match}.

    `where` is the SQL after WHERE (empty string if no SQL conditions). The
    caller runs each semantic term through the vector index and combines the
    media-id sets using `match` (all = intersect, any = union).
    """
    if not isinstance(spec, dict):
        raise QueryError("query spec must be an object")
    match = (spec.get("match") or "all").lower()
    if match not in ("all", "any"):
        raise QueryError("match must be 'all' or 'any'")
    conditions = spec.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise QueryError("query needs at least one condition")
    if len(conditions) > 32:
        raise QueryError("too many conditions (max 32)")

    sql_frags: List[str] = []
    params: List[Any] = []
    semantic_terms: List[str] = []
    for cond in conditions:
        frag, frag_params, term = _one_condition(cond)
        if term is not None:
            semantic_terms.append(term)
        else:
            sql_frags.append(frag)
            params.extend(frag_params)

    joiner = " AND " if match == "all" else " OR "
    where = joiner.join(sql_frags)
    return {
        "where": where,
        "params": params,
        "semantic_terms": semantic_terms,
        "match": match,
    }
