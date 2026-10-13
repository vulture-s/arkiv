"""commands.py — the one write path for a clip's editorial fields.

Rating, IN/OUT, camera/angle and manual tags used to be written by four route
handlers, three of them as inline `UPDATE media` SQL inside routers/media.py.
Every new caller (another route, the Resolve plugin, a future AI write path) would
have had to copy that SQL and its rules, and the copies drift. These functions are
now the single owner of those writes; routes only translate HTTP in and out.

Contract shared by the field setters (`set_rating`, `set_inout`, `set_camera`):
  * `changes` holds only the fields the caller actually sent. A field that is
    absent is left untouched; a field present with None clears it (PATCH
    semantics, audit M20).
  * The return value is the full post-write value of every field the setter
    owns, whether or not it changed.
  * NotFound if the clip does not exist; Invalid if the merged result breaks a
    rule. Nothing is written in either case.
  * A write that changes something appends one line to
    `.arkiv/audit/edits.jsonl`: {ts, action, media_id, before, after, actor}.
    The before/after pair is what a later undo or "who changed this" needs.
    Best effort, like the delete audit: a full disk must not fail the edit.

Leaf module: imports db/embed only — no FastAPI, no server.
"""
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import config
import db
import embed


class NotFound(Exception):
    """The media id does not exist."""


class Invalid(ValueError):
    """The write would leave the clip in a state its rules forbid."""


def _actor(token: Optional[Dict[str, Any]]) -> str:
    # Same identity rule as media_delete._audit, so both logs name callers alike.
    if isinstance(token, dict):
        return token.get("name") or token.get("id") or "unknown"
    return "unknown"


def _audit(action: str, media_id: int, before: Any, after: Any,
           token: Optional[Dict[str, Any]]) -> None:
    try:
        log_dir = config.PROJECT_ROOT / ".arkiv" / "audit"
        log_dir.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "media_id": media_id,
            "before": before,
            "after": after,
            "actor": _actor(token),
        }
        with (log_dir / "edits.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


# (field name the caller uses, media column it lives in)
_RATING_FIELDS = (("rating", "rating"), ("note", "rating_note"))
_INOUT_FIELDS = (("in_point", "in_point"), ("out_point", "out_point"))
_CAMERA_FIELDS = (("camera_id", "camera_id"), ("angle", "angle"))


def _patch(
    action: str,
    media_id: int,
    changes: Dict[str, Any],
    fields: Sequence[Tuple[str, str]],
    token: Optional[Dict[str, Any]],
    check: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise NotFound(media_id)
    current = {field: rec.get(column) for field, column in fields}
    merged = {field: changes[field] if field in changes else current[field]
              for field, _ in fields}
    if check:
        check(merged)
    sets = [(column, changes[field]) for field, column in fields if field in changes]
    if sets:
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE media SET {0} WHERE id = ?".format(
                    ", ".join("{0} = ?".format(column) for column, _ in sets)),
                (*(value for _, value in sets), media_id),
            )
    if merged != current:
        _audit(action, media_id, current, merged, token)
    return merged


def set_rating(media_id: int, changes: Dict[str, Any],
               token: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Set or clear a clip's rating and rating note. Returns {rating, note}."""
    return _patch("set_rating", media_id, changes, _RATING_FIELDS, token)


def _check_inout(merged: Dict[str, Any]) -> None:
    # An inverted window (in ≥ out) exports an empty/negative range downstream —
    # reject it rather than silently persisting a range that yields nothing.
    in_point, out_point = merged["in_point"], merged["out_point"]
    if in_point is not None and out_point is not None and in_point >= out_point:
        raise Invalid("in_point 必須小於 out_point")


def set_inout(media_id: int, changes: Dict[str, Any],
              token: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Set or clear a clip's IN/OUT trim points (seconds). Returns {in_point, out_point}."""
    return _patch("set_inout", media_id, changes, _INOUT_FIELDS, token, check=_check_inout)


def set_camera(media_id: int, changes: Dict[str, Any],
               token: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Set or clear a clip's multicam camera_id / angle. Returns {camera_id, angle}."""
    return _patch("set_camera", media_id, changes, _CAMERA_FIELDS, token)


def _reindex(media_id: int, after: str) -> None:
    # Tags feed the vector index; a failed reindex leaves the tag written and the
    # index stale until the next rebuild.
    try:
        embed.reindex_media(media_id)
    except Exception as e:
        print("[warn] reindex after {0} failed (non-fatal):".format(after), e)


def _tag_names(tags: List[Dict[str, Any]]) -> List[str]:
    return sorted(t["name"] for t in tags)


def add_tag(media_id: int, name: str, source: str,
            token: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Attach a tag to a clip and reindex it. Returns the clip's tags."""
    if not db.get_record_by_id(media_id):
        raise NotFound(media_id)
    before = _tag_names(db.get_tags(media_id))
    db.add_tag(media_id, name, source)
    _reindex(media_id, "add_tag")
    tags = db.get_tags(media_id)
    if _tag_names(tags) != before:
        _audit("add_tag", media_id, before, _tag_names(tags), token)
    return tags


def remove_tag(media_id: int, name: str,
               token: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Detach a tag from a clip and reindex it. Returns the clip's tags.

    Does not check that the clip exists: removing from an unknown id is a no-op
    that returns [], which is what the route has always answered.
    """
    before = _tag_names(db.get_tags(media_id))
    db.remove_tag(media_id, name)
    _reindex(media_id, "remove_tag")
    tags = db.get_tags(media_id)
    if _tag_names(tags) != before:
        _audit("remove_tag", media_id, before, _tag_names(tags), token)
    return tags
