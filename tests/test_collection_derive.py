"""Deriving topical collections from a project's own vocabulary (feature B).

Every decision lives in a pure function in collection_defs, so these tests call
the real code. The sibling alias pipeline keeps its logic inside
`_run_propose_aliases`, and `tests/test_ingest_llm_shape.py:107` consequently
re-implements its guard loop inline — a test that copies the code it tests still
passes when the real code breaks.

The reference library is single-topic, so it yields at most one candidate. That
proves the gates reject; it cannot prove they accept. The synthetic three-topic
fixture below is the positive control.
"""
from __future__ import annotations

import collection_defs as cd
import smart_collections as sc


def _clip(mid, tags, **over):
    rec = {"id": mid, "duration_s": 10.0, "has_audio": 1,
           "tags": list(tags), "rating": "good",
           "processed_at": "2000-01-01T00:00:00+00:00"}
    rec.update(over)
    return rec


# Three clearly separate subjects, 6 clips each, plus a shot-type descriptor
# sprinkled across all of them to prove stopwords don't bridge the topics.
def _three_topic_library():
    topics = {
        "kitchen": ["炒鍋", "爐火", "food_盤子", "廚房檯面"],
        "ocean": ["海浪", "沙灘", "衝浪板", "礁石"],
        "studio": ["麥克風", "混音台", "監聽喇叭", "隔音牆"],
    }
    recs, mid = [], 1
    for names in topics.values():
        for _ in range(6):
            recs.append(_clip(mid, list(names) + ["特寫", "人物"]))
            mid += 1
    return recs


# ─────────────────────────────── vocabulary ───────────────────────────────

def test_singletons_are_pruned():
    """169 of 244 tags on the reference library occur exactly once. A tag on one
    clip identifies a clip, not a theme."""
    recs = [_clip(1, ["共有", "只出現一次"]), _clip(2, ["共有"]), _clip(3, ["共有"])]
    vocab, _docs, rejected = cd.derivable_vocabulary(recs, min_doc_freq=3)
    assert vocab == ["共有"]
    assert rejected["doc-freq<3"] == 1


def test_stopwords_and_latin_and_markers_are_pruned():
    recs = [_clip(i, ["黑膠唱片", "特寫", "jbl", "* 電池", "5. 汽車"]) for i in range(1, 5)]
    vocab, _docs, rejected = cd.derivable_vocabulary(recs, min_doc_freq=3)
    assert vocab == ["黑膠唱片"]
    assert rejected["stopword"] == 1
    assert rejected["latin"] == 1          # OCR read off objects; is_noise misses it
    assert rejected["list-marker"] == 2


def test_vocabulary_reads_the_same_view_the_scorer_does():
    """Derivation must not source from the lowercased `tags` table: the scorer
    compares against raw frame_tags, so `cd封面` could never match `CD封面`."""
    import json
    recs = [_clip(i, [], frame_tags=json.dumps([{"tags": ["CD封面", "唱針"]}]))
            for i in range(1, 5)]
    vocab, _docs, _r = cd.derivable_vocabulary(recs, min_doc_freq=3)
    assert "CD封面" in vocab, "frame_tags casing must survive into the vocabulary"


# ─────────────────────────────── clustering ───────────────────────────────

def test_clusters_separate_unrelated_topics():
    recs = _three_topic_library()
    vocab, docs, _r = cd.derivable_vocabulary(recs, min_doc_freq=3)
    clusters = cd.cooccurrence_clusters(vocab, docs, threshold=0.25)
    assert len(clusters) == 3, [sorted(c) for c in clusters]
    assert all(len(c) == 4 for c in clusters)


def test_a_descriptor_on_every_clip_does_not_bridge_topics():
    """特寫 co-occurs with everything. Left in, it merges all three subjects into
    one cluster — the mechanism by which 切割 glued cable-making clips into a food
    collection in PR #92."""
    recs = _three_topic_library()
    vocab, docs, _r = cd.derivable_vocabulary(recs, min_doc_freq=3)
    assert "特寫" not in vocab and "人物" not in vocab

    # prove the counterfactual: put it back and the topics collapse into one
    leaky = vocab + ["特寫"]
    docs["特寫"] = {r["id"] for r in recs}
    assert len(cd.cooccurrence_clusters(leaky, docs, threshold=0.25)) == 1


def test_cluster_by_similarity_is_the_shared_union_find():
    sim = {("a", "b"): 0.9, ("c", "d"): 0.9}
    groups = cd.cluster_by_similarity(
        ["a", "b", "c", "d", "e"],
        lambda x, y: sim.get((x, y), sim.get((y, x), 0.0)),
        0.5,
    )
    assert sorted(sorted(g) for g in groups) == [["a", "b"], ["c", "d"]]  # 'e' alone is dropped


# ───────────────────────────────── guard ─────────────────────────────────

def test_guard_drops_invented_tags():
    assert cd.guard_derived_tags(
        ["炒鍋", "爐火", "廚房檯面", "盤子"],
        ["炒鍋", "爐火", "廚房檯面", "盤子", "米其林三星"],
    ) == ["炒鍋", "爐火", "廚房檯面", "盤子"]


def test_guard_returns_empty_rather_than_falling_back_to_raw():
    """Deliberately unlike tag_quality.guard_canonical. Falling back to the raw
    cluster would ship a collection built from an UNJUDGED cluster — the exact
    thing the judging step exists to prevent."""
    assert cd.guard_derived_tags(["a", "b", "c", "d"], ["nope", "also-nope"]) == []
    assert cd.guard_derived_tags(["a", "b", "c", "d"], []) == []
    assert cd.guard_derived_tags(["a", "b", "c", "d"], [{"not": "a string"}]) == []


def test_guard_enforces_the_lower_bound():
    assert cd.guard_derived_tags(["a", "b", "c"], ["a", "b", "c"]) == []


def test_guard_truncates_to_the_upper_bound():
    many = ["t{0}".format(i) for i in range(cd.MAX_DERIVED_TAGS + 6)]
    assert len(cd.guard_derived_tags(many, many)) == cd.MAX_DERIVED_TAGS


# ────────────────────────────── validation ──────────────────────────────

def test_candidate_membership_is_measured_by_the_real_scorer():
    recs = _three_topic_library()
    col = sc.Collection(key="topic_k", title="廚房", category="topic",
                        tags=("炒鍋", "爐火", "food_盤子", "廚房檯面"))
    stats, why = cd.validate_candidate(col, recs, min_members=4, max_share=0.35)
    assert why is None, why
    assert stats["members_at_build"] == 6
    assert stats["library_at_build"] == 18


def test_a_collection_covering_most_of_the_library_is_rejected():
    """On the reference library the vinyl ring covers 72% of tagged clips. A
    collection that is a synonym for the library carries no information."""
    recs = [_clip(i, ["共有A", "共有B", "共有C", "共有D"]) for i in range(1, 11)]
    col = sc.Collection(key="topic_all", title="全部", category="topic",
                        tags=("共有A", "共有B", "共有C", "共有D"))
    stats, why = cd.validate_candidate(col, recs, max_share=0.35)
    assert stats is None and "share" in why


def test_a_two_clip_collection_is_rejected():
    recs = _three_topic_library()
    col = sc.Collection(key="topic_x", title="稀有", category="topic",
                        tags=("炒鍋", "爐火", "food_盤子", "廚房檯面"))
    stats, why = cd.validate_candidate(col, recs, min_members=8)
    assert stats is None and "members" in why


def test_out_of_band_tag_counts_are_rejected():
    recs = _three_topic_library()
    small = sc.Collection(key="topic_s", title="小", category="topic", tags=("炒鍋",))
    stats, why = cd.validate_candidate(small, recs)
    assert stats is None and "outside" in why


def test_a_near_duplicate_of_an_accepted_collection_is_rejected():
    recs = _three_topic_library()
    first = sc.Collection(key="topic_a", title="廚房", category="topic",
                          tags=("炒鍋", "爐火", "food_盤子", "廚房檯面"))
    stats, _ = cd.validate_candidate(first, recs)
    dup = sc.Collection(key="topic_b", title="灶", category="topic",
                        tags=("炒鍋", "爐火", "廚房檯面", "food_盤子"))
    stats2, why = cd.validate_candidate(dup, recs, [set(stats["members"])])
    assert stats2 is None and "duplicates" in why


# ─────────────────────────── keys, titles, diff ───────────────────────────

def test_key_is_order_independent_and_definition_bound():
    assert cd.derived_key(["b", "a", "c"]) == cd.derived_key(["c", "b", "a"])
    assert cd.derived_key(["a", "b"]) != cd.derived_key(["a", "b", "c"])
    assert cd.derived_key(["a", "b"]).startswith("topic_")


def test_substring_titles_are_treated_as_colliding():
    """黑膠唱片 and 黑膠唱片機 share only 24% of members, so a member-overlap gate
    keeps both — and a user cannot tell which to click."""
    assert cd.titles_collide("黑膠唱片", ["黑膠唱片機"])
    assert cd.titles_collide("黑膠唱片機", ["黑膠唱片"])
    assert not cd.titles_collide("海邊", ["錄音室"])
    assert cd.titles_collide("", ["任何"])


def test_rerun_keeps_existing_collections_and_marks_the_gone_ones_stale():
    """A re-run must never silently rename or drop a collection in use."""
    existing = [
        {"key": "topic_keep", "title": "留著", "tags": ["a", "b", "c", "d"]},
        {"key": "topic_gone", "title": "消失", "tags": ["e", "f", "g", "h"]},
        {"key": "custom_hand", "title": "手寫的", "tags": ["x", "y", "z", "w"]},
    ]
    candidates = [
        {"key": "topic_keep", "title": "換個名字", "tags": ["a", "b", "c", "d"]},
        {"key": "topic_new", "title": "新的", "tags": ["n", "o", "p", "q"]},
    ]
    out = {e["key"]: e for e in cd.merge_proposal(existing, candidates)}

    assert out["topic_keep"]["title"] == "留著", "an existing title must not be rewritten"
    assert "stale" not in out["topic_keep"]
    assert out["topic_gone"].get("stale") is True, "gone → marked, never deleted"
    assert "stale" not in out["custom_hand"], "hand-written entries are not derived"
    assert out["topic_new"]["title"] == "新的"


def test_a_stale_collection_recovers_on_a_later_run():
    """Hysteresis: an ingest that dips a collection below the floor must not
    delete a definition the next ingest would restore."""
    existing = [{"key": "topic_a", "title": "甲", "tags": ["a", "b", "c", "d"], "stale": True}]
    out = cd.merge_proposal(existing, [{"key": "topic_a", "title": "甲", "tags": ["a", "b", "c", "d"]}])
    assert "stale" not in out[0]


# ───────────────── regressions from the 2026-08-10 Codex audit ─────────────────

def _with_alias_map(tmp_path, monkeypatch, groups):
    import json
    import config
    import tag_aliases
    amap = tmp_path / "tag_aliases.json"
    amap.write_text(json.dumps({"version": 1, "groups": groups}, ensure_ascii=False),
                    encoding="utf-8")
    monkeypatch.setattr(config, "TAG_ALIASES_PATH", amap)
    tag_aliases._CACHE["mtime"] = None
    return tag_aliases


def test_two_spellings_of_one_concept_are_one_hit_not_two(tmp_path, monkeypatch):
    """BLOCKER. A collection listing both spellings of one concept gave a clip
    carrying only that concept TWO hits against a denominator of four — 0.583, a
    member on a single concept, which is exactly what the 4..14 band exists to
    prevent. Both sides of the ratio now count folded concepts, so a duplicate
    spelling changes nothing."""
    aliases = _with_alias_map(tmp_path, monkeypatch,
                              [{"pref": "黑膠唱片", "alts": ["黑膠唱盤"]}])
    clip = {"duration_s": 3, "has_audio": 1, "tags": ["黑膠唱片"]}
    with_dup = sc.Collection(key="topic_x", title="t", category="topic",
                             tags=("黑膠唱片", "黑膠唱盤", "唱針", "唱片架"))
    without = sc.Collection(key="topic_y", title="t", category="topic",
                            tags=("黑膠唱片", "唱針", "唱片架"))
    assert sc.score_collection(clip, with_dup) == sc.score_collection(clip, without)
    aliases._CACHE["mtime"] = None


def test_booster_tag_conditions_fold_like_everything_else():
    """CONCERN. sig["tags"] is folded, so an unfolded booster condition silently
    never fires (any_tags/all_tags) or always fires (no_tags)."""
    sig = sc.media_signal({"duration_s": 3, "has_audio": 1, "tags": ["吧檯"]})
    assert sig["tags"] == {"吧台"}
    assert sc._booster_applies(sc.Booster(boost=0.1, any_tags=["吧檯"]), sig)
    assert sc._booster_applies(sc.Booster(boost=0.1, all_tags=["吧檯"]), sig)
    assert not sc._booster_applies(sc.Booster(boost=0.1, no_tags=["吧檯"]), sig)


def test_merge_proposal_keeps_the_first_duplicate_like_the_loader():
    """BLOCKER. A dict comprehension kept the LAST duplicate while the loader keeps
    the first, so re-running derivation silently swapped which definition was
    effective — a rename, from the function whose whole job is preventing them."""
    existing = [
        {"key": "topic_dup", "title": "原名", "tags": ["a", "b", "c", "d"]},
        {"key": "topic_dup", "title": "新名", "tags": ["e", "f", "g", "h"]},
    ]
    assert [e["title"] for e in cd.merge_proposal(existing, [])] == ["原名"]


def test_validate_candidate_counts_concepts_not_tag_strings(tmp_path, monkeypatch):
    """The k-band gate must measure what the scorer measures."""
    aliases = _with_alias_map(tmp_path, monkeypatch,
                              [{"pref": "甲", "alts": ["甲2", "甲3"]}])
    col = sc.Collection(key="topic_z", title="t", category="topic",
                        tags=("甲", "甲2", "甲3", "乙", "丙"))  # 5 strings, 3 concepts
    stats, why = cd.validate_candidate(col, _three_topic_library())
    assert stats is None and "k=3" in why
    aliases._CACHE["mtime"] = None
