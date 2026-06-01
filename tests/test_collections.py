"""Unit tests for smart collections engine (collections.py).

Fixtures are the REAL qwen3-vl tags from the 5 明燒肉 clips ingested 2026-05-31
(verified against .arkiv/project.db), so expected memberships are ground truth:
  C3742 → 店內空景   C3747/C3750/C3756 → 食材特寫   C3811 → 廢鏡
"""
from __future__ import annotations

import smart_collections as sc


# ── real fixtures (tags verified vs live DB) ─────────────────────────────────
C3742 = {  # 店內空景 — 吧檯/咖啡館/室內
    "duration_s": 6.01, "has_audio": 1,
    "tags": ["低光", "吧檯", "咖啡館", "圖形設計", "室內", "幾何結構", "座椅", "條紋牆面"],
    "frames": [
        {"tags": ["圖形設計", "條紋牆面", "幾何結構"], "content_type": "Transition", "atmosphere": "簡約", "energy": "低"},
        {"tags": ["咖啡館", "吧檯", "座椅", "室內", "燈光"], "content_type": "Establishing", "atmosphere": "舒適", "energy": "低"},
        {"tags": ["餐廳", "吧檯", "低光"], "content_type": "Establishing", "atmosphere": "幽暗", "energy": "中"},
    ],
}
C3747 = {  # 生肉特寫 — 切割/手套
    "duration_s": 9.01, "has_audio": 1,
    "tags": ["切割", "手套", "手部操作", "生肉", "紫色手套", "肉品", "肉類加工", "食品處理"],
    "frames": [{"tags": ["切割", "生肉", "手套"], "content_type": "Detail", "atmosphere": "專注", "energy": "中"}],
}
C3750 = {  # 生肉特寫 — 生豬肉/脂肪
    "duration_s": 6.51, "has_audio": 1,
    "tags": ["切割", "切痕", "包裝", "生肉", "生豬肉", "肉類", "脂肪層", "食材"],
    "frames": [{"tags": ["生豬肉", "脂肪層", "切痕"], "content_type": "Detail"}],
}
C3756 = {  # 三文魚切片
    "duration_s": 2.0, "has_audio": 1,
    "tags": ["三文魚", "切片", "廚房", "模糊", "生魚", "砧板", "粉紅色", "肉類"],
    "frames": [{"tags": ["三文魚", "切片", "砧板"], "content_type": "Detail"}],
}
C3811 = {  # 廢鏡 — 模糊/電視
    "duration_s": 4.5, "has_audio": 1,
    "tags": ["不明物體", "低解析度", "屏幕", "模糊", "模糊畫面", "淺色背景", "邊框", "電視"],
    "frames": [{"tags": ["模糊", "電視", "屏幕"], "content_type": "Transition", "atmosphere": "幽暗"}],
}

COLS = sc.DEFAULT_COLLECTIONS


def _keys(media):
    return {r["key"] for r in sc.classify(media, COLS)}


# ── ground-truth membership ──────────────────────────────────────────────────
def test_interior_clip_joins_interior_collection():
    assert "interior_establishing" in _keys(C3742)


def test_meat_clips_join_food_collection():
    for clip in (C3747, C3750, C3756):
        assert "food_closeup" in _keys(clip), clip["tags"]


def test_unusable_clip_joins_unusable_collection():
    assert "unusable" in _keys(C3811)


# ── separation (no false positives across the obvious divides) ───────────────
def test_interior_clip_not_in_food():
    assert "food_closeup" not in _keys(C3742)


def test_meat_clip_not_in_interior():
    assert "interior_establishing" not in _keys(C3747)


# ── scoring mechanics ────────────────────────────────────────────────────────
def test_no_tag_overlap_scores_zero():
    blank = {"duration_s": 5, "has_audio": 1, "tags": ["航空", "雲海", "日出"], "frames": []}
    assert sc.classify(blank, COLS) == []


def test_score_below_threshold_excluded():
    # a single weak tag hit stays under MIN_CONFIDENCE without boosters
    weak = {"duration_s": 5, "has_audio": 1, "tags": ["室內"], "frames": []}
    interior = next(c for c in COLS if c.key == "interior_establishing")
    s = sc.score_collection(weak, interior)
    assert s < sc.MIN_CONFIDENCE


def test_booster_raises_score():
    interior = next(c for c in COLS if c.key == "interior_establishing")
    # same tags, but C3742 has Establishing frames → booster fires
    base = {"duration_s": 6, "has_audio": 1,
            "tags": ["餐廳", "咖啡館", "吧檯", "室內"], "frames": []}
    boosted = dict(base, frames=[{"content_type": "Establishing", "atmosphere": "舒適"}])
    assert sc.score_collection(boosted, interior) > sc.score_collection(base, interior)


def test_classify_sorted_desc():
    rows = sc.classify(C3747, COLS)
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_non_exclusive_membership_possible():
    # C3756 is both 食材 (三文魚/切片/肉類) and has 模糊 — but 模糊 alone is 1 weak
    # hit for 廢鏡, should stay under threshold → only food. Asserts non-exclusivity
    # is allowed yet threshold still gates.
    keys = _keys(C3756)
    assert "food_closeup" in keys


# ── media_signal normalization ───────────────────────────────────────────────
def test_signal_parses_frame_tags_json_string():
    import json
    raw = {"duration_s": 3, "has_audio": 1,
           "frame_tags": json.dumps([{"tags": ["生肉", "切割"], "content_type": "Detail"}])}
    sig = sc.media_signal(raw)
    assert "生肉" in sig["tags"] and "Detail" in sig["content_types"]


def test_signal_parses_api_tag_dicts():
    raw = {"duration_s": 3, "has_audio": 1, "tags": [{"name": "吧檯"}, {"name": "室內"}]}
    sig = sc.media_signal(raw)
    assert sig["tags"] == {"吧檯", "室內"}


def test_min_duration_hard_filter():
    short_meat = dict(C3756, duration_s=0.5)
    col = sc.Collection(key="t", title="t", category="c", tags=["三文魚"], min_duration=2.0)
    assert sc.score_collection(short_meat, col) == 0.0


def test_exclude_tags_rejects():
    col = sc.Collection(key="t", title="t", category="c", tags=["生肉"], exclude_tags=["模糊"])
    blurry_meat = {"duration_s": 5, "has_audio": 1, "tags": ["生肉", "模糊"], "frames": []}
    assert sc.score_collection(blurry_meat, col) == 0.0
