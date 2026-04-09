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


def _describe_one(img_path: str, max_retries: int = 2) -> dict:
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
                desc = parsed.get("description", "")
                tags = parsed.get("tags", [])
                # Retry if description is empty or garbage
                if not desc or desc.startswith("```"):
                    if attempt < max_retries - 1:
                        continue
                return {"description": desc, "tags": tags}
            except json.JSONDecodeError:
                pass

            # Fallback: free-text parse
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("```")]
            description = lines[0] if lines else ""
            tags = [t.strip() for t in lines[-1].split(",")] if len(lines) > 1 else []
            if description:
                return {"description": description, "tags": tags}
            # Empty result — retry
            if attempt < max_retries - 1:
                continue
            return {"description": description, "tags": tags}
        except Exception as e:
            if attempt < max_retries - 1:
                continue
            print(f" [VISION FAIL: {e}]", end="", flush=True)
            return {"description": "", "tags": [], "error": str(e)}

    return {"description": "", "tags": []}


def frames_to_json(results: list[dict]) -> str:
    """Serialize frame results to JSON string for DB storage."""
    return json.dumps([
        {"description": r["description"], "tags": r["tags"]}
        for r in results
    ], ensure_ascii=False)
