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
    OLLAMA_VISION_NUM_CTX,
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
    schema: Optional[Dict[str, Any]] = None,
    provider: Provider = Provider.OLLAMA,
) -> Dict[str, Any]:
    """Call the chat model. `json_mode` asks for syntactically valid JSON; it does
    NOT constrain the shape, so the model can answer a "return {"groups":[...]}"
    prompt with {"慢跑":"路跑"} and the caller silently gets nothing. Pass
    `schema` (a JSON Schema dict) to constrain the structure at the source —
    Ollama has supported this in `format` since 0.5. Callers must STILL validate
    the parsed result (see chat.py): schema adherence is model-dependent and a
    non-conforming provider must not be able to crash the caller."""
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
    if schema:
        payload["format"] = schema
    elif json_mode:
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
    num_ctx: Optional[int] = None,
) -> Dict[str, Any]:
    model = model or OLLAMA_VISION_MODEL
    if num_ctx is None:
        num_ctx = OLLAMA_VISION_NUM_CTX
    start = time.time()
    response = _ollama_post(
        "/api/generate",
        {
            "model": model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "options": {"num_ctx": num_ctx},
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
