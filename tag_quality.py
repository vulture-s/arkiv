"""Tag quality filter — first-pass screening of vision tags before they reach
the user.

qwen3-vl emits both *content* tags (生肉, 餐廳, 切割 — what the footage is) and
*quality-defect* tags (模糊, 低解析度 — how bad the frame looks). The latter are
noise for browsing/search: a filmmaker wants to find content, not be told a clip
is blurry via a tag cloud. This module screens the defect tags out of the
user-facing tag surfaces (sidebar cloud, inspector tags, collection scoring is
unaffected — 廢鏡 collection still uses them deliberately).

Centralized so every surface filters identically. Matching is substring-based on
a small curated noise vocabulary (Chinese, qwen3-vl's output language).
"""
from __future__ import annotations

from typing import Dict, Iterable, List

# Quality-defect vocabulary. A tag is noise if it CONTAINS any of these — covers
# variants like 模糊/模糊畫面 without enumerating each. Keep this to genuine
# image-quality defects, NOT content (e.g. 低光 is a legit lighting style, kept).
_NOISE_SUBSTRINGS = (
    # NOTE: substring match — keep terms SPECIFIC so they don't eat content tags.
    # Bare "畫面" was removed: it would drop content like 店內畫面/街景畫面 (Codex
    # review P2). "模糊畫面" is already covered by "模糊".
    "模糊",       # blurry, 模糊畫面
    "低解析",     # low-res, 低解析度
    "不明物體",   # unidentifiable object (specific, not bare 不明)
    "屏幕",       # screen (artifact of filming a monitor)
    "雜訊",       # noise
    "失焦",       # out of focus
    "過曝",       # overexposed
    "欠曝",       # underexposed
    "晃動",       # shaky
    "黑畫面",     # black frame (specific)
)


def is_noise(tag: str) -> bool:
    """True if the tag describes an image-quality defect rather than content."""
    if not tag:
        return True
    return any(sub in tag for sub in _NOISE_SUBSTRINGS)


# CJK variant-character normalization. The VLM writes the same word with variant
# Traditional chars across frames (吧台 vs 吧檯) — exact dedup keeps both as
# separate tags. Map the variant char → one canonical so they collapse. CONSERVATIVE:
# only TRUE same-word character variants, never semantic merges (生肉/生魚 is the
# LLM-canonicalization pass's job, not this). Extend as real variants surface.
_CHAR_VARIANTS = str.maketrans({
    "檯": "台",   # 吧檯 → 吧台
    "裏": "裡",   # 裏 → 裡
    "着": "著",   # 着 → 著
})


def canonicalize(tag: str) -> str:
    """Trim + normalize variant characters so variant spellings of one word
    collapse on dedup (吧台/吧檯 → 吧台)."""
    return (tag or "").strip().translate(_CHAR_VARIANTS)


def filter_tags(tags: Iterable[str]) -> List[str]:
    """Drop quality-defect tags, preserving order, de-duplicating."""
    seen = set()
    out = []
    for t in tags:
        c = canonicalize(t)
        if not c or is_noise(c) or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def filter_tag_records(records: Iterable[Dict]) -> List[Dict]:
    """Drop noise from a list of {name, count, ...} tag dicts (e.g. /api/tags)."""
    return [r for r in records if r.get("name") and not is_noise(r["name"])]


# Per-media tag count. Industry DAMs (Canto/Adobe) cap auto-tags to avoid noise —
# Canto recommends ~5, Adobe sets a confidence threshold to "avoid too many tags".
# qwen3-vl gives NO per-tag confidence, so we rank by a focus-weighted frequency
# proxy and keep the top N.
DEFAULT_TOP_N = 6


def rank_media_tags(frames: Iterable[Dict], top_n: int = DEFAULT_TOP_N) -> List[str]:
    """Rank a media item's tags from its per-frame data and return the top N.

    Score = sum over frames mentioning the tag of that frame's focus_score
    (1-5; default 3 when missing). A tag seen in several well-focused frames
    outranks one glimpsed in a single soft frame — a confidence proxy since
    qwen3-vl emits no score. Noise tags are dropped first. Ties broken by raw
    frequency then first-seen order (stable).
    """
    weight: Dict[str, float] = {}
    freq: Dict[str, int] = {}
    order: Dict[str, int] = {}
    seq = 0
    for fr in frames or []:
        if not isinstance(fr, dict):
            continue
        try:
            focus = float(fr.get("focus_score") or 3)
        except (TypeError, ValueError):
            focus = 3.0
        for t in fr.get("tags") or []:
            t = canonicalize(str(t))
            if not t or is_noise(t):
                continue
            if t not in order:
                order[t] = seq
                seq += 1
            weight[t] = weight.get(t, 0.0) + focus
            freq[t] = freq.get(t, 0) + 1
    ranked = sorted(weight, key=lambda t: (-weight[t], -freq[t], order[t]))
    return ranked[: top_n if top_n and top_n > 0 else None]
