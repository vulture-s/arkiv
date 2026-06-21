"""Library-level tag alias map (SKOS-lite preferred-term / synonym-ring).

The per-clip canonicalize pass (ingest --canonicalize-tags) merges synonyms
*within one clip*, but across the whole library near-synonyms still coexist in
the global tag cloud (運動 / 跑步 / 賽事 / 路跑 / 運動會 all describe one running
event). The industry answer is a thesaurus equivalence layer, NOT a hierarchy:
one *preferred label* per concept, the rest kept as reversible *aliases*.

This module is the runtime half — it loads a reviewed alias map and folds the
cloud so each concept shows once with summed counts. It is REVERSIBLE and
NON-DESTRUCTIVE: the raw `tags` rows are never touched, so dropping the map file
restores the unfolded cloud, and an alt-label is never lost (search can expand a
pref back to its alts). The proposal half (embed → cluster → LLM judge → human
review) lives in ingest.py (--propose-aliases / --apply-aliases).

File shape (`.arkiv/tag_aliases.json`):
    {"version": 1, "groups": [{"pref": "賽事", "alts": ["運動會", "比賽"]}, ...]}
"""
from __future__ import annotations

import json
from typing import Dict, Iterable, List

import config
import tag_quality

# Reload-on-change cache so a freshly-applied map takes effect without a restart.
_CACHE: Dict[str, object] = {"mtime": None, "alt2pref": {}, "pref2alts": {}}


def _maps():
    """Return (alt2pref, pref2alts), reloading if the file changed. Empty if absent
    or malformed (fail-soft — a bad map must never break the tag cloud)."""
    path = config.TAG_ALIASES_PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _CACHE.update(mtime=None, alt2pref={}, pref2alts={})
        return _CACHE["alt2pref"], _CACHE["pref2alts"]
    if _CACHE["mtime"] == mtime:
        return _CACHE["alt2pref"], _CACHE["pref2alts"]
    alt2pref: Dict[str, str] = {}
    pref2alts: Dict[str, List[str]] = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for g in data.get("groups", []):
            pref = tag_quality.canonicalize(g.get("pref") or "")
            if not pref:
                continue
            alts = []
            for a in g.get("alts", []):
                a = tag_quality.canonicalize(a or "")
                if a and a != pref and a not in alt2pref:
                    alt2pref[a] = pref
                    alts.append(a)
            if alts:
                pref2alts.setdefault(pref, []).extend(alts)
    except (ValueError, OSError):
        alt2pref, pref2alts = {}, {}  # malformed → behave as no map
    _CACHE.update(mtime=mtime, alt2pref=alt2pref, pref2alts=pref2alts)
    return alt2pref, pref2alts


def is_active() -> bool:
    """True if a non-empty alias map is loaded."""
    alt2pref, _ = _maps()
    return bool(alt2pref)


def to_pref(name: str) -> str:
    """Map a tag to its preferred label (identity if not aliased)."""
    alt2pref, _ = _maps()
    return alt2pref.get(tag_quality.canonicalize(name), tag_quality.canonicalize(name))


def expand(name: str) -> List[str]:
    """A tag expands to every spelling of its concept: the pref + all its alts.
    Used so a search/filter on the preferred label still hits alt-tagged media
    (the reversibility guarantee). Returns [name] unchanged when not in the map."""
    alt2pref, pref2alts = _maps()
    c = tag_quality.canonicalize(name)
    pref = alt2pref.get(c, c)
    out = [pref] + list(pref2alts.get(pref, []))
    return list(dict.fromkeys(out)) if len(out) > 1 else [c]


def fold_records(records: Iterable[Dict]) -> List[Dict]:
    """Fold a tag-cloud record list ({name, count}) by the alias map: alt rows
    merge into their preferred label, counts summed, sorted by merged count desc.
    Each folded pref carries `aliases` (the alt spellings it absorbed) so the UI
    can show "賽事 ·3" with a "+運動會/比賽" hint. A no-op when no map is loaded."""
    alt2pref, _ = _maps()
    if not alt2pref:
        return list(records)
    merged: Dict[str, int] = {}
    alts_seen: Dict[str, List[str]] = {}
    order: Dict[str, int] = {}
    seq = 0
    for r in records or []:
        name = tag_quality.canonicalize(r.get("name") or "")
        if not name:
            continue
        pref = alt2pref.get(name, name)
        try:
            cnt = int(r.get("count") or 0)
        except (TypeError, ValueError):
            cnt = 0
        if pref not in order:
            order[pref] = seq
            seq += 1
        merged[pref] = merged.get(pref, 0) + cnt
        if name != pref:
            alts_seen.setdefault(pref, [])
            if name not in alts_seen[pref]:
                alts_seen[pref].append(name)
    ranked = sorted(merged, key=lambda n: (-merged[n], order[n]))
    out = []
    for n in ranked:
        rec = {"name": n, "count": merged[n]}
        if alts_seen.get(n):
            rec["aliases"] = alts_seen[n]
        out.append(rec)
    return out
