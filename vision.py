from __future__ import annotations
import base64
import json
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
    Run llava:7b on each frame. Returns list of {file, description, tags}.
    """
    results = []
    for path in frame_paths:
        result = _describe_one(path)
        result["file"] = path
        results.append(result)
    return results


def _normalize_result(parsed: Dict) -> Dict:
    result = _empty_result()
    result["description"] = parsed.get("description", "")
    result["tags"] = parsed.get("tags", []) or []
    for field in _VISION_FIELDS:
        result[field] = parsed.get(field)
    return result


def _describe_one(img_path: str, max_retries: int = 2) -> Dict:
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    payload = json.dumps({
        "model": VISION_MODEL,
        "prompt": PROMPT,
        "images": [b64],
        "stream": False,
    }).encode()

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                OLLAMA_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
            raw = resp.get("response", "").strip()

            # Strip markdown code fences
            import re
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            raw = raw.strip()

            # Try JSON parse
            try:
                parsed = json.loads(raw)
                result = _normalize_result(parsed)
                desc = result.get("description", "")
                # Retry if description is empty or garbage
                if not desc or desc.startswith("```"):
                    if attempt < max_retries - 1:
                        continue
                return result
            except json.JSONDecodeError:
                pass

            # Fallback: free-text parse
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("```")]
            description = lines[0] if lines else ""
            tags = [t.strip() for t in lines[-1].split(",")] if len(lines) > 1 else []
            if description:
                result = _empty_result()
                result["description"] = description
                result["tags"] = tags
                return result
            # Empty result — retry
            if attempt < max_retries - 1:
                continue
            result = _empty_result()
            result["description"] = description
            result["tags"] = tags
            return result
        except Exception as e:
            if attempt < max_retries - 1:
                continue
            print(f" [VISION FAIL: {e}]", end="", flush=True)
            result = _empty_result()
            result["error"] = str(e)
            return result

    return _empty_result()


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
