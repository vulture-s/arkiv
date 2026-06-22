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

import re
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


# English scene-descriptor words the VLM leaks into the (otherwise Chinese) tag
# list on every clip (day/interior/…). They're not content tags. Exact-match,
# lowercased — does NOT touch useful ASCII tags like LED / USB / 4K / RJ45, which
# the electronics/cable domain legitimately produces.
_ENGLISH_SCENE_NOISE = frozenset({
    "day", "night", "interior", "exterior", "indoor", "outdoor",
    "daytime", "nighttime", "morning", "evening",
})

# A tag with NO Chinese ideograph AND no alphanumeric content is a parse artifact
# (e.g. the bare "}" that leaks from the JSON-fallback parser) → drop it.
_HAS_CONTENT = re.compile(r"[一-鿿0-9A-Za-z]")


def is_noise(tag: str) -> bool:
    """True if the tag is an image-quality defect, a JSON/punctuation artifact, or
    a leaked English scene-descriptor — anything that isn't real content."""
    if not tag:
        return True
    t = tag.strip()
    if not t or not _HAS_CONTENT.search(t):
        return True  # empty / pure-punctuation artifact (e.g. "}")
    if t.lower() in _ENGLISH_SCENE_NOISE:
        return True  # leaked English scene word (day/interior/…)
    return any(sub in t for sub in _NOISE_SUBSTRINGS)


# CJK variant-character normalization. The VLM writes the same word with variant
# Traditional chars across frames (吧台 vs 吧檯) — exact dedup keeps both as
# separate tags. Map the variant char → one canonical so they collapse. CONSERVATIVE:
# only TRUE same-word character variants, never semantic merges (生肉/生魚 is the
# LLM-canonicalization pass's job, not this). Extend as real variants surface.
_VARIANT_MAP = {
    "檯": "台",   # 吧檯 → 吧台
    "裏": "裡",   # 裏 → 裡
    "着": "著",   # 着 → 著
    "羣": "群",   # 人羣 → 人群 (VLM mixes the 羣/群 variant across frames)
    "牀": "床",   # 牀 → 床
    "麪": "麵",   # 麪 → 麵
}

# Simplified → Traditional character normalization. qwen2.5vl leaks Simplified
# chars into its (prompted-Traditional) output, so 电子/電子, 维修/維修, 电线/電線
# coexist as separate tags across a library — a major, cross-project source of
# duplication. We have no opencc dependency, so this is a curated map of the
# high-frequency Simplified characters that actually appear in vision tags (a
# few hundred); extend as new ones surface. Per-CHARACTER (not word), so it
# composes with everything (个→個 fixes 个/個, 零个件 → 零個件, etc.).
_SIMP_TO_TRAD = {
    "电": "電", "线": "線", "维": "維", "缆": "纜", "个": "個", "纸": "紙",
    "这": "這", "们": "們", "时": "時", "应": "應", "实": "實", "现": "現",
    "发": "發", "对": "對", "开": "開", "关": "關", "门": "門", "间": "間",
    "问": "問", "题": "題", "业": "業", "东": "東", "车": "車", "长": "長",
    "马": "馬", "鸟": "鳥", "鱼": "魚", "鸡": "雞", "见": "見", "观": "觀",
    "视": "視", "觉": "覺", "说": "說", "语": "語", "读": "讀", "课": "課",
    "谁": "誰", "让": "讓", "认": "認", "识": "識", "记": "記", "设": "設",
    "计": "計", "论": "論", "议": "議", "讲": "講", "话": "話", "试": "試",
    "师": "師", "务": "務", "动": "動", "劳": "勞", "单": "單", "学": "學",
    "写": "寫", "头": "頭", "体": "體", "万": "萬", "与": "與", "专": "專",
    "丝": "絲", "双": "雙", "历": "歷", "压": "壓", "厅": "廳", "厂": "廠",
    "备": "備", "处": "處", "复": "復", "够": "夠", "构": "構", "样": "樣",
    "标": "標", "检": "檢", "测": "測", "济": "濟", "环": "環", "电": "電",
    "节": "節", "约": "約", "纲": "綱", "纳": "納", "纵": "縱", "织": "織",
    "终": "終", "组": "組", "细": "細", "经": "經", "绕": "繞", "绘": "繪",
    "给": "給", "络": "絡", "统": "統", "继": "繼", "续": "續", "绿": "綠",
    "网": "網", "罗": "羅", "义": "義", "习": "習", "乡": "鄉", "书": "書",
    "买": "買", "乱": "亂", "争": "爭", "亏": "虧", "产": "產", "亩": "畝",
    "亲": "親", "仅": "僅", "从": "從", "仑": "侖", "仓": "倉", "仪": "儀",
    "们": "們", "价": "價", "众": "眾", "优": "優", "会": "會", "伞": "傘",
    "传": "傳", "伤": "傷", "伥": "倀", "伦": "倫", "伪": "偽", "体": "體",
    "余": "餘", "佣": "傭", "侠": "俠", "侣": "侶", "侥": "僥", "侦": "偵",
    "侧": "側", "侨": "僑", "俭": "儉", "债": "債", "倾": "傾", "假": "假",
    "储": "儲", "儿": "兒", "克": "克", "兑": "兌", "兰": "蘭", "关": "關",
    "兴": "興", "兹": "茲", "养": "養", "兽": "獸", "冁": "囅", "内": "內",
    "册": "冊", "写": "寫", "军": "軍", "农": "農", "冯": "馮", "冲": "衝",
    "决": "決", "况": "況", "冻": "凍", "净": "淨", "凄": "悽", "减": "減",
    "凤": "鳳", "处": "處", "凭": "憑", "击": "擊", "凿": "鑿", "刘": "劉",
    "则": "則", "刚": "剛", "创": "創", "删": "刪", "别": "別", "刹": "剎",
    "刽": "劊", "剀": "剴", "剂": "劑", "剐": "剮", "剑": "劍", "剥": "剝",
    "剧": "劇", "劝": "勸", "办": "辦", "务": "務", "劢": "勱", "动": "動",
    "励": "勵", "劲": "勁", "劳": "勞", "势": "勢", "勐": "猛", "勚": "勩",
    "匀": "勻", "匦": "匭", "匮": "匱", "区": "區", "医": "醫", "华": "華",
    "协": "協", "单": "單", "卖": "賣", "卢": "盧", "卤": "鹵", "卫": "衛",
    "却": "卻", "卷": "捲", "厂": "廠", "厅": "廳", "历": "歷", "厉": "厲",
    "压": "壓", "厌": "厭", "厍": "厙", "厦": "廈", "厨": "廚", "厩": "廄",
    "县": "縣", "参": "參", "叆": "靉", "双": "雙", "发": "發", "变": "變",
    "叙": "敘", "叠": "疊", "号": "號", "叹": "嘆", "叽": "嘰", "吓": "嚇",
    "吕": "呂", "吗": "嗎", "员": "員", "呐": "吶", "呒": "嘸", "呓": "囈",
    "周": "週", "钳": "鉗", "钻": "鑽", "铁": "鐵", "铜": "銅", "银": "銀",
    "铝": "鋁", "锅": "鍋", "锈": "鏽", "锋": "鋒", "错": "錯", "锯": "鋸",
    "键": "鍵", "锤": "錘", "锁": "鎖", "焊": "焊", "贴": "貼", "费": "費",
    "贵": "貴", "质": "質", "购": "購", "贮": "貯", "货": "貨", "软": "軟",
    "轮": "輪", "转": "轉", "输": "輸", "适": "適", "选": "選", "递": "遞",
    "运": "運", "过": "過", "还": "還", "进": "進", "远": "遠", "连": "連",
    "迟": "遲", "达": "達", "迁": "遷",
}

_CANON_TRANS = str.maketrans({**_VARIANT_MAP, **_SIMP_TO_TRAD})


def canonicalize(tag: str) -> str:
    """Trim + normalize variant chars AND Simplified→Traditional so different
    spellings of one word collapse on dedup (吧台/吧檯 → 吧台, 电子/電子 → 電子)."""
    return (tag or "").strip().translate(_CANON_TRANS)


def guard_canonical(raw: List[str], proposed: Iterable[str], min_keep_ratio: float = 0.34) -> List[str]:
    """Sanitize an LLM tag-canonicalization proposal against the raw tag list.

    The LLM semantic merge (生肉/生魚/肉類 → 肉類) is non-deterministic and can
    (a) invent words not in the source (生食) or (b) over-merge / drop distinct
    tags. This makes it SAFE: keep only proposed tags that actually existed in
    raw (no invention), dedup preserving order, and if the merge collapses the
    list below `min_keep_ratio` of the raw count (over-aggressive), reject it and
    return raw unchanged. Combined with non-destructive storage + the UI toggle,
    the worst case is "no change", never "tags destroyed".
    """
    raw_dedup = list(dict.fromkeys(canonicalize(t) for t in raw if t))
    raw_set = set(raw_dedup)
    seen = set()
    out = []
    for t in proposed:
        t = canonicalize(t)
        if t and t in raw_set and t not in seen:
            seen.add(t)
            out.append(t)
    if not out or len(out) < max(1, int(len(raw_set) * min_keep_ratio)):
        return raw_dedup  # over-merge / empty → keep raw
    return out


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
    """Clean a list of {name, source, ...} tag dicts for a single surface (e.g. the
    inspector's per-clip tags): drop noise/artifacts, normalize each name
    (Simplified→Traditional + variant chars), and DEDUP by canonical name so
    电子/電子 and 工作台/工作檯 collapse to one chip instead of several."""
    seen = set()
    out = []
    for r in records:
        name = canonicalize(r.get("name") or "")
        if not name or is_noise(name) or name in seen:
            continue
        seen.add(name)
        out.append({**r, "name": name})
    return out


def merge_tag_records(records: Iterable[Dict]) -> List[Dict]:
    """Build the global tag cloud: drop noise, collapse variant-char spellings to
    one canonical name, and SUM their counts (人群 + 人羣 → one row, count merged).

    SQL `GROUP BY name` keeps variant spellings as separate rows; this re-aggregates
    on the canonicalized name so the cloud shows one chip per word with the true
    total. Output is sorted by merged count desc (stable on first-seen for ties).
    """
    merged: Dict[str, int] = {}
    order: Dict[str, int] = {}
    seq = 0
    for r in records or []:
        name = canonicalize(r.get("name") or "")
        if not name or is_noise(name):
            continue
        try:
            cnt = int(r.get("count") or 0)
        except (TypeError, ValueError):
            cnt = 0
        if name not in order:
            order[name] = seq
            seq += 1
        merged[name] = merged.get(name, 0) + cnt
    ranked = sorted(merged, key=lambda n: (-merged[n], order[n]))
    return [{"name": n, "count": merged[n]} for n in ranked]


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
