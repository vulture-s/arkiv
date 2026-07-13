from __future__ import annotations
import base64
import json
import re
from typing import Dict, List, Optional

import requests

import config
from llm import vision

OLLAMA_URL = f"{config.OLLAMA_URL}/api/generate"

_model_avail_cache: Dict[str, bool] = {}


def model_available(name: str) -> bool:
    """True if `name` is installed in the local Ollama (matched by tag or base
    name). Cached per process. Lets the fallback path skip cleanly when its model
    isn't pulled instead of erroring (404) once per failed frame."""
    if not name:
        return False
    if name in _model_avail_cache:
        return _model_avail_cache[name]
    avail = False
    try:
        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=5)
        installed = {m.get("name", "") for m in r.json().get("models", [])}
        base = name.split(":")[0]
        avail = name in installed or any(n.split(":")[0] == base for n in installed)
    except Exception:
        avail = False
    _model_avail_cache[name] = avail
    return avail


# Vision-capable model name families — used as a fallback when Ollama's
# /api/show doesn't report capabilities (older Ollama) or is unreachable.
_VISION_NAME_RE = re.compile(
    r"(?:^|[-_/.\d])v(?:l|ision)(?:[-_.:]|$)"   # …vl / …-vision (qwen2.5vl, qwen3-vl, llama3.2-vision)
    r"|llava|bakllava|moondream|minicpm-v|granite[\d.]*-vision|cogvlm",
    re.IGNORECASE,
)

_vision_capable_cache: Dict[str, bool] = {}


def _is_vision_model(name: str) -> bool:
    """Best-effort vision-capability check for an installed Ollama model. Prefers
    Ollama's own reported capabilities (POST /api/show → capabilities: [...]),
    falling back to a name heuristic when capabilities are absent (older Ollama)
    or the show call fails. Cached per process."""
    if not name:
        return False
    if name in _vision_capable_cache:
        return _vision_capable_cache[name]
    result = None
    try:
        r = requests.post(f"{config.OLLAMA_URL}/api/show", json={"name": name}, timeout=5)
        if r.ok:
            caps = r.json().get("capabilities")
            if isinstance(caps, list):
                result = "vision" in caps
    except Exception:
        result = None  # fall through to the name heuristic
    if result is None:
        result = bool(_VISION_NAME_RE.search(name))
    _vision_capable_cache[name] = result
    return result


def list_vision_models() -> List[str]:
    """Installed Ollama models that can do vision, for the ingest-setup picker so
    the UI is driven by backend truth instead of a hardcoded list. Returns a
    sorted list of model tags; empty when Ollama is unreachable (the UI then
    falls back to a free-text vision-model field)."""
    try:
        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=5)
        installed = [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
    except Exception:
        return []
    return sorted(n for n in installed if _is_vision_model(n))
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
    "規則：只描述清楚可見的內容，不要推測。所有欄位必填。\n"
    "標籤精準度：不確定具體品項時用上層通稱（魚→「魚肉」、肉→「生肉」），不要猜具體品項（鮪魚／三文魚／牛肉）。"
    "同一個概念固定只用一個詞，不要交替使用近義詞或異體字——例：夜景／夜間、生肉／生魚／肉類、戶外／戶外活動、吧台／吧檯，各擇一個固定用，全片一致。"
    "每個標籤只用一個詞；寧可通用正確，也不要具體但可能錯。"
)

LIGHT_PROMPT = (
    "請用繁體中文分析這個影片畫面，回傳嚴格的 JSON 格式（不要加 markdown 標記）：\n"
    '{"description": "一句話描述可見內容", "tags": ["標籤1","標籤2","標籤3"],'
    ' "content_type": "A-Roll|B-Roll|Talking-Head|Product-Shot|Transition|Establishing|Undefined 擇一",'
    ' "focus_score": 1到5的整數, "exposure": "dark|normal|over 擇一",'
    ' "stability": "穩定|輕微晃動|嚴重晃動 擇一", "audio_quality": "清晰|嘈雜|靜音 擇一",'
    ' "atmosphere": "一個詞描述氛圍", "energy": "高|中|低 擇一",'
    ' "edit_position": "開場|中段-轉場|中段-互動|收尾 擇一"}\n'
    "規則：只描述可見內容，不推測。所有欄位必填。"
    "不確定具體品項用上層通稱（魚→「魚肉」、肉→「生肉」，不猜鮪魚／牛肉）；同一概念固定用一個詞、勿用近義詞或異體字，每標籤一個詞。"
)

_LIGHT_FIELDS = ("content_type", "focus_score", "exposure", "stability", "audio_quality", "atmosphere", "energy", "edit_position")

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


def describe_frames(frame_paths: List[str], model: Optional[str] = None) -> List[Dict]:
    """
    Representative frame strategy:
    - Middle frame: full 12-field analysis
    - Other frames: light 11-field + inherit edit_reason only
    - Skip unusable frames (black/white/blurry)

    `model` is threaded down to `_call_vision`; when None the effective/default
    model is resolved from settings (behavior-preserving). Callers pass an explicit
    model name to force the fallback path onto that model (round-5 #50).
    """
    if not frame_paths:
        return []

    rep_idx = len(frame_paths) // 2
    rep_result = _describe_one(frame_paths[rep_idx], model=model)
    rep_result["file"] = frame_paths[rep_idx]

    inheritable = ("edit_reason",)

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

        light = _describe_one_light(path, model=model)
        light["file"] = path
        for k in inheritable:
            light[k] = rep_result.get(k)
        results.append(light)

    return results


def _normalize_result(parsed: Dict) -> Dict:
    result = _empty_result()
    result["description"] = parsed.get("description", "")
    # Sanitize at the source: drop empty / pure-punctuation tags (e.g. the bare
    # "}" the JSON-fallback parser leaks) so they never enter the DB. Read-side
    # filtering (tag_quality.is_noise) also screens them, this just keeps storage clean.
    result["tags"] = [
        t.strip() for t in (parsed.get("tags") or [])
        if isinstance(t, str) and t.strip() and re.search(r"[一-鿿0-9A-Za-z]", t)
    ]
    for field in _VISION_FIELDS:
        result[field] = parsed.get(field)
    return result


def _call_vision(img_path, prompt, max_retries=2, model=None):
    """Send image to Ollama vision, return raw response text.

    `model`, when given, is used verbatim (this is how the failure-fallback path
    forces the fallback model to actually run — round-5 #50). When None, Phase 9.7
    G5③ honors the operator's library default (settings table), falling back to
    config.VISION_MODEL / num_ctx when unset (behavior-preserving)."""
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    import settings as _settings
    eff_model = model if model is not None else _settings.vision_model()
    eff_num_ctx = _settings.vision_num_ctx()
    for attempt in range(max_retries):
        try:
            resp = vision(prompt=prompt, image_b64=b64, model=eff_model, num_ctx=eff_num_ctx)
            raw = resp.get("text", "").strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            return raw.strip()
        except Exception:
            if attempt < max_retries - 1:
                continue
            raise
    return ""


def _describe_one(img_path, max_retries=2, model=None):
    try:
        raw = _call_vision(img_path, PROMPT, max_retries, model=model)
    except Exception as e:
        print(f" [VISION FAIL: {e}]", end="", flush=True)
        result = _empty_result()
        result["error"] = str(e)
        return result

    # Try JSON parse
    try:
        parsed = json.loads(raw)
        return _normalize_result(parsed)
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


def _describe_one_light(img_path, max_retries=2, model=None):
    """Lightweight vision: description + tags + 7 quality/content fields."""
    try:
        raw = _call_vision(img_path, LIGHT_PROMPT, max_retries, model=model)
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
