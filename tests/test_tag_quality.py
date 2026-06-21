"""tag_quality: noise filtering, exact dedup, and CJK variant-character collapse.

The variant-collapse (吧台/吧檯 → one tag) closes the exact-dedup gap where the
VLM writes the same word with variant Traditional characters across frames.
Semantic near-synonyms (生肉/生魚) are deliberately NOT merged here — that's the
LLM-canonicalization pass, kept separate so this layer stays deterministic.
"""
import tag_quality as tq


def test_is_noise_screens_quality_defects_not_content():
    assert tq.is_noise("模糊")
    assert tq.is_noise("低解析度")
    assert not tq.is_noise("吧台")
    assert not tq.is_noise("低光")  # legit lighting style, kept


def test_canonicalize_collapses_variant_chars():
    assert tq.canonicalize("吧檯") == "吧台"
    assert tq.canonicalize(" 吧台 ") == "吧台"  # also trims
    assert tq.canonicalize("餐廳") == "餐廳"  # unaffected


def test_filter_tags_dedupes_including_variants():
    out = tq.filter_tags(["吧台", "吧檯", "餐廳", "餐廳", "模糊"])
    assert out == ["吧台", "餐廳"]  # 吧檯→吧台 merged, exact dup dropped, noise dropped


def test_rank_media_tags_merges_variant_across_frames():
    frames = [
        {"tags": ["吧台", "室內"], "focus_score": 4},
        {"tags": ["吧檯", "室內"], "focus_score": 4},  # variant of 吧台
    ]
    ranked = tq.rank_media_tags(frames)
    # 吧台 and 吧檯 collapse to a single tag, not two
    assert ranked.count("吧台") == 1
    assert "吧檯" not in ranked
    assert "室內" in ranked


def test_rank_media_tags_does_not_merge_distinct_concepts():
    # Guard against over-merging: distinct tags must survive.
    frames = [{"tags": ["室內", "餐廳", "刀"], "focus_score": 3}]
    ranked = tq.rank_media_tags(frames)
    assert set(["室內", "餐廳", "刀"]).issubset(set(ranked))


# ── guard_canonical: sanitize an LLM merge proposal ─────────────────────────
def test_guard_strips_invented_words():
    raw = ["生肉", "魚肉", "肉類", "生魚"]
    # LLM invented 生食 (not in raw) + picked 肉類 (in raw)
    assert tq.guard_canonical(raw, ["肉類", "生食"]) == ["肉類"]


def test_guard_rejects_over_merge_falls_back_to_raw():
    raw = ["手套", "生肉", "刀", "切割", "食品", "處理"]
    # collapsing 6 distinct down to 1 is over-aggressive → keep raw
    out = tq.guard_canonical(raw, ["食品"])
    assert out == list(dict.fromkeys(raw))


def test_guard_accepts_reasonable_merge():
    raw = ["生肉", "魚肉", "肉類", "脂肪層", "生魚", "切片"]
    out = tq.guard_canonical(raw, ["肉類", "脂肪層", "切片"])
    assert out == ["肉類", "脂肪層", "切片"]  # all ⊂ raw, not over-merged


# ── merge_tag_records: global tag-cloud aggregation ─────────────────────────
def test_merge_tag_records_collapses_variant_and_sums_counts():
    recs = [
        {"name": "人群", "count": 15},
        {"name": "人羣", "count": 9},   # 羣→群 variant of the above
        {"name": "跑步", "count": 17},
        {"name": "模糊", "count": 3},   # noise, dropped
    ]
    out = tq.merge_tag_records(recs)
    names = {r["name"]: r["count"] for r in out}
    assert names == {"跑步": 17, "人群": 24}  # variants merged, noise gone
    assert out[0]["name"] == "人群"  # sorted by merged count desc (24 > 17)


def test_merge_tag_records_handles_missing_count():
    out = tq.merge_tag_records([{"name": "餐廳"}, {"name": "餐廳", "count": 2}])
    assert out == [{"name": "餐廳", "count": 2}]
