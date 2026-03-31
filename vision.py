from __future__ import annotations
import base64
import json
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/generate"
VISION_MODEL = "llava:7b"
PROMPT = (
    "請用繁體中文，用1至2句話描述這個影片畫面。"
    "只描述清楚可見的內容：地點、主要事件、人物。"
    "不要推測看不清楚的細節，不要過度解釋。"
    "然後在新的一行列出 5 個關鍵詞標籤，用逗號分隔。"
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
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
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
