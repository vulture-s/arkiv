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

import hashlib
import json
import re
from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

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


# ─────────────────────────── derivation (feature B) ───────────────────────────
#
# Every decision below is a pure function taking data and returning data. The
# `ingest.py` command is only I/O and printing.
#
# That split is deliberate. The sibling alias pipeline keeps its logic inside
# `_run_propose_aliases`, so it cannot be tested — and `tests/test_ingest_llm_shape.py`
# ends up re-implementing its guard loop inline to pin the behaviour. A test that
# copies the code it is testing passes when the real code breaks.

# Shot types, camera moves, generic scene furniture, bare body parts. These
# co-occur with everything, so in a co-occurrence graph they act as bridge edges
# that glue unrelated subjects into one blob — which is exactly the role 切割
# played when PR #92's food collections swallowed a cable-making shoot. Measured:
# dropping these turns a 55%-coverage mega-cluster (室內 + 男子 + 夜間 = "a man,
# indoors") into nothing, and removes the 唱針+特寫 pairing that recreated #92.
DERIVE_STOPWORDS = frozenset({
    "特寫", "近景", "遠景", "中景", "全景", "俯視", "仰視", "鏡頭", "畫面", "背景",
    "操作", "手部操作", "手", "手指",
    "人物", "男子", "男人", "男性", "女子", "女人", "女性", "人",
    "室內", "室外", "戶外", "夜間", "白天", "日間",
})

# A tag longer than this is a phrase or a parse artifact, not a subject.
MAX_DERIVABLE_TAG_LEN = 12

# Leading enumeration debris the model leaks into tag lists ("* 電池", "5. 汽車").
_LIST_MARKER_RE = re.compile(r"^\s*(?:[*\-•·]|\d+\s*[.、)])\s*")

# CJK / kana / hangul. A tag with none of these is Latin text read off an object
# in frame (WORLD, 50 cent, audio-technica off a record sleeve) rather than a
# description of it — and tag_quality.is_noise does not catch those.
_CJK_RE = re.compile(r"[⺀-鿿가-힯぀-ヿ]")


def is_derivable_tag(tag: str) -> Tuple[bool, Optional[str]]:
    """Can this tag anchor a topical collection? Returns (ok, reason_if_not).

    The reason is returned rather than logged so the CLI can print a rejection
    tally — "252 tags in, 36 survived, here is where the rest went" is what makes
    an empty proposal legible instead of looking like a broken run.
    """
    t = (tag or "").strip()
    if not t:
        return False, "empty"
    if _LIST_MARKER_RE.match(t):
        return False, "list-marker"
    if t in DERIVE_STOPWORDS:
        return False, "stopword"
    if len(t) > MAX_DERIVABLE_TAG_LEN:
        return False, "too-long"
    if not _CJK_RE.search(t):
        return False, "latin"
    return True, None


def tag_documents(records: Iterable[Dict[str, Any]]) -> Dict[str, Set[Any]]:
    """tag -> set of media ids carrying it, read through the production signal.

    Sourced from `smart_collections.media_signal`, NOT `db.get_all_tag_names()`.
    The tags table lowercases on write (`db.add_tag`) while the classifier
    compares against raw `frame_tags`, so a vocabulary taken from the table emits
    candidates like `cd封面` that can never match the stored `CD封面`. Deriving
    from the same view the scorer reads is what makes a proposal's member count
    mean anything.
    """
    docs: Dict[str, Set[Any]] = {}
    for rec in records:
        mid = rec.get("id")
        for tag in smart_collections.media_signal(rec)["tags"]:
            docs.setdefault(tag, set()).add(mid)
    return docs


def derivable_vocabulary(
    records: Sequence[Dict[str, Any]], min_doc_freq: int = 3
) -> Tuple[List[str], Dict[str, Set[Any]], Counter]:
    """(vocabulary sorted by doc-frequency desc, tag->docs, rejection tally).

    `min_doc_freq` does the heavy lifting: on the reference library 169 of 244
    tags occur exactly once. A tag on one clip does not describe a theme, it
    identifies a clip.
    """
    docs = tag_documents(records)
    rejected: Counter = Counter()
    vocab: List[str] = []
    for tag, mids in docs.items():
        if len(mids) < min_doc_freq:
            rejected["doc-freq<{0}".format(min_doc_freq)] += 1
            continue
        ok, why = is_derivable_tag(tag)
        if not ok:
            rejected[why] += 1
            continue
        vocab.append(tag)
    vocab.sort(key=lambda t: (-len(docs[t]), t))
    return vocab, docs, rejected


def jaccard(a: Set[Any], b: Set[Any]) -> float:
    """Overlap ratio of two sets; 0.0 when either is empty."""
    if not a or not b:
        return 0.0
    union = len(a | b)
    return (len(a & b) / union) if union else 0.0


def cluster_by_similarity(
    names: Sequence[str],
    simfn: Callable[[str, str], float],
    threshold: float,
) -> List[List[str]]:
    """Union-find single-linkage clustering. Groups of size >= 2, order preserved.

    Generalised from `ingest._cluster_by_cosine`, which had the same body wired to
    one similarity function and no tests. Loose on purpose — the LLM is the real
    gate that splits and rejects; clustering only needs recall.
    """
    n = len(names)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if simfn(names[i], names[j]) >= threshold:
                parent[find(i)] = find(j)

    groups: Dict[int, List[str]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(names[i])
    return [g for g in groups.values() if len(g) >= 2]


def cooccurrence_clusters(
    vocab: Sequence[str], docs: Dict[str, Set[Any]], threshold: float = 0.25
) -> List[List[str]]:
    """Cluster tags that appear on the same clips.

    Co-occurrence, deliberately, not embedding cosine: "shot together" is the
    question a collection answers, while "means the same" is the alias layer's
    job. It also costs no Ollama call, which is why the whole pipeline can be
    exercised end-to-end in CI.
    """
    return cluster_by_similarity(
        vocab, lambda a, b: jaccard(docs.get(a, set()), docs.get(b, set())), threshold
    )


def guard_derived_tags(
    cluster: Sequence[str],
    proposed: Iterable[Any],
    docs: Optional[Dict[str, Set[Any]]] = None,
) -> List[str]:
    """Sanitize an LLM theme proposal against the tags that actually exist.

    Sibling of `tag_quality.guard_canonical` with one deliberate difference. That
    one falls back to the RAW input when a proposal looks wrong, because for a tag
    list the worst case should be "no change". Here, falling back would ship a
    collection built from an UNJUDGED cluster — precisely the thing the LLM step
    exists to prevent — so an out-of-band result returns [] and the theme is
    dropped. For a tag list silence is worse than raw; for a collection silence is
    the safe answer.
    """
    allowed = set(cluster)
    kept: List[str] = []
    for item in proposed or []:
        if not isinstance(item, str):
            continue  # schema adherence is model-dependent; a stray dict is ignored
        name = item.strip()
        if name and name in allowed and name not in kept:
            kept.append(name)
    if docs:
        kept.sort(key=lambda t: (-len(docs.get(t, ())), t))
    kept = kept[:MAX_DERIVED_TAGS]
    return kept if len(kept) >= MIN_DERIVED_TAGS else []


def derived_key(tags: Sequence[str]) -> str:
    """Stable key for a derived collection: a hash of its DEFINITION.

    Not of its name or its rank. Measured on the reference library, dropping 3 of
    62 clips changed the collection set in five of six trials — anything keyed on
    "the most frequent tag" renames or vanishes on the next ingest, and the
    frontend's `{#each liveCollections as c (c.key)}` loses DOM identity with it.
    Same tags in any order -> same key; different tags -> honestly a different
    collection.
    """
    joined = "\x00".join(sorted(tags))
    return "topic_" + hashlib.sha1(joined.encode("utf-8")).hexdigest()[:8]


def candidate_members(
    col: smart_collections.Collection, records: Sequence[Dict[str, Any]]
) -> List[Any]:
    """Media ids this collection would actually claim, per the production scorer.

    Measured, never predicted: the arity arithmetic is subtle enough (k<=3 admits
    on one hit, k>=15 demands three) that a hand-derived estimate would be wrong
    in exactly the cases that matter.
    """
    out = []
    for rec in records:
        if smart_collections.score_collection(rec, col) >= smart_collections.MIN_CONFIDENCE:
            out.append(rec.get("id"))
    return out


def validate_candidate(
    col: smart_collections.Collection,
    records: Sequence[Dict[str, Any]],
    accepted_member_sets: Sequence[Set[Any]] = (),
    min_members: int = 4,
    max_share: float = 0.35,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(stats, None) if the candidate may exist, else (None, reason).

    The ceiling is the load-bearing gate. On the reference library the vinyl
    synonym ring covers 72% of tagged clips and the audio-gear ring 63%: a
    collection holding most of the library is a synonym for the library and
    carries no information. The floor is the other half — a 2-clip collection
    costs a permanent sidebar row and saves nobody any scrolling.
    """
    if not (MIN_DERIVED_TAGS <= len(col.tags) <= MAX_DERIVED_TAGS):
        return None, "k={0} outside [{1},{2}]".format(
            len(col.tags), MIN_DERIVED_TAGS, MAX_DERIVED_TAGS)

    members = set(candidate_members(col, records))
    library = len([r for r in records if r.get("id") is not None])
    share = (len(members) / library) if library else 0.0

    if len(members) < min_members:
        return None, "members={0} < {1}".format(len(members), min_members)
    if share > max_share:
        return None, "share={0:.0%} > {1:.0%}".format(share, max_share)
    for prev in accepted_member_sets:
        if jaccard(members, prev) >= 0.85:
            return None, "duplicates an accepted collection"

    return {
        "members": sorted(members, key=lambda x: (x is None, x)),
        "members_at_build": len(members),
        "library_at_build": library,
        "share": round(share, 4),
    }, None


def titles_collide(title: str, existing: Iterable[str]) -> bool:
    """One title being a substring of another makes both unclickable.

    Member-overlap gates do not catch this: 黑膠唱片 and 黑膠唱片機 share only
    24% of their members yet differ by a single character, and a user cannot form
    a hypothesis about which one to click.
    """
    t = (title or "").strip()
    if not t:
        return True
    for other in existing:
        o = (other or "").strip()
        if not o:
            continue
        if t == o or t in o or o in t:
            return True
    return False


def merge_proposal(
    existing: Sequence[Dict[str, Any]], candidates: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Diff a fresh derivation against what was already accepted.

    A re-run must never silently rename or drop a collection the user has been
    using: existing entries keep their key, tags and frozen title, and are only
    MARKED `stale` when they no longer clear the gates. New candidates append.
    Hysteresis is the point — an ingest that temporarily pushes a collection
    below the floor should not delete a definition that the next ingest restores.
    """
    by_key = {e.get("key"): dict(e) for e in existing if isinstance(e, dict) and e.get("key")}
    fresh_keys = {c["key"] for c in candidates}

    for key, entry in by_key.items():
        if key in fresh_keys:
            entry.pop("stale", None)
        elif str(key).startswith("topic_"):
            entry["stale"] = True  # derived and no longer derivable; kept, hidden

    out = list(by_key.values())
    for cand in candidates:
        if cand["key"] not in by_key:
            out.append(cand)
    return out
