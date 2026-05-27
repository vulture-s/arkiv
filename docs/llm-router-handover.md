# Handover: B.0 LLM Router Refactor (Codex Job 2a)

**Date**: 2026-05-27
**Scope**: Extract `llm.py` abstraction + swap 3 existing modules (drop-in)
**Codex est**: ~2-3 hr, ~200 LOC net
**Plan reference**: `~/.claude/plans/roadmap-cuddly-goblet.md` §B.0–B.1, §B.6 Iteration 1
**Prerequisite**: v0.4.1 main (Feature A token auth shipped, tag `v0.4.1`)

---

## Goal

抽 `llm.py` 統一 LLM router abstraction。`vision.py` / `transcribe.py` / `vectordb.py` 三個既有模組改成內部 `from llm import chat, embed, vision`，**行為不變**（drop-in refactor），但建立未來 fallback chain / token tracking / multi-provider 的擴展點。

## Files to create / modify

### NEW
- `llm.py` (~150 LOC)
- `tests/test_llm_router.py` (~80 LOC)

### MODIFY
- `vision.py` — swap `requests.post(/api/generate, json={images: [b64]})` → `from llm import vision; vision(prompt=..., image_b64=b64)`
- `transcribe.py` — swap LLM call points (transcript summarization / post-processing if any) → `from llm import chat`
- `vectordb.py` — `_embed_text()` 改 call `from llm import embed`
- `config.py` — 加 `OLLAMA_CHAT_MODEL` (default `qwen2.5:14b`)、`OLLAMA_EMBED_MODEL` (default `nomic-embed-text:latest`) env var defaults

### 禁區（不准動）
- `auth.py` middleware 邏輯 / token CRUD (Feature A 已穩定)
- `ingest.py` / `mhl.py` / `offload.py` / `camera_report.py`
- `src-tauri/*` / `docs/*` 除本 README 段
- `db.py` (B.4 才動 chat tables)

## Implementation guide

### `llm.py` 大綱

```python
from enum import Enum
from typing import Optional
import time
import requests
from config import OLLAMA_URL, OLLAMA_VISION_MODEL, OLLAMA_CHAT_MODEL, OLLAMA_EMBED_MODEL


class Provider(str, Enum):
    OLLAMA = "ollama"
    # 未來 GEMINI / CEREBRAS / OPENAI ...


def chat(prompt: str, model: Optional[str] = None, system: Optional[str] = None,
         conversation: Optional[list] = None, json_mode: bool = False,
         provider: Provider = Provider.OLLAMA) -> dict:
    """統一 chat completion。
    Returns: {text: str, tokens_used: int, provider: str, model: str, latency_ms: int}
    """
    model = model or OLLAMA_CHAT_MODEL
    start = time.time()
    messages = list(conversation or [])
    if system:
        messages.insert(0, {"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": model, "messages": messages, "stream": False}
    if json_mode:
        payload["format"] = "json"
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    return {
        "text": data["message"]["content"],
        "tokens_used": data.get("eval_count", 0) + data.get("prompt_eval_count", 0),
        "provider": provider.value,
        "model": model,
        "latency_ms": int((time.time() - start) * 1000),
    }


def embed(text: str, model: Optional[str] = None,
          provider: Provider = Provider.OLLAMA) -> list[float]:
    """統一 embedding。"""
    model = model or OLLAMA_EMBED_MODEL
    r = requests.post(f"{OLLAMA_URL}/api/embeddings",
                      json={"model": model, "prompt": text}, timeout=60)
    r.raise_for_status()
    return r.json()["embedding"]


def vision(prompt: str, image_b64: str, model: Optional[str] = None) -> dict:
    """統一 vision (multimodal) call — for vision.py replacement。
    Returns: same shape as chat() but for multimodal generate endpoint.
    """
    model = model or OLLAMA_VISION_MODEL
    start = time.time()
    r = requests.post(f"{OLLAMA_URL}/api/generate",
                      json={"model": model, "prompt": prompt,
                            "images": [image_b64], "stream": False},
                      timeout=300)
    r.raise_for_status()
    data = r.json()
    return {
        "text": data["response"],
        "tokens_used": data.get("eval_count", 0) + data.get("prompt_eval_count", 0),
        "provider": "ollama",
        "model": model,
        "latency_ms": int((time.time() - start) * 1000),
    }
```

### `vision.py` swap pattern

```python
# BEFORE
r = requests.post(f"{OLLAMA_URL}/api/generate",
                  json={"model": VISION_MODEL, "prompt": prompt_text, "images": [b64]})
data = r.json()
result_text = data["response"]

# AFTER
from llm import vision
result = vision(prompt=prompt_text, image_b64=b64)
result_text = result["text"]
```

注意：`_normalize_result()` 12-field JSON parser 不動。tokens_used 多了可以 log 但不要塞 DB（DB schema 不在本 iteration 動）。

### `transcribe.py` swap

如有 transcript summarization / post-processing call Ollama 的點，改 `from llm import chat`。如果 transcribe.py **沒有 LLM call**（只用 whisper），本檔不動。

### `vectordb.py._embed_text()` swap

```python
# BEFORE (假設目前)
def _embed_text(text: str) -> list[float]:
    r = requests.post(f"{OLLAMA_URL}/api/embeddings",
                      json={"model": EMBED_MODEL, "prompt": text})
    return r.json()["embedding"]

# AFTER
from llm import embed
def _embed_text(text: str) -> list[float]:
    return embed(text)
```

## Tests to add

### `tests/test_llm_router.py`

1. **`test_chat_returns_expected_schema`**: mock `requests.post` 回 fake Ollama `/api/chat` payload，驗 `chat()` 回 `{text, tokens_used, provider, model, latency_ms}` 全在。
2. **`test_embed_returns_list_of_floats`**: mock 回 `{embedding: [0.1, 0.2, ...]}`，驗 `embed()` 回 list[float]。
3. **`test_vision_returns_expected_schema`**: mock 回 `{response: "..."}`，驗 `vision()` 回 chat-shape schema。
4. **`test_chat_json_mode_sets_format`**: 用 `json_mode=True`，驗 outgoing payload 含 `"format": "json"`。
5. **`test_token_tracking_not_negative`**: tokens_used >= 0 (即使 Ollama 沒回 eval_count)，不 None / -1。
6. **`test_chat_uses_default_model_if_none`**: 不傳 `model`，驗用 `OLLAMA_CHAT_MODEL` config default。

不測 fallback chain (本 iteration 沒實作 — 預留 hook 即可，後續 iteration B.4c 才加)。

## Acceptance criteria

1. ✅ `llm.py` exists, exports `chat / embed / vision / Provider`
2. ✅ `vision.py` 不再 `import requests`；只 `from llm import vision`
3. ✅ `vectordb.py._embed_text()` 用 `from llm import embed`
4. ✅ `tests/test_llm_router.py` 6/6 PASS
5. ✅ **既有所有 tests 不變**（pytest tests/ -v 對比 v0.4.1 baseline，9 pre-existing fails 數字一致，沒因 refactor 新增 fail）
6. ✅ Smoke test：跑一輪既有 ingest pipeline (e.g., `python ingest.py --vision-only` 對小資料夾) → vision call 用新 router、行為跟 v0.4.1 一致
7. ✅ Smoke test：跑一次 `/api/search?q=...` → embedding 走新 router、結果跟 v0.4.1 一致 (mock 或實測 Ollama)

## Edge cases

- Ollama 不在線 (`requests.exceptions.ConnectionError`) → 不 catch，讓上層處理 (現行 vision.py / vectordb.py 也是這樣，不改行為)
- `tokens_used` 計算：Ollama 0.24 起回 `eval_count` + `prompt_eval_count`，舊版可能 None — 用 `data.get(..., 0)` 防護
- `vision()` timeout 300s 對齊既有 vision.py（不要縮短）

## Codex prompt template

```
You are working on the arkiv repo (vulture-s/arkiv, currently at v0.4.1+).

Task: Implement Feature B Iteration 1 — extract `llm.py` abstraction.

Read first:
1. docs/llm-router-handover.md (this file) — full spec
2. CHANGELOG.md — to understand v0.4.1 state
3. vision.py / vectordb.py / config.py — current call sites

Implement:
1. Create llm.py per "Implementation guide" section
2. Modify vision.py / vectordb.py per "swap pattern" examples
3. (Optional) modify transcribe.py if it has LLM call points
4. Add tests/test_llm_router.py with 6 tests per "Tests to add"
5. Add OLLAMA_CHAT_MODEL + OLLAMA_EMBED_MODEL defaults to config.py

Constraints:
- ALWAYS write `request: Request` AND `_tok: dict = Depends(...)` with full type annotations — without annotations FastAPI returns 422 (per Feature A learning)
- Use `from llm import X` style imports (not `import llm`)
- Don't touch auth.py / db.py / ingest.py / mhl.py / offload.py / camera_report.py / src-tauri/* / docs/* (禁區 per handover)
- Behavior MUST be identical to v0.4.1 (drop-in refactor)

Verify:
1. pytest tests/test_llm_router.py -v → 6/6 PASS
2. pytest tests/ -v → 9 pre-existing fails unchanged (no new regressions)
3. Smoke test guide in handover §Acceptance criteria

Commit message: `refactor(llm): extract llm.py abstraction (drop-in)`

Use Windows-safe pytest TMP: `export TMP=/c/tmp` if on Windows fleet.
```

## Status

- [ ] Codex dispatched
- [ ] Codex commit applied
- [ ] CC audit + smoke (既有 ingest + search 行為不變)
- [ ] Commit pushed to arkiv main

接下來 → `docs/chat-rag-4a-handover.md` (B.4a sub-dispatch)
