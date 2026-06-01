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
    "模糊",       # blurry, 模糊畫面
    "低解析",     # low-res, 低解析度
    "不明",       # 不明物體 (unidentifiable object)
    "屏幕",       # screen (artifact of filming a monitor)
    "畫面",       # bare "畫面" / 模糊畫面 — describes the frame, not content
    "雜訊",       # noise
    "失焦",       # out of focus
    "過曝",       # overexposed
    "欠曝",       # underexposed
    "晃動",       # shaky
    "黑畫面",     # black frame
)


def is_noise(tag: str) -> bool:
    """True if the tag describes an image-quality defect rather than content."""
    if not tag:
        return True
    return any(sub in tag for sub in _NOISE_SUBSTRINGS)


def filter_tags(tags: Iterable[str]) -> List[str]:
    """Drop quality-defect tags, preserving order, de-duplicating."""
    seen = set()
    out = []
    for t in tags:
        if not t or is_noise(t) or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def filter_tag_records(records: Iterable[Dict]) -> List[Dict]:
    """Drop noise from a list of {name, count, ...} tag dicts (e.g. /api/tags)."""
    return [r for r in records if r.get("name") and not is_noise(r["name"])]
