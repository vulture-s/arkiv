from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, List, Optional

import requests

from config import (
    OLLAMA_CHAT_MODEL,
    OLLAMA_EMBED_MODEL,
    OLLAMA_URL,
    OLLAMA_VISION_MODEL,
)


EMBED_MAX_CHARS = 2000


class Provider(str, Enum):
    OLLAMA = "ollama"


def _safe_nonnegative_int(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _ollama_post(path: str, payload: Dict[str, Any], timeout: int):
    return requests.post("{0}{1}".format(OLLAMA_URL, path), json=payload, timeout=timeout)


def chat(
    prompt: str,
    model: Optional[str] = None,
    system: Optional[str] = None,
    conversation: Optional[List[Dict[str, Any]]] = None,
    json_mode: bool = False,
    provider: Provider = Provider.OLLAMA,
) -> Dict[str, Any]:
    model = model or OLLAMA_CHAT_MODEL
    start = time.time()

    messages = list(conversation or [])
    if system:
        messages.insert(0, {"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"

    response = _ollama_post("/api/chat", payload, timeout=120)
    response.raise_for_status()
    data = response.json()
    return {
        "text": data.get("message", {}).get("content", ""),
        "tokens_used": _safe_nonnegative_int(data.get("eval_count")) + _safe_nonnegative_int(
            data.get("prompt_eval_count")
        ),
        "provider": provider.value,
        "model": model,
        "latency_ms": int((time.time() - start) * 1000),
    }


def embed(
    text: str,
    model: Optional[str] = None,
    provider: Provider = Provider.OLLAMA,
) -> List[float]:
    del provider
    model = model or OLLAMA_EMBED_MODEL
    payload = {"model": model, "prompt": text[:EMBED_MAX_CHARS]}
    response = _ollama_post("/api/embeddings", payload, timeout=30)
    response.raise_for_status()
    return response.json()["embedding"]


def vision(
    prompt: str,
    image_b64: str,
    model: Optional[str] = None,
    provider: Provider = Provider.OLLAMA,
) -> Dict[str, Any]:
    model = model or OLLAMA_VISION_MODEL
    start = time.time()
    response = _ollama_post(
        "/api/generate",
        {
            "model": model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
        },
        timeout=300,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "text": data.get("response", ""),
        "tokens_used": _safe_nonnegative_int(data.get("eval_count")) + _safe_nonnegative_int(
            data.get("prompt_eval_count")
        ),
        "provider": provider.value,
        "model": model,
        "latency_ms": int((time.time() - start) * 1000),
    }
