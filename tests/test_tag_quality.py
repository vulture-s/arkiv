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
