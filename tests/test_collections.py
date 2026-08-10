"""Unit tests for smart collections engine (smart_collections.py).

Tier-1 collections are now DOMAIN-AGNOSTIC structural buckets (待審查 / 最近匯入 /
廢鏡) so any project classifies correctly — the old hardcoded food vocab
(食材特寫/店內空景) mis-filed non-food projects (cable-making's 切割 tag). The
qwen3-vl tag fixtures (C37xx) still exercise the tag engine + media_signal.
"""
from __future__ import annotations

import json

import config
import smart_collections as sc
import tag_aliases


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


# ── structural collection membership (domain-agnostic Tier-1) ────────────────
# The Tier-1 collections are now project-agnostic structural buckets (待審查 /
# 最近匯入 / 廢鏡), NOT the old hardcoded food vocab — so a non-food project
# (e.g. cable-making) is never mis-filed into 食材特寫.
def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def test_unrated_clip_joins_needs_review():
    assert "needs_review" in _keys({"duration_s": 5, "has_audio": 1, "tags": [], "rating": None})


def test_rated_clip_not_in_needs_review():
    assert "needs_review" not in _keys(
        {"duration_s": 5, "has_audio": 1, "tags": [], "rating": "good"})


def test_recent_clip_joins_recent():
    assert "recent" in _keys(
        {"duration_s": 5, "tags": [], "rating": "good", "processed_at": _now_iso()})


def test_old_clip_not_in_recent():
    assert "recent" not in _keys(
        {"duration_s": 5, "tags": [], "rating": "good", "processed_at": "2000-01-01T00:00:00+00:00"})


def test_unusable_clip_joins_unusable_collection():
    assert "unusable" in _keys(C3811)


def test_clip_can_belong_to_multiple_structural_collections():
    # non-exclusive membership: unrated AND recent → both buckets
    keys = _keys({"duration_s": 5, "tags": [], "rating": None, "processed_at": _now_iso()})
    assert {"needs_review", "recent"} <= keys


# ── scoring mechanics (tag engine, exercised via 廢鏡 + ad-hoc collections) ────
def test_no_defect_tags_not_unusable():
    # content tags but no quality-defect tags, rated + old → joins NOTHING
    blank = {"duration_s": 5, "has_audio": 1, "tags": ["航空", "雲海", "日出"],
             "rating": "good", "processed_at": "2000-01-01T00:00:00+00:00"}
    assert sc.classify(blank, COLS) == []


def test_score_below_threshold_excluded():
    # a single weak tag hit stays under MIN_CONFIDENCE without boosters (ad-hoc col)
    col = sc.Collection(key="t", title="t", category="c",
                        tags=["室內", "吧檯", "座椅", "燈光", "低光", "餐廳"])
    weak = {"duration_s": 5, "has_audio": 1, "tags": ["室內"], "frames": []}
    assert sc.score_collection(weak, col) < sc.MIN_CONFIDENCE


def test_booster_raises_score():
    # boosters lift a tag-based score (ad-hoc col, project-neutral)
    col = sc.Collection(key="t", title="t", category="c", tags=["吧檯", "室內", "座椅"],
                        boosters=(sc.Booster(boost=0.2, content_types=["Establishing"]),))
    base = {"duration_s": 6, "has_audio": 1, "tags": ["吧檯", "室內", "座椅"], "frames": []}
    boosted = dict(base, frames=[{"content_type": "Establishing"}])
    assert sc.score_collection(boosted, col) > sc.score_collection(base, col)


def test_classify_sorted_desc():
    rows = sc.classify(C3811, COLS)
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


# ── media_signal normalization ───────────────────────────────────────────────
def test_signal_parses_frame_tags_json_string():
    import json
    raw = {"duration_s": 3, "has_audio": 1,
           "frame_tags": json.dumps([{"tags": ["生肉", "切割"], "content_type": "Detail"}])}
    sig = sc.media_signal(raw)
    assert "生肉" in sig["tags"] and "Detail" in sig["content_types"]


def test_signal_parses_api_tag_dicts():
    """The /api/media shape ([{'name': ...}]) parses, and tags land canonicalised.

    `吧檯` arrives as `吧台`: media_signal now folds every tag through
    tag_quality.canonicalize + tag_aliases.to_pref, because scoring is an exact
    string comparison and the variant spellings would otherwise never match.
    The collection's own tags fold identically, so this is not a new mismatch —
    see test_variant_spelling_matches_a_collection below.
    """
    raw = {"duration_s": 3, "has_audio": 1, "tags": [{"name": "吧檯"}, {"name": "室內"}]}
    sig = sc.media_signal(raw)
    assert sig["tags"] == {"吧台", "室內"}


def test_variant_spelling_matches_a_collection():
    """The point of folding: 檯/台 are the same word, so a clip tagged one way
    must join a collection written the other way. Before folding this scored 0.0
    — exact string comparison against an un-normalised tag set."""
    clip = {"duration_s": 3, "has_audio": 1, "tags": ["吧檯"]}
    col = sc.Collection(key="t", title="t", category="c", tags=["吧台"])
    assert sc.score_collection(clip, col) >= sc.MIN_CONFIDENCE

    # ...and symmetrically, with the spellings swapped.
    clip2 = {"duration_s": 3, "has_audio": 1, "tags": ["吧台"]}
    col2 = sc.Collection(key="t", title="t", category="c", tags=["吧檯"])
    assert sc.score_collection(clip2, col2) >= sc.MIN_CONFIDENCE


def test_folding_is_a_noop_without_an_alias_map():
    """A project that never ran `ingest --apply-aliases` must behave exactly as
    before. tag_aliases.to_pref returns its input unchanged when no map is
    active, so only character canonicalisation applies."""
    assert not tag_aliases.is_active()
    assert sc.fold_tag("黑膠唱盤") == "黑膠唱盤"
    clip = {"duration_s": 3, "has_audio": 1, "tags": ["黑膠唱盤"]}
    col = sc.Collection(key="t", title="t", category="c", tags=["黑膠唱片"])
    assert sc.score_collection(clip, col) == 0.0  # unrelated strings stay unrelated


def test_alias_map_makes_a_synonym_a_member(tmp_path, monkeypatch):
    """The regression this whole change exists for: with a reviewed alias map,
    a clip tagged 黑膠唱盤 joins a collection listing 黑膠唱片. Before this change
    `smart_collections` imported neither tag_aliases nor tag_quality, so
    `ingest --apply-aliases` folded the tag cloud and left membership untouched.
    """
    amap = tmp_path / "tag_aliases.json"
    amap.write_text(json.dumps({
        "version": 1,
        "groups": [{"pref": "黑膠唱片", "alts": ["黑膠唱盤", "黑膠唱機", "唱盤"]}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "TAG_ALIASES_PATH", amap)
    tag_aliases._CACHE["mtime"] = None  # bust the mtime cache

    col = sc.Collection(key="t", title="t", category="c", tags=["黑膠唱片"])
    for spelling in ("黑膠唱盤", "黑膠唱機", "唱盤", "黑膠唱片"):
        clip = {"duration_s": 3, "has_audio": 1, "tags": [spelling]}
        assert sc.score_collection(clip, col) >= sc.MIN_CONFIDENCE, spelling

    tag_aliases._CACHE["mtime"] = None  # don't leak the map into later tests


def test_two_spellings_of_one_concept_are_not_two_hits(tmp_path, monkeypatch):
    """Folding on the CLIP side (not by expanding the collection's tag list) is
    what makes "2 hits" mean two different concepts. A clip carrying three
    spellings of the same object must not clear a 2-hit gate on its own."""
    amap = tmp_path / "tag_aliases.json"
    amap.write_text(json.dumps({
        "version": 1,
        "groups": [{"pref": "黑膠唱片", "alts": ["黑膠唱盤", "黑膠唱機"]}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "TAG_ALIASES_PATH", amap)
    tag_aliases._CACHE["mtime"] = None

    # 4 tags → needs 2 hits. All three clip tags fold to ONE concept.
    col = sc.Collection(key="t", title="t", category="c",
                        tags=["黑膠唱片", "唱針", "轉盤", "唱片架"])
    clip = {"duration_s": 3, "has_audio": 1,
            "tags": ["黑膠唱片", "黑膠唱盤", "黑膠唱機"]}
    assert sc.score_collection(clip, col) < sc.MIN_CONFIDENCE

    # add a genuinely different concept → now it is a member
    clip2 = dict(clip, tags=clip["tags"] + ["唱針"])
    assert sc.score_collection(clip2, col) >= sc.MIN_CONFIDENCE

    tag_aliases._CACHE["mtime"] = None


def test_min_duration_hard_filter():
    short_meat = dict(C3756, duration_s=0.5)
    col = sc.Collection(key="t", title="t", category="c", tags=["三文魚"], min_duration=2.0)
    assert sc.score_collection(short_meat, col) == 0.0


def test_exclude_tags_rejects():
    col = sc.Collection(key="t", title="t", category="c", tags=["生肉"], exclude_tags=["模糊"])
    blurry_meat = {"duration_s": 5, "has_audio": 1, "tags": ["生肉", "模糊"], "frames": []}
    assert sc.score_collection(blurry_meat, col) == 0.0


# ── media-level aggregate columns (not just per-frame) ───────────────────────
def test_signal_reads_media_level_aggregate_columns():
    # A row with no frames but top-level content_type/atmosphere/energy columns
    # (the shape db.get_all_records() returns) must still surface those signals.
    raw = {"duration_s": 5, "has_audio": 1, "tags": ["吧檯"],
           "content_type": "Establishing", "atmosphere": "昏暗", "energy": "中"}
    sig = sc.media_signal(raw)
    assert "Establishing" in sig["content_types"]
    assert "昏暗" in sig["atmospheres"]
    assert "中" in sig["energies"]


def test_aggregate_content_type_booster_applies():
    col = sc.Collection(
        key="t", title="t", category="c", tags=["吧檯"],
        boosters=(sc.Booster(boost=0.2, content_types=["Establishing"]),),
    )
    base = {"duration_s": 5, "has_audio": 1, "tags": ["吧檯"]}
    boosted = dict(base, content_type="Establishing")
    assert sc.score_collection(boosted, col) > sc.score_collection(base, col)


# ── location signal (geo integration) ────────────────────────────────────────
def test_signal_derives_location_from_gps():
    raw = {"duration_s": 5, "has_audio": 1, "tags": ["吧檯"],
           "gps_lat": 24.1477, "gps_lon": 120.6736}
    assert sc.media_signal(raw)["location"] == "24.15N,120.67E"


def test_signal_location_none_without_gps():
    assert sc.media_signal({"duration_s": 5, "tags": ["吧檯"]})["location"] is None


def test_signal_null_island_gps_is_no_location():
    raw = {"duration_s": 5, "tags": ["吧檯"], "gps_lat": 0.0, "gps_lon": 0.0}
    assert sc.media_signal(raw)["location"] is None


# ── edit-role collections keyed on MANUAL tags (a-roll / b-roll) ─────────────
# These populate from the `tags` table (hand-added), which list_collections now
# feeds into the classifier. media_signal already merges media["tags"], so at the
# engine level a clip carrying the manual tag is enough to join the collection.
def test_manual_a_roll_tag_joins_a_roll_collection():
    clip = {"duration_s": 60, "has_audio": 1, "tags": ["a-roll"], "frames": []}
    assert "a_roll" in _keys(clip)


def test_manual_b_roll_tag_joins_b_roll_collection():
    clip = {"duration_s": 6, "has_audio": 0, "tags": ["b-roll"], "frames": []}
    assert "b_roll" in _keys(clip)


def test_untagged_clip_in_neither_edit_role_collection():
    clip = {"duration_s": 6, "has_audio": 1, "tags": ["拉麵", "碗"], "frames": []}
    keys = _keys(clip)
    assert "a_roll" not in keys and "b_roll" not in keys


def test_edit_role_collections_do_not_cross_leak():
    a_only = {"duration_s": 60, "has_audio": 1, "tags": ["a-roll"], "frames": []}
    b_only = {"duration_s": 6, "has_audio": 1, "tags": ["b-roll"], "frames": []}
    assert "b_roll" not in _keys(a_only)
    assert "a_roll" not in _keys(b_only)


def test_location_booster_gates_on_label():
    col = sc.Collection(
        key="t", title="t", category="c", tags=["吧檯"],
        boosters=(sc.Booster(boost=0.3, locations=["24.15N,120.67E"]),),
    )
    here = {"duration_s": 5, "has_audio": 1, "tags": ["吧檯"], "gps_lat": 24.1477, "gps_lon": 120.6736}
    elsewhere = {"duration_s": 5, "has_audio": 1, "tags": ["吧檯"], "gps_lat": 25.0330, "gps_lon": 121.5654}
    assert sc.score_collection(here, col) > sc.score_collection(elsewhere, col)
