"""arkiv Smart Collections — rule-driven curation (Tier 1).

NOT ML clustering. Predefined collection definitions are scored against each
media item's existing metadata (vision tags + per-frame content_type /
atmosphere / energy + EXIF) using multi-signal weighted matching. A media item
joins every collection it scores >= MIN_CONFIDENCE for (non-exclusive).

Design (per case-study edit-mind §5.3, de-domained to arkiv's real schema):
  score = tag_overlap * tag_weight
        + content_type_match * ctype_weight
        + atmosphere_match  * atmo_weight
        + booster contributions (condition-gated)
  then hard filters (min_duration etc.) gate membership.

Pure Python, no new deps, no embedding for Tier 1 — tag/metadata matching only.
Consumes the shape returned by db helpers (see media_signal()).
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import geo

MIN_CONFIDENCE = 0.40


@dataclass(frozen=True)
class Booster:
    """Condition-gated score bonus. All conditions must hold to apply `boost`."""

    boost: float
    min_duration: Optional[float] = None
    has_audio: Optional[bool] = None
    any_tags: Sequence[str] = field(default_factory=tuple)  # >=1 must be present
    all_tags: Sequence[str] = field(default_factory=tuple)  # all must be present
    no_tags: Sequence[str] = field(default_factory=tuple)  # none may be present
    content_types: Sequence[str] = field(default_factory=tuple)  # any frame matches
    atmospheres: Sequence[str] = field(default_factory=tuple)  # any frame matches
    locations: Sequence[str] = field(default_factory=tuple)  # geo location_label is one of


@dataclass(frozen=True)
class Collection:
    """A predefined smart collection definition."""

    key: str
    title: str
    category: str
    # Tag vocabularies the collection is "about" — overlap with a media item's
    # tags drives the base visual score. Chinese tags match arkiv's qwen3-vl output.
    tags: Sequence[str]
    # Optional hard filters (membership gates, applied AFTER scoring).
    min_duration: Optional[float] = None
    require_audio: Optional[bool] = None
    exclude_tags: Sequence[str] = field(default_factory=tuple)  # any present → reject
    # Condition-gated boosters.
    boosters: Sequence[Booster] = field(default_factory=tuple)
    # Weight of the base tag-overlap signal (rest of score comes from boosters).
    tag_weight: float = 1.0
    # Optional STRUCTURAL membership: a predicate on the raw media row (rating,
    # processed_at, has_audio…) instead of tag overlap. When set, membership is
    # purely predicate(row) and tags are ignored — this is how domain-agnostic
    # collections (待審查 / 最近匯入) work for ANY project, not a hardcoded vocab.
    predicate: Optional[Callable[[Dict[str, Any]], bool]] = None


def media_signal(media: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw media row (dict) into the flat signal the scorer uses.

    Accepts either:
      - {'tags': ['a','b'], 'frames': [{'content_type':..,'atmosphere':..}, ...]}
      - a raw db row with 'frame_tags' JSON string (server /api/media/{id} shape)
    Reads BOTH per-frame metadata AND the media-level aggregate columns
    (content_type / atmosphere / energy on the row itself), plus a derived
    location label from gps_lat/gps_lon.

    Always returns: {tags:set[str], content_types:set, atmospheres:set,
                     energies:set, location:str|None, duration_s:float,
                     has_audio:bool}
    """
    tags: set[str] = set()
    content_types: set[str] = set()
    atmospheres: set[str] = set()
    energies: set[str] = set()

    raw_tags = media.get("tags")
    if isinstance(raw_tags, (list, tuple)):
        for t in raw_tags:
            if isinstance(t, dict):  # [{'name': 'x'}, ...] from /api/media
                if t.get("name"):
                    tags.add(str(t["name"]))
            elif t:
                tags.add(str(t))

    # per-frame metadata: prefer explicit 'frames', fall back to 'frame_tags' JSON
    frames = media.get("frames")
    if frames is None:
        ft = media.get("frame_tags")
        if isinstance(ft, str) and ft:
            try:
                frames = json.loads(ft)
            except (ValueError, TypeError):
                frames = None
        elif isinstance(ft, list):
            frames = ft
    for fr in frames or []:
        if not isinstance(fr, dict):
            continue
        for t in fr.get("tags") or []:
            if t:
                tags.add(str(t))
        if fr.get("content_type"):
            content_types.add(str(fr["content_type"]))
        if fr.get("atmosphere"):
            atmospheres.add(str(fr["atmosphere"]))
        if fr.get("energy"):
            energies.add(str(fr["energy"]))

    # media-level aggregate columns (present on the row itself, not just frames)
    for col, bucket in (("content_type", content_types), ("atmosphere", atmospheres), ("energy", energies)):
        v = media.get(col)
        if v:
            bucket.add(str(v))

    return {
        "tags": tags,
        "content_types": content_types,
        "atmospheres": atmospheres,
        "energies": energies,
        "location": geo.location_label(media.get("gps_lat"), media.get("gps_lon")),
        "duration_s": float(media.get("duration_s") or 0.0),
        "has_audio": bool(media.get("has_audio")),
    }


def _booster_applies(b: Booster, sig: Dict[str, Any]) -> bool:
    tags = sig["tags"]
    if b.min_duration is not None and sig["duration_s"] < b.min_duration:
        return False
    if b.has_audio is not None and sig["has_audio"] != b.has_audio:
        return False
    if b.all_tags and not all(t in tags for t in b.all_tags):
        return False
    if b.any_tags and not any(t in tags for t in b.any_tags):
        return False
    if b.no_tags and any(t in tags for t in b.no_tags):
        return False
    if b.content_types and not (set(b.content_types) & sig["content_types"]):
        return False
    if b.atmospheres and not (set(b.atmospheres) & sig["atmospheres"]):
        return False
    if b.locations and sig.get("location") not in set(b.locations):
        return False
    return True


def score_collection(media: Dict[str, Any], col: Collection) -> float:
    """Score a media item against one collection. 0.0 = no signal / hard-rejected."""
    # Structural collections: membership is the predicate, not tag overlap.
    if col.predicate is not None:
        return 1.0 if col.predicate(media) else 0.0
    sig = media_signal(media)

    # Hard filters (membership gates).
    if col.min_duration is not None and sig["duration_s"] < col.min_duration:
        return 0.0
    if col.require_audio is not None and sig["has_audio"] != col.require_audio:
        return 0.0
    if col.exclude_tags and (set(col.exclude_tags) & sig["tags"]):
        return 0.0

    # Base signal: fraction of the collection's vocabulary the item hits, but
    # rewarded by absolute overlap too (so a 1-of-8 hit isn't as strong as 4-of-8).
    if not col.tags:
        base = 0.0
    else:
        hits = sum(1 for t in col.tags if t in sig["tags"])
        if hits == 0:
            base = 0.0
        else:
            coverage = hits / len(col.tags)  # 0..1
            # saturating reward for raw hits: 1 hit→0.5, 2→0.67, 3→0.75, 4→0.8...
            depth = hits / (hits + 1)
            base = col.tag_weight * (0.5 * coverage + 0.5 * depth)

    if base == 0.0:
        return 0.0  # no topical overlap → not a member regardless of boosters

    score = base
    for b in col.boosters:
        if _booster_applies(b, sig):
            score += b.boost
    return min(1.0, score)


def classify(media: Dict[str, Any], collections: Sequence[Collection]) -> List[Dict[str, Any]]:
    """Return the collections a media item belongs to, sorted by score desc.

    Each result: {key, title, category, score}. Non-exclusive (can be many).
    """
    out = []
    for col in collections:
        s = score_collection(media, col)
        if s >= MIN_CONFIDENCE:
            out.append({"key": col.key, "title": col.title, "category": col.category, "score": round(s, 4)})
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


# ── Tier-1 definitions — Hevin's real shooting archetypes ────────────────────
# Tags are arkiv's qwen3-vl Chinese vocabulary (verified against ingested data).
_RECENT_DAYS = 14


def _is_unrated(m: Dict[str, Any]) -> bool:
    """No editorial rating yet → belongs in the review queue."""
    r = m.get("rating")
    return r is None or (isinstance(r, str) and r.strip() == "")


def _recently_ingested(m: Dict[str, Any]) -> bool:
    """processed_at within the last _RECENT_DAYS — project-agnostic 'new arrivals'."""
    ts = m.get("processed_at")
    if not ts:
        return False
    try:
        t = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - t).days <= _RECENT_DAYS


# Tier-1 collections are now DOMAIN-AGNOSTIC structural buckets that hold for any
# project (the old food-specific 食材特寫/店內空景 were hardcoded for one shoot and
# mis-classified e.g. cable-making clips via the 切割 tag). Topical/auto-derived
# and per-project collections are deferred (B + C).
DEFAULT_COLLECTIONS: List[Collection] = [
    Collection(
        key="recent",
        title="最近匯入",
        category="status",
        tags=(),
        predicate=_recently_ingested,
    ),
    Collection(
        key="needs_review",
        title="待審查",
        category="status",
        tags=(),
        predicate=_is_unrated,
    ),
    Collection(
        key="unusable",
        title="廢鏡 · 待汰",
        category="quality",
        tags=["模糊", "模糊畫面", "低解析度", "低解析", "不明物體", "屏幕", "電視"],
    ),
    # Edit-role collections keyed on MANUAL tags (a-roll / b-roll). They populate
    # only when a clip carries the hand-added tag; a library that never tags this
    # way shows 0 members and the collection is hidden, so they cost nothing.
    # Requires list_collections to feed the `tags` table into the classifier.
    Collection(
        key="a_roll",
        title="A-roll · 主軸",
        category="edit",
        tags=["a-roll"],
    ),
    Collection(
        key="b_roll",
        title="B-roll · 輔助",
        category="edit",
        tags=["b-roll"],
    ),
]
