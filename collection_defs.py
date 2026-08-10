"""Per-project smart-collection definitions loaded from `.arkiv/collections.json`.

PR #92 deleted the shipped topical collections (食材特寫 / 店內空景) because a
hardcoded food vocabulary mis-filed a cable-making shoot through the shared 切割
tag. The lesson was not "topical collections are bad" — it was that a *shipped
default* carries one shoot's vocabulary into every other project. A per-project
file structurally cannot do that, which is why this exists.

The file is the UI. Same contract as `tag_aliases.json` and `corrections.json`:
plain JSON in the project data dir, readable, diffable, hand-editable, and
reversible by deleting it. `ingest --propose-collections` writes a proposal
alongside it; nothing here requires that generator to have run.

MODULE NAME: this must never be called `collections.py`. The repo puts its root
on a flat sys.path, so that name would shadow the stdlib `collections` module and
break imports repo-wide.

Fail-soft throughout, mirroring `tag_aliases._maps()`: a missing, unparseable or
partly-invalid file degrades to the shipped defaults and never raises. A bad
config file must not be able to take down /api/collections.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

import config
import smart_collections

# Format version. A file declaring anything else is treated as absent rather than
# half-read, so a future format can't be misinterpreted by an older build.
FORMAT_VERSION = 1

# Derived collections live inside this band, and the reason is arithmetic, not
# taste. Membership needs base = 0.5*(hits/k) + 0.5*(hits/(hits+1)) >= 0.40:
#   k <= 3   -> ONE shared tag is enough      (this is the PR #92 accident)
#   k 4..14  -> TWO are required              (the safe band)
#   k >= 15  -> THREE, and k=15/h=2 lands on 0.39999999999999997 in IEEE754,
#               which is why the ceiling is 14 and not 15.
# A generator that picks k freely is silently picking each collection's strictness.
MIN_DERIVED_TAGS = 4
MAX_DERIVED_TAGS = 14

# A runaway file must not make /api/collections classify the library against
# thousands of definitions on every request.
MAX_FILE_COLLECTIONS = 50

# Hand-editable, so the loader validates independently of whatever wrote the file.
KEY_RE = re.compile(r"^(topic|custom)_[A-Za-z0-9_]{1,40}$")
MAX_TITLE_LEN = 32
MAX_TAGS_PER_COLLECTION = 32

BUILTIN_KEYS = frozenset(c.key for c in smart_collections.DEFAULT_COLLECTIONS)

# Reload-on-change cache so an applied file takes effect without a restart.
_CACHE: Dict[str, Any] = {"mtime": None, "entries": []}


def _clean_str_list(value: Any, cap: int) -> List[str]:
    """Non-empty trimmed strings only, deduped, order preserved, capped."""
    if not isinstance(value, (list, tuple)):
        return []
    out: List[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item and item not in out:
            out.append(item)
        if len(out) >= cap:
            break
    return out


def _entry_to_collection(entry: Any) -> Optional[smart_collections.Collection]:
    """One validated file entry -> a Collection, or None if it doesn't qualify.

    Returning None for a single bad entry (rather than raising) is deliberate: one
    typo in a hand-edited file must cost that entry, not the whole file.
    """
    if not isinstance(entry, dict):
        return None

    key = entry.get("key")
    if not isinstance(key, str) or not KEY_RE.match(key):
        return None
    # Built-ins are shipped code with predicates JSON cannot express. Letting a
    # file entry reuse a built-in key would silently replace `recent` with a
    # predicate-less, tag-less collection that can never match anything. Built-ins
    # are hideable via "disable", never overridable.
    if key in BUILTIN_KEYS:
        return None

    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    title = title.strip()[:MAX_TITLE_LEN]

    tags = _clean_str_list(entry.get("tags"), MAX_TAGS_PER_COLLECTION)
    if not tags:
        return None

    category = entry.get("category")
    if not isinstance(category, str) or not category.strip():
        category = "topic"

    exclude_tags = _clean_str_list(entry.get("exclude_tags"), MAX_TAGS_PER_COLLECTION)

    min_duration = entry.get("min_duration")
    if not isinstance(min_duration, (int, float)) or isinstance(min_duration, bool):
        min_duration = None

    require_audio = entry.get("require_audio")
    if not isinstance(require_audio, bool):
        require_audio = None

    return smart_collections.Collection(
        key=key,
        title=title,
        category=category.strip(),
        tags=tuple(tags),
        min_duration=min_duration,
        require_audio=require_audio,
        exclude_tags=tuple(exclude_tags),
        # Always None, and this is a security property rather than a limitation:
        # `predicate` is a callable, so a config file can never inject code into
        # the classifier. File collections are tag-matched, full stop.
        predicate=None,
    )


def _read_file() -> Dict[str, Any]:
    """Parse the collections file, reloading only when its mtime changed."""
    path = config.COLLECTIONS_PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _CACHE.update(mtime=None, entries=[], disable=frozenset())
        return _CACHE
    if _CACHE["mtime"] == mtime:
        return _CACHE

    entries: List[smart_collections.Collection] = []
    disable: frozenset = frozenset()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == FORMAT_VERSION:
            seen = set()
            for raw in data.get("collections") or []:
                col = _entry_to_collection(raw)
                if col is None or col.key in seen:
                    continue
                seen.add(col.key)
                entries.append(col)
                if len(entries) >= MAX_FILE_COLLECTIONS:
                    break
            # Only built-in keys are disableable — a file cannot disable another
            # file entry, which would just be a confusing way to delete a line.
            disable = frozenset(
                k for k in _clean_str_list(data.get("disable"), 64) if k in BUILTIN_KEYS
            )
    except (ValueError, OSError, TypeError):
        entries, disable = [], frozenset()  # malformed → behave as no file

    _CACHE.update(mtime=mtime, entries=entries, disable=disable)
    return _CACHE


def file_collections() -> List[smart_collections.Collection]:
    """Just the project's own definitions (no built-ins)."""
    return list(_read_file()["entries"])


def disabled_builtins() -> frozenset:
    """Built-in keys the project has switched off."""
    return _read_file().get("disable") or frozenset()


def load_collections() -> List[smart_collections.Collection]:
    """The effective definition set: shipped defaults (minus disabled) + the
    project's own. File entries append; they never override a built-in."""
    off = disabled_builtins()
    return [c for c in smart_collections.DEFAULT_COLLECTIONS if c.key not in off] + \
        file_collections()


def classification_records() -> List[Dict[str, Any]]:
    """Every media row in the shape `smart_collections.classify` consumes.

    Shared by /api/collections and by the derivation CLI on purpose: a proposal
    generated from a different view of the vocabulary than the classifier uses is
    a proposal whose member counts are fiction. Keeping one function means that
    class of drift cannot reappear.

    audit L13: reads only the columns classify needs (frame_tags + media-level
    aggregates + gps + duration/audio) — get_all_records() was SELECT *, hauling
    words_json/segments_json/transcript for the entire library.
    """
    import db  # local: keeps this module importable without the DB stack

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, thumbnail_path, duration_s, has_audio, "
            "frame_tags, content_type, atmosphere, energy, gps_lat, gps_lon, "
            "rating, processed_at "
            "FROM media ORDER BY id"
        ).fetchall()
        # Manual tags live in the `tags` table, not frame_tags. Pull them (one
        # bulk query) so tag-keyed collections match USER tags, not only vision
        # output. Filter to source='manual': the tags table ALSO holds
        # source='auto' vision copies (ingest.py), and an auto tag that happened
        # to be named 'a-roll' must not silently join an editorial collection
        # without the user's hand (Codex audit). media_signal merges
        # media["tags"] into the scored signal.
        tag_map: Dict[int, List[str]] = {}
        for tid, tname in conn.execute(
            "SELECT media_id, name FROM tags WHERE source = 'manual'"
        ):
            tag_map.setdefault(tid, []).append(tname)

    out = []
    for row in rows:
        rec = dict(row)
        rec["tags"] = tag_map.get(rec["id"], [])
        out.append(rec)
    return out
