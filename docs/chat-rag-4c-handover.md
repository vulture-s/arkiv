# Handover: B.4c Chat RAG Sub-Dispatch 3/3 (Codex Job 2b-c)

**Date**: 2026-05-27
**Scope**: Edge cases (timeout / oversize / empty intent) + project scope filter + integration test + README/CHANGELOG + v0.5.0 release
**Codex est**: ~1 hr, ~150 LOC
**Plan reference**: `~/.claude/plans/roadmap-cuddly-goblet.md` §B.4 / §B.5 / §B.6 Iteration 2 row B.4c / §B.7
**Prerequisite**: B.4a + B.4b merged

---

## Goal

收尾 Chat surface — 補齊 edge case 防護 (Ollama timeout / oversize prompt / empty intent / classifier 失敗) + project scope filter 完整實作 + integration test 涵蓋 a/b/c + 文件 (README EN/zh-TW + CHANGELOG v0.5.0) + tag + GitHub Release。

## Files to modify

### MODIFY
- `chat.py` — 加 timeout 防護 + oversize prompt trimming + classify_intent fallback
- `vectordb.py` — `search()` + `find_similar()` 加 `project_scope` 參數實作 (B.4a/b 假設存在但沒實作)
- `tests/test_chat.py` — 加 ~5 case (edge + project scope + integration end-to-end)
- `README.md` — Features 段加 chat + quickstart 範例
- `README.zh-TW.md` — 同步加
- `CHANGELOG.md` — `## v0.5.0 - YYYY-MM-DD` entry

### NEW
- (無新檔)

### 禁區
- B.0 llm.py / B.4a baseline / B.4b 4 handler 既有實作不准動 (只在 chat.py 加 wrapper / 補 defensive code)
- auth.py / db.py / ingest.py / mhl.py / offload.py / camera_report.py / src-tauri/* / docs/* 除 README
- 不准開新 Codex sub-dispatch (本 dispatch 是 B 鏈最後一段)

## Implementation guide

### 1. Edge case 防護 (`chat.py`)

#### Ollama timeout

```python
# llm.py 既有 chat() 已 raise requests.exceptions.Timeout 給上層
# chat.py 在 dispatch 加 try/except + persist error msg

def dispatch(prompt: str, conversation_id: str,
             project_scope: Optional[list] = None) -> dict:
    import requests
    history = load_history(conversation_id, limit=10)
    try:
        intent_result = classify_intent(prompt, history)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        return {
            "assistant_text": f"LLM 服務暫時無回應（{type(e).__name__}），請稍後再試。",
            "scene_ids": [],
            "tokens_used": 0,
            "stage": "error",
            "intent": "general",
            "latency_ms": 0,
        }

    handler = HANDLERS.get(intent_result["intent"], handle_general)
    try:
        result = handler(prompt, history, project_scope, conversation_id)
        result["intent"] = intent_result["intent"]
        return result
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        return {
            "assistant_text": f"處理時 LLM 失聯（{type(e).__name__}），已記錄 intent={intent_result['intent']}。",
            "scene_ids": [],
            "tokens_used": intent_result["tokens_used"],
            "stage": "error",
            "intent": intent_result["intent"],
            "latency_ms": intent_result["latency_ms"],
        }
```

#### Oversize prompt

```python
# chat.py top
MAX_PROMPT_CHARS = 4000  # 約 1500 tokens for Chinese, leave room for system + history

def _trim_prompt(prompt: str) -> str:
    """Prompt 超長就截斷 + 加標記。"""
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt
    return prompt[:MAX_PROMPT_CHARS - 50] + "\n[...prompt 過長已截斷]"

# dispatch() 開頭加：
prompt = _trim_prompt(prompt)
```

#### Empty intent / classifier 失敗

B.4a 已有 try/except json 解析 fallback `general`，本 dispatch 額外加：

```python
# classify_intent() 加 validate:
def classify_intent(prompt: str, history: list) -> dict:
    result = llm_chat(INTENT_PROMPT, model=ARKIV_INTENT_MODEL,
                      conversation=history, json_mode=True)
    try:
        parsed = json.loads(result["text"])
    except json.JSONDecodeError:
        parsed = {"intent": "general", "search_params": {"query": prompt}, "limit": 20}

    # Validate intent in known set
    KNOWN_INTENTS = {"compilation", "refinement", "similarity", "analytics", "general"}
    if parsed.get("intent") not in KNOWN_INTENTS:
        parsed["intent"] = "general"

    # Ensure search_params + limit
    parsed.setdefault("search_params", {"query": prompt})
    parsed.setdefault("limit", 20)
    parsed["limit"] = min(int(parsed["limit"]), 100)  # cap

    return {**parsed, "tokens_used": result["tokens_used"], "latency_ms": result["latency_ms"]}
```

### 2. Project scope filter (`vectordb.py`)

B.4a/b assume `vector_search(query, n_results, project_scope=None)` 接 project_scope。本 dispatch 實作：

```python
# vectordb.py
from typing import Optional

def search(query: str, n_results: int = 20,
           project_scope: Optional[list[str]] = None) -> list[dict]:
    embedding = _embed_text(query)
    where = None
    if project_scope:
        where = {"project_name": {"$in": project_scope}}  # Chroma filter syntax
    results = collection.query(
        query_embeddings=[embedding],
        n_results=n_results,
        where=where,
    )
    # ... existing post-processing ...
    return processed


def find_similar(media_id: int, n_results: int = 20,
                 project_scope: Optional[list[str]] = None) -> list[dict]:
    # Get ref embedding from collection by id
    ref = collection.get(ids=[str(media_id)], include=["embeddings"])
    if not ref["embeddings"]:
        return []
    where = None
    if project_scope:
        where = {"project_name": {"$in": project_scope}}
    results = collection.query(
        query_embeddings=ref["embeddings"],
        n_results=n_results + 1,  # +1 because first result = self
        where=where,
    )
    # filter out self + return
    return [r for r in processed if r["media_id"] != media_id][:n_results]
```

### 3. Integration test (`tests/test_chat.py` ~5 new case)

```python
def test_chat_handles_ollama_timeout(fastapi_client):
    """LLM timeout 應該 graceful error msg 不 500。"""
    import requests
    with patch("chat.classify_intent", side_effect=requests.exceptions.Timeout):
        r = fastapi_client.post("/api/chat", json={"prompt": "test"})
        assert r.status_code == 200  # not 500
        assert "暫時無回應" in r.json()["assistant_text"]
        assert r.json()["intent"] == "general"


def test_chat_trims_oversize_prompt(fastapi_client):
    long_prompt = "x" * 10000  # > MAX_PROMPT_CHARS
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("chat.vector_search"):
        mock_cls.return_value = {"intent": "general", "search_params": {"query": "x"}, "limit": 20, "tokens_used": 50, "latency_ms": 10}
        mock_chat.return_value = {"text": "ok", "tokens_used": 100, "latency_ms": 20, "provider": "ollama", "model": "x"}
        r = fastapi_client.post("/api/chat", json={"prompt": long_prompt})
        assert r.status_code == 200
        # 驗 classify_intent 收到的 prompt 是 trimmed
        called_prompt = mock_cls.call_args[0][0]
        assert len(called_prompt) <= 4000


def test_chat_classifier_fallback_on_invalid_intent(fastapi_client):
    """LLM 回非法 intent name → fallback general."""
    with patch("chat.llm_chat") as mock_chat, patch("chat.vector_search"):
        mock_chat.return_value = {"text": '{"intent": "bogus_intent", "search_params": {"query": "x"}, "limit": 20}',
                                  "tokens_used": 50, "latency_ms": 10, "provider": "ollama", "model": "x"}
        r = fastapi_client.post("/api/chat", json={"prompt": "test"})
        assert r.status_code == 200
        # general intent handler 沒查 db
        assert r.json()["intent"] == "general"


def test_chat_project_scope_passes_to_vectordb(fastapi_client):
    """project_scope 應該傳到 vector_search 的 filter。"""
    with patch("chat.vector_search") as mock_search, patch("chat.llm_chat"), patch("chat.classify_intent") as mock_cls:
        mock_cls.return_value = {"intent": "compilation", "search_params": {"query": "x"}, "limit": 5, "tokens_used": 50, "latency_ms": 10}
        r = fastapi_client.post("/api/chat", json={"prompt": "test", "project_scope": ["projectA"]})
        assert r.status_code == 200
        # vector_search 被呼叫時 project_scope=["projectA"]
        _, kwargs = mock_search.call_args
        assert kwargs.get("project_scope") == ["projectA"]


def test_chat_full_flow_compilation_to_refinement(fastapi_client):
    """End-to-end: 第一條 compilation → 第二條 refinement 拿到上輪 ids 過濾。"""
    # 連跑兩條 POST，verify history persistence + refinement filter behavior
    ...
```

### 4. README updates

#### `README.md` (EN) Features 段

```markdown
### Chat — RAG over your video library

Ask natural language questions about your archive:

- "Give me all sunset shots from May" → compilation intent
- "Only the indoor ones" → refinement (filters last result)
- "Similar to scene 42" → similarity (vector neighbor search)
- "How many hours did I shoot this month?" → analytics

5-intent classifier routes to specialized handler. Conversation history persists.

**Quickstart**:

```bash
# 1. Create conversation
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $ARKIV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Give me all sunset shots"}'

# Returns: {"conversation_id": "abc123", "assistant_text": "...", "scene_ids": [1,2,3], ...}

# 2. Continue conversation
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $ARKIV_TOKEN" \
  -d '{"prompt": "Only indoor ones", "conversation_id": "abc123"}'
```

#### `README.zh-TW.md` 同步加繁中版

### 5. CHANGELOG `v0.5.0` entry

```markdown
## [v0.5.0] - 2026-05-XX

### Added — Chat: RAG over your video library

- **5-intent classifier** routes natural language prompts to specialized handlers:
  - `compilation` — search scenes by description
  - `refinement` — filter prior results
  - `similarity` — find scenes similar to a reference
  - `analytics` — aggregate stats (count, duration, by project/month)
  - `general` — pure chat fallback
- **Conversation memory**: chat history persists to DB, last 10 messages threaded into next prompt
- **3 new endpoints**: `POST /api/chat`, `GET /api/chat/history/{conv_id}`, `GET /api/chat/conversations`
- **2 new scopes**: `chat_read`, `chat_write` (Bearer token required per Feature A)
- **Project scope filter**: optionally limit search to specific projects
- **Edge case防護**: Ollama timeout → graceful error; oversize prompt → trim; invalid intent → fallback `general`

### Refactored

- `llm.py` — new LLM router abstraction; `vision.py` / `transcribe.py` / `vectordb.py` swapped to use unified interface (drop-in, behavior identical)

### Tests

- +15 new tests (5 LLM router + 10 chat handlers / endpoints / edge)
- Total: 169 passed / 9 pre-existing Windows POSIX fails (unchanged from v0.4.1)
```

### 6. Tag + GitHub Release

```bash
# CC 在 main:
git tag v0.5.0
git push origin v0.5.0
gh release create v0.5.0 \
  --title "v0.5.0 — Chat: RAG over your video library" \
  --notes-from-tag
```

## Acceptance criteria

1. ✅ `chat.py` dispatch 有 try/except for timeout + connection error
2. ✅ `chat.py` `_trim_prompt` + `MAX_PROMPT_CHARS=4000` + dispatch 開頭呼叫
3. ✅ `chat.py` `classify_intent` 加 validate fallback `general`
4. ✅ `vectordb.py` `search()` + `find_similar()` 接 `project_scope` 參數 + Chroma where filter
5. ✅ `tests/test_chat.py` 15/15 PASS (3 B.4a + 7 B.4b + 5 B.4c)
6. ✅ 既有 tests 不變 (9 pre-existing fails 數字一致)
7. ✅ README EN + zh-TW 加 chat features 段 + quickstart
8. ✅ CHANGELOG v0.5.0 entry
9. ✅ tag v0.5.0 pushed
10. ✅ GitHub Release page live

## Edge cases (B.4c 全範圍)

- Ollama down → graceful "服務暫時無回應" + intent=general + tokens_used=0
- Oversize prompt (>4000 char) → trim with marker
- Classifier 回 invalid intent (e.g., "bogus_xyz") → fallback general
- Classifier 回 limit > 100 → cap to 100
- Project scope empty list `[]` (vs None) → treat as "no filter" (Chroma where=None)
- find_similar ref id 不在 collection → return [] (don't crash)

## Smoke test (mini SSH → PC arkiv 驗 IP allowlist)

```bash
# From mini (after Feature A token bootstrap):
export ARKIV_TOKEN="<mini-token>"
curl -X POST http://<pc-tailscale-ip>:8000/api/chat \
  -H "Authorization: Bearer $ARKIV_TOKEN" \
  -d '{"prompt": "test from mini"}' \
  | jq

# 驗：
# 1. 200 OK (auth.py IP allowlist 通過)
# 2. assistant_text 非空
# 3. PC arkiv DB 有新 chat_conversations row
```

## Codex prompt template

```
You are working on the arkiv repo (vulture-s/arkiv, currently at v0.4.1+ post-B.4a+B.4b commits).

Task: Implement Feature B Iteration 2 sub-dispatch c (B.4c) — chat edge cases + project scope + ship v0.5.0.

Read first:
1. docs/chat-rag-4c-handover.md (this file) — full spec
2. docs/chat-rag-4a-handover.md + docs/chat-rag-4b-handover.md — for context
3. chat.py / vectordb.py — current state

Implement:
1. chat.py: add try/except for timeout + connection error in dispatch; add _trim_prompt + MAX_PROMPT_CHARS; add classify_intent validate fallback
2. vectordb.py: add project_scope param to search() + find_similar(); use Chroma where filter
3. tests/test_chat.py: add 5 new test cases per Tests section
4. README.md (EN): add Chat Features section + quickstart curl example
5. README.zh-TW.md: 同步加繁中版
6. CHANGELOG.md: add v0.5.0 entry per template
7. tag v0.5.0 + push + GitHub release

Constraints:
- ALWAYS write `request: Request` AND `_tok: dict = Depends(...)` with full type annotations
- Don't touch B.0 llm.py / B.4a baseline / B.4b 4 handler 既有實作 (only add defensive wrappers in chat.py and add project_scope to vectordb.py)
- Don't touch auth.py / db.py schema / ingest.py / mhl.py / offload.py / camera_report.py / src-tauri/* / docs/*
- Use existing pytest + conftest.fastapi_client

Verify:
1. pytest tests/test_chat.py -v → 15/15 PASS (3 + 7 + 5)
2. pytest tests/ -v → 9 pre-existing fails unchanged
3. README EN + zh-TW 都有 Chat section + quickstart
4. CHANGELOG.md 有 v0.5.0 entry
5. tag v0.5.0 pushed + GitHub Release live

Commit message (split into 2):
- `feat(chat): B.4c — edge case 防護 + project scope filter + integration test`
- `release(chat): v0.5.0 — Chat RAG ship`

Use Windows-safe pytest TMP: `export TMP=/c/tmp` if on Windows fleet.
```

## Status

- [ ] Codex dispatched
- [ ] Codex commit applied
- [ ] CC audit + smoke (mini SSH → PC arkiv 驗 IP allowlist)
- [ ] tag v0.5.0 pushed
- [ ] GitHub Release page live

完成後 v0.5 Chat feature 整套 ship。
