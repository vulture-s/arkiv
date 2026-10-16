"""search_syntax.py — the search box's `key:value` filters, actually applied.

The search box placeholder, the website and the docs all advertise queries like
`"格爾木氧氣" camera:a7s3 tag:cycling lang:zh`. Nothing parsed them: the whole
string went to vector search / LIKE as text, so `camera:a7s3` matched nothing (no
clip's text contains that token) or, on the semantic leg, drifted to whatever was
nearest. This splits a query into free text and typed conditions, and the
conditions reuse query_builder's field table so the search box, /api/search/query
and the MCP tool all mean the same thing by `tag:` or `camera:`.

Grammar (whitespace-separated tokens):
  key:value / key:"two words"   a filter, if `key` is one of FILTER_KEYS
  "quoted phrase"               free text (quotes removed)
  anything else                 free text, verbatim — including `12:30` or an
                                unknown `foo:bar`, so ordinary text with a colon
                                is never swallowed
Repeated keys AND together (`tag:海邊 tag:逆光` = both tags).

Leaf module: imports query_builder only.
"""
import calendar
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import query_builder
from query_builder import QueryError

# key -> how to turn the value into a query_builder condition
FILTER_KEYS = ("tag", "camera", "lang", "rating", "type", "shot")

_RATING_ALIASES = {
    "good": "good", "review": "review", "rev": "review",
    "ng": "ng", "n·g": "ng", "n.g": "ng", "unrated": "unrated",
}
_TYPES = ("video", "audio", "image")

_TOKEN = re.compile(r'([A-Za-z_]+):"([^"]*)"|([A-Za-z_]+):(\S+)|"([^"]*)"|(\S+)')
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR = re.compile(r"^\d{4}$")


def _shot_window(value: str) -> List[Optional[str]]:
    """`2025` / `2025-10` / `2025-10-03` / `2025-10-01..2025-10-15` → [first, last] day."""
    if ".." in value:
        lo, hi = value.split("..", 1)
        for part in (lo, hi):
            if part and not _DAY.match(part):
                raise QueryError("shot range takes YYYY-MM-DD..YYYY-MM-DD: {0}".format(value))
        if not lo and not hi:
            raise QueryError("shot range needs at least one end: {0}".format(value))
        return [lo or None, hi or None]
    if _DAY.match(value):
        return [value, value]
    m = _MONTH.match(value)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            raise QueryError("shot month out of range: {0}".format(value))
        last = calendar.monthrange(year, month)[1]
        return ["{0}-01".format(value), "{0}-{1:02d}".format(value, last)]
    if _YEAR.match(value):
        return ["{0}-01-01".format(value), "{0}-12-31".format(value)]
    raise QueryError("shot takes YYYY, YYYY-MM, YYYY-MM-DD or a..b range: {0}".format(value))


def _condition(key: str, value: str) -> Dict[str, Any]:
    value = value.strip()
    if not value:
        raise QueryError("{0}: needs a value".format(key))
    if key == "tag":
        return {"field": "tag", "op": "contains", "value": value}
    if key == "camera":
        return {"field": "camera", "op": "contains", "value": value}
    if key == "lang":
        return {"field": "lang", "op": "eq", "value": value.lower()}
    if key == "rating":
        rating = _RATING_ALIASES.get(value.lower())
        if rating is None:
            raise QueryError("rating takes good / review / ng / unrated: {0}".format(value))
        return {"field": "rating", "op": "eq", "value": rating}
    if key == "type":
        if value.lower() not in _TYPES:
            raise QueryError("type takes video / audio / image: {0}".format(value))
        return {"field": "media_type", "op": "eq", "value": value.lower()}
    if key == "shot":
        return {"field": "shot", "op": "range", "value": _shot_window(value)}
    raise AssertionError(key)  # pragma: no cover — guarded by FILTER_KEYS


def parse(query: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Split a search-box query into (free_text, conditions).

    Raises QueryError when a known key carries a value it cannot mean (e.g.
    `rating:maybe`) — better a clear error than a filter silently dropped.
    """
    text_parts: List[str] = []
    conditions: List[Dict[str, Any]] = []
    for m in _TOKEN.finditer(query or ""):
        qkey, qval, key, val, phrase, word = m.groups()
        key, val = (qkey, qval) if qkey is not None else (key, val)
        if key is not None and key.lower() in FILTER_KEYS:
            conditions.append(_condition(key.lower(), val))
        elif phrase is not None:
            if phrase.strip():
                text_parts.append(phrase.strip())
        else:
            text_parts.append(m.group(0))
    return " ".join(text_parts), conditions


def matching_ids(conn: Any, conditions: Sequence[Dict[str, Any]]) -> Set[int]:
    """Media ids in `conn`'s library satisfying every condition (AND)."""
    compiled = query_builder.compile_spec({"match": "all", "conditions": list(conditions)})
    rows = conn.execute("SELECT id FROM media WHERE " + compiled["where"],
                        compiled["params"]).fetchall()
    return {r[0] for r in rows}


def describe(text: str, conditions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """What the query was understood as — returned alongside results so a person
    or an agent can see that `camera:a7s3` became a filter, not a search word."""
    return {"text": text, "filters": [
        {"field": c["field"], "value": c["value"]} for c in conditions
    ]}
