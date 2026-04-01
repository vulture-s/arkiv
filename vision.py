from __future__ import annotations
import base64
import json
import urllib.request

import config

OLLAMA_URL = f"{config.OLLAMA_URL}/api/generate"
VISION_MODEL = config.VISION_MODEL
PROMPT = (
    "請用繁體中文分析這個影片畫面，回傳嚴格的 JSON 格式（不要加 markdown 標記）：\n"
    '{"description": "1-2句描述可見內容（地點、事件、人物）", "tags": ["標籤1", "標籤2", "標籤3", "標籤4", "標籤5"]}\n'
    "規則：只描述清楚可見的內容，不要推測，不要過度解釋。"
)


def describe_frames(frame_paths: list[str]) -> list[dict]:
    """
    Run llava:7b on each frame. Returns list of {file, description, tags}.
    """
    results = []
    for path in frame_paths:
        result = _describe_one(path)
        result["file"] = path
        results.append(result)
    return results


def _describe_one(img_path: str) -> dict:
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    payload = json.dumps({
        "model": VISION_MODEL,
        "prompt": PROMPT,
        "images": [b64],
        "stream": False,
    }).encode()

    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        raw = resp.get("response", "").strip()

        # Try JSON parse first
        try:
            parsed = json.loads(raw)
            return {
                "description": parsed.get("description", ""),
                "tags": parsed.get("tags", []),
            }
        except json.JSONDecodeError:
            pass

        # Fallback: free-text parse
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        description = lines[0] if lines else ""
        tags = [t.strip() for t in lines[-1].split(",")] if len(lines) > 1 else []
        return {"description": description, "tags": tags}
    except Exception as e:
        return {"description": "", "tags": [], "error": str(e)}


def frames_to_json(results: list[dict]) -> str:
    """Serialize frame results to JSON string for DB storage."""
    return json.dumps([
        {"description": r["description"], "tags": r["tags"]}
        for r in results
    ], ensure_ascii=False)
