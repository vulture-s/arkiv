from __future__ import annotations
import base64
import json
import re
import urllib.request
from typing import Dict, List

import config

OLLAMA_URL = f"{config.OLLAMA_URL}/api/generate"
VISION_MODEL = config.VISION_MODEL
PROMPT = (
    "請用繁體中文分析這個影片畫面，回傳嚴格的 JSON 格式（不要加 markdown 標記）：\n"
    "{\n"
    '  "description": "1-2句描述可見內容（地點、事件、人物）",\n'
    '  "tags": ["標籤1", "標籤2", "標籤3", "標籤4", "標籤5"],\n'
    '  "content_type": "A-Roll|B-Roll|Talking-Head|Product-Shot|Transition|Establishing|Undefined 擇一",\n'
    '  "focus_score": 1到5的整數,\n'
    '  "exposure": "dark|normal|over 擇一",\n'
    '  "stability": "穩定|輕微晃動|嚴重晃動 擇一",\n'
    '  "audio_quality": "清晰|嘈雜|靜音 擇一",\n'
    '  "atmosphere": "一個詞描述氛圍",\n'
    '  "energy": "高|中|低 擇一",\n'
    '  "edit_position": "開場|中段-轉場|中段-互動|收尾 擇一",\n'
    '  "edit_reason": "一句話說明建議用途"\n'
    "}\n"
    "規則：只描述清楚可見的內容，不要推測。所有欄位必填。"
)

LIGHT_PROMPT = (
    "請用繁體中文分析這個影片畫面，回傳嚴格的 JSON 格式（不要加 markdown 標記）：\n"
    '{"description": "一句話描述可見內容", "tags": ["標籤1","標籤2","標籤3"],'
    ' "content_type": "A-Roll|B-Roll|Talking-Head|Product-Shot|Transition|Establishing|Undefined 擇一",'
    ' "focus_score": 1到5的整數, "exposure": "dark|normal|over 擇一",'
    ' "stability": "穩定|輕微晃動|嚴重晃動 擇一", "audio_quality": "清晰|嘈雜|靜音 擇一",'
    ' "atmosphere": "一個詞描述氛圍", "energy": "高|中|低 擇一"}\n'
    "規則：只描述可見內容，不推測。所有欄位必填。"
)

_LIGHT_FIELDS = ("content_type", "focus_score", "exposure", "stability", "audio_quality", "atmosphere", "energy")

_VISION_FIELDS = (
    "content_type",
    "focus_score",
    "exposure",
    "stability",
    "audio_quality",
    "atmosphere",
    "energy",
    "edit_position",
    "edit_reason",
)


def _empty_result() -> Dict:
    result = {"description": "", "tags": []}
    for field in _VISION_FIELDS:
        result[field] = None
    return result


def describe_frames(frame_paths: List[str]) -> List[Dict]:
    """
    Representative frame strategy:
    - Middle frame: full 12-field analysis
    - Other frames: light 10-field + inherit edit_position/edit_reason
    - Skip unusable frames (black/white/blurry)
    """
    if not frame_paths:
        return []

    rep_idx = len(frame_paths) // 2
    rep_result = _describe_one(frame_paths[rep_idx])
    rep_result["file"] = frame_paths[rep_idx]

    inheritable = ("edit_position", "edit_reason")

    results = []
    for i, path in enumerate(frame_paths):
        if i == rep_idx:
            results.append(rep_result)
            continue

        if not _is_usable_frame(path):
            skip_result = _empty_result()
            skip_result["file"] = path
            skip_result["description"] = "[skipped: unusable frame]"
            for k in inheritable:
                skip_result[k] = rep_result.get(k)
            results.append(skip_result)
            continue

        light = _describe_one_light(path)
        light["file"] = path
        for k in inheritable:
            light[k] = rep_result.get(k)
        results.append(light)

    return results


def _normalize_result(parsed: Dict) -> Dict:
    result = _empty_result()
    result["description"] = parsed.get("description", "")
    result["tags"] = parsed.get("tags", []) or []
    for field in _VISION_FIELDS:
        result[field] = parsed.get(field)
    return result


def _call_vision(img_path, prompt, max_retries=2):
    """Send image to Ollama vision, return raw response text."""
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    payload = json.dumps({
        "model": VISION_MODEL,
        "prompt": prompt,
        "images": [b64],
        "stream": False,
    }).encode()
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                OLLAMA_URL, data=payload,
                headers={"Content-Type": "application/json"},
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
            raw = resp.get("response", "").strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            return raw.strip()
        except Exception:
            if attempt < max_retries - 1:
                continue
            raise
    return ""


def _describe_one(img_path, max_retries=2):
    try:
        raw = _call_vision(img_path, PROMPT, max_retries)
    except Exception as e:
        print(f" [VISION FAIL: {e}]", end="", flush=True)
        result = _empty_result()
        result["error"] = str(e)
        return result

    # Try JSON parse
    try:
        parsed = json.loads(raw)
        result = _normalize_result(parsed)
        desc = result.get("description", "")
        if not desc or desc.startswith("```"):
            return result
        return result
    except json.JSONDecodeError:
        pass

    # Fallback: free-text parse
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("```")]
    description = lines[0] if lines else ""
    tags = [t.strip() for t in lines[-1].split(",")] if len(lines) > 1 else []
    result = _empty_result()
    result["description"] = description
    result["tags"] = tags
    return result


def _describe_one_light(img_path, max_retries=2):
    """Lightweight vision: description + tags + 7 quality/content fields."""
    try:
        raw = _call_vision(img_path, LIGHT_PROMPT, max_retries)
    except Exception as e:
        print(f" [VISION-LIGHT FAIL: {e}]", end="", flush=True)
        return _empty_result()
    try:
        parsed = json.loads(raw)
        result = _empty_result()
        result["description"] = parsed.get("description", "")
        result["tags"] = parsed.get("tags", []) or []
        for field in _LIGHT_FIELDS:
            result[field] = parsed.get(field)
        return result
    except json.JSONDecodeError:
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("```")]
        result = _empty_result()
        result["description"] = lines[0] if lines else ""
        result["tags"] = [t.strip() for t in lines[-1].split(",")] if len(lines) > 1 else []
        return result


def _is_usable_frame(img_path):
    """Skip black/white/blurry frames before sending to LLM."""
    try:
        from PIL import Image
        import numpy as np
        img = np.array(Image.open(img_path).convert("L").resize((160, 90)))
        mean_brightness = float(img.mean())
        if mean_brightness < 10 or mean_brightness > 245:
            return False
        var_x = float(np.var(np.diff(img.astype(float), axis=0)))
        var_y = float(np.var(np.diff(img.astype(float), axis=1)))
        return (var_x + var_y) > 30
    except Exception:
        return True


def frames_to_json(results: List[Dict]) -> str:
    """Serialize frame results to JSON string for DB storage."""
    return json.dumps([
        {
            "description": r.get("description", ""),
            "tags": r.get("tags", []),
            "content_type": r.get("content_type"),
            "focus_score": r.get("focus_score"),
            "exposure": r.get("exposure"),
            "stability": r.get("stability"),
            "audio_quality": r.get("audio_quality"),
            "atmosphere": r.get("atmosphere"),
            "energy": r.get("energy"),
            "edit_position": r.get("edit_position"),
            "edit_reason": r.get("edit_reason"),
        }
        for r in results
    ], ensure_ascii=False)
