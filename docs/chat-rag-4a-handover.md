# Handover: B.4a Chat RAG Sub-Dispatch 1/3 (Codex Job 2b-a)

**Date**: 2026-05-27
**Scope**: chat DB schema + intent classifier + handle_compilation + 1 POST endpoint + auth scope + tests baseline
**Codex est**: ~1-2 hr, ~250 LOC
**Plan reference**: `~/.claude/plans/roadmap-cuddly-goblet.md` §B.2 / §B.3 / §B.4 / §B.6 Iteration 2
**Prerequisite**: B.0 LLM router merged (`refactor(llm): extract llm.py abstraction`)

---

## Goal

啟動 Chat surface 第 1 段：建 DB 2 table + chat.py intent classifier 框架 + 第 1 個 handler (`handle_compilation`) + POST `/api/chat` 1 endpoint + auth `chat_read` / `chat_write` scope。讓 chat 能跑 1 個 intent path。

剩 4 handler + 2 GET endpoint 在 B.4b、edge case 在 B.4c。

## Files to create / modify

### NEW
- `chat.py` (~150 LOC，本子 dispatch；B.4b 補到 ~250)
- `tests/test_chat.py` (~80 LOC，本子 dispatch 3 case；B.4b/c 補到 ~200)

### MODIFY
- `db.py` — `init_db()` 加 `chat_conversations` + `chat_messages` 兩 table CREATE TABLE IF NOT EXISTS (idempotent)
- `auth.py` — `SCOPES` enum 加 `chat_read` / `chat_write` (只加 enum entry，middleware 不准動)
- `server.py` — 加 POST `/api/chat` 一條 endpoint，含 `Depends(require_scopes("chat_write"))`
- `config.py` — 加 `ARKIV_CHAT_MODEL` (default `qwen2.5:14b`) + `ARKIV_INTENT_MODEL` (default `qwen2.5:7b-instruct`)

### 禁區
- B.0 已 ship 的 `llm.py` 不准動（只 import 用）
- `auth.py` middleware 邏輯 / token CRUD (Feature A) 不准重構
- `ingest.py` / `mhl.py` / `offload.py` / `camera_report.py` / `src-tauri/*` / `docs/*`
- 剩 4 handler (refinement / similarity / analytics / general) **本子 dispatch 不寫**，留 stub `raise NotImplementedError("B.4b")` 或註解

## Implementation guide

### `db.py` init_db() 加兩 table

```python
# 在 file_hash 系列 + tokens 系列之後
cur.execute("""CREATE TABLE IF NOT EXISTS chat_conversations (
  id TEXT PRIMARY KEY,                      -- nanoid 或 uuid4 hex[:12]
  user_token_id TEXT,                       -- 對應 access_tokens.id；audit who started conversation；null = bootstrap / anonymous
  title TEXT,                                -- 自動從第一條 prompt 截 50 char
  project_scope_json TEXT,                  -- JSON array of project names；null = all projects (cross-project search via federation)
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)""")
cur.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL,                       -- 'user' | 'assistant'
  content TEXT NOT NULL,
  intent TEXT,                              -- 'compilation' | 'refinement' | 'similarity' | 'analytics' | 'general' | null (user msg)
  scene_ids_json TEXT,                      -- JSON array — handler 找到的 media_id 清單
  tokens_used INTEGER DEFAULT 0,
  stage TEXT,                               -- 'parsing' | 'searching' | 'generating' | 'done' | 'error'
  latency_ms INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE
)""")
cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_msg_conv ON chat_messages(conversation_id, created_at)")
```

### `auth.py` 加 2 scope

```python
# 在 SCOPES enum 既有 read / write / admin / token_admin 之後
class Scope(str, Enum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    TOKEN_ADMIN = "token_admin"
    CHAT_READ = "chat_read"        # 新增
    CHAT_WRITE = "chat_write"      # 新增
```

middleware `require_scopes(*required)` 邏輯**不動** — 既有實作已 handle list of scope names。

### `chat.py` 大綱（本子 dispatch）

```python
"""arkiv chat — RAG over video library via 5-intent classifier + handler routing.

B.4a: skeleton + handle_compilation only. 其他 4 handler stub 在 B.4b 補。
"""
import json
from typing import Optional
import nanoid  # or uuid
from llm import chat as llm_chat
from db import get_conn
from vectordb import search as vector_search
from config import ARKIV_INTENT_MODEL

INTENT_PROMPT = """你是 video archive 助手。將用戶 prompt 分類成下列其中一個 intent，回 JSON 不要任何其他文字：

- compilation: 想要剪輯素材清單（例："給我所有黃昏鏡頭"）
- refinement: 對剛剛結果再過濾（例："只要室內的"）
- similarity: 找跟某 scene 相似（例："跟這個像的"）
- analytics: 庫存統計（例："我這個月拍了幾小時"）
- general: 其他

回 JSON: {"intent": "<one of above>", "search_params": {"query": "<extracted keyword>"}, "limit": <int default 20>}
"""


def classify_intent(prompt: str, history: list) -> dict:
    """用 ARKIV_INTENT_MODEL (輕量 7b) 分類。"""
    result = llm_chat(INTENT_PROMPT, model=ARKIV_INTENT_MODEL,
                      conversation=history, json_mode=True)
    try:
        parsed = json.loads(result["text"])
    except json.JSONDecodeError:
        parsed = {"intent": "general", "search_params": {"query": prompt}, "limit": 20}
    return {**parsed, "tokens_used": result["tokens_used"], "latency_ms": result["latency_ms"]}


def handle_compilation(prompt: str, history: list, project_scope: Optional[list],
                       conversation_id: str) -> dict:
    """剪輯素材清單 intent。"""
    intent = classify_intent(prompt, history)
    results = vector_search(intent["search_params"]["query"],
                            n_results=intent.get("limit", 20),
                            project_scope=project_scope)
    summary_prompt = (f"用戶問：{prompt}\n搜到 {len(results)} 個 scene。"
                      f"用一段繁中說明找到什麼。如果結果不夠，提示用戶 refine。")
    summary = llm_chat(summary_prompt, conversation=history)
    return {
        "assistant_text": summary["text"],
        "scene_ids": [r.get("media_id") for r in results if r.get("media_id")],
        "tokens_used": intent["tokens_used"] + summary["tokens_used"],
        "stage": "done",
        "latency_ms": intent["latency_ms"] + summary["latency_ms"],
    }


# Stub handlers — B.4b 實作
def handle_refinement(*args, **kwargs):
    raise NotImplementedError("B.4b")

def handle_similarity(*args, **kwargs):
    raise NotImplementedError("B.4b")

def handle_analytics(*args, **kwargs):
    raise NotImplementedError("B.4b")

def handle_general(*args, **kwargs):
    raise NotImplementedError("B.4b")


HANDLERS = {
    "compilation": handle_compilation,
    "refinement": handle_refinement,
    "similarity": handle_similarity,
    "analytics": handle_analytics,
    "general": handle_general,
}


def load_history(conversation_id: str, limit: int = 10) -> list:
    """從 chat_messages 拉最近 limit 條 (user + assistant 各算一條)。回 LLM-friendly format."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content FROM chat_messages "
        "WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def dispatch(prompt: str, conversation_id: str,
             project_scope: Optional[list] = None) -> dict:
    history = load_history(conversation_id, limit=10)
    intent_result = classify_intent(prompt, history)
    handler = HANDLERS.get(intent_result["intent"], handle_compilation)  # fallback compilation in B.4a
    return handler(prompt, history, project_scope, conversation_id)


def create_conversation(user_token_id: Optional[str], first_prompt: str,
                        project_scope: Optional[list] = None) -> str:
    """Create new conversation, return conv_id."""
    conv_id = nanoid.generate(size=12)
    title = first_prompt[:50]
    conn = get_conn()
    conn.execute(
        "INSERT INTO chat_conversations (id, user_token_id, title, project_scope_json) "
        "VALUES (?, ?, ?, ?)",
        (conv_id, user_token_id, title,
         json.dumps(project_scope) if project_scope else None),
    )
    conn.commit()
    return conv_id


def persist_message(conversation_id: str, role: str, content: str,
                    intent: Optional[str] = None, scene_ids: Optional[list] = None,
                    tokens_used: int = 0, stage: Optional[str] = None,
                    latency_ms: Optional[int] = None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO chat_messages (id, conversation_id, role, content, intent, "
        "scene_ids_json, tokens_used, stage, latency_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (nanoid.generate(size=12), conversation_id, role, content, intent,
         json.dumps(scene_ids) if scene_ids else None,
         tokens_used, stage, latency_ms),
    )
    conn.commit()
```

### `server.py` 加 POST endpoint

```python
from fastapi import Request, Depends
from pydantic import BaseModel
from typing import Optional
import chat


class ChatRequest(BaseModel):
    prompt: str
    conversation_id: Optional[str] = None
    project_scope: Optional[list[str]] = None


class ChatResponse(BaseModel):
    conversation_id: str
    assistant_text: str
    scene_ids: list
    intent: Optional[str] = None
    tokens_used: int
    latency_ms: int


@app.post("/api/chat", response_model=ChatResponse)
def chat_endpoint(
    request: Request,
    req: ChatRequest,
    _tok: dict = Depends(require_scopes("chat_write"))
) -> ChatResponse:
    # Create new conversation if not provided
    if req.conversation_id is None:
        conv_id = chat.create_conversation(
            user_token_id=_tok.get("token_id"),
            first_prompt=req.prompt,
            project_scope=req.project_scope,
        )
    else:
        conv_id = req.conversation_id

    # Persist user msg
    chat.persist_message(conv_id, role="user", content=req.prompt)

    # Dispatch
    result = chat.dispatch(req.prompt, conv_id, project_scope=req.project_scope)

    # Persist assistant msg
    chat.persist_message(
        conv_id, role="assistant", content=result["assistant_text"],
        intent=result.get("intent", "compilation"),  # 預設 compilation in B.4a
        scene_ids=result["scene_ids"],
        tokens_used=result["tokens_used"],
        stage=result.get("stage", "done"),
        latency_ms=result.get("latency_ms"),
    )

    return ChatResponse(
        conversation_id=conv_id,
        assistant_text=result["assistant_text"],
        scene_ids=result["scene_ids"],
        intent=result.get("intent", "compilation"),
        tokens_used=result["tokens_used"],
        latency_ms=result.get("latency_ms", 0),
    )
```

⚠️ **重要 (per Feature A learning)**：`request: Request` AND `_tok: dict = Depends(require_scopes("chat_write"))` 必須有完整 type annotation — 沒有的話 FastAPI 422。

## Tests to add (`tests/test_chat.py` baseline 3 case)

```python
"""Chat RAG test baseline — B.4a 3 case，B.4b/c 補到 ~12 case."""
import json
import pytest
from unittest.mock import patch


def test_chat_create_conversation_returns_conv_id(fastapi_client):
    """POST /api/chat 不帶 conversation_id 應創新 conv + return conv_id."""
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("chat.vector_search") as mock_search:
        mock_cls.return_value = {"intent": "compilation", "search_params": {"query": "黃昏"}, "limit": 5, "tokens_used": 100, "latency_ms": 50}
        mock_chat.return_value = {"text": "找到 3 個黃昏鏡頭", "tokens_used": 200, "latency_ms": 100, "provider": "ollama", "model": "qwen2.5:14b"}
        mock_search.return_value = [{"media_id": 1}, {"media_id": 2}, {"media_id": 3}]
        r = fastapi_client.post("/api/chat", json={"prompt": "給我黃昏鏡頭"})
        assert r.status_code == 200
        data = r.json()
        assert data["conversation_id"]  # non-empty
        assert data["intent"] == "compilation"
        assert data["scene_ids"] == [1, 2, 3]


def test_chat_continues_existing_conversation(fastapi_client):
    """帶 existing conversation_id 應 append message。"""
    # 1st POST creates conv
    with patch("chat.classify_intent"), patch("chat.llm_chat"), patch("chat.vector_search"):
        # ... setup ...
        r1 = fastapi_client.post("/api/chat", json={"prompt": "第一條"})
        conv_id = r1.json()["conversation_id"]
        r2 = fastapi_client.post("/api/chat", json={"prompt": "第二條", "conversation_id": conv_id})
        assert r2.json()["conversation_id"] == conv_id


def test_chat_requires_chat_write_scope(fastapi_client_with_readonly_token):
    """No chat_write scope → 403."""
    r = fastapi_client_with_readonly_token.post("/api/chat", json={"prompt": "x"})
    assert r.status_code == 403
```

`fastapi_client` fixture 已在 `tests/conftest.py` 設定（per Feature A A.1c），inject 含 chat_write scope 的 admin token。如需另一個 readonly fixture，加：

```python
# tests/conftest.py 加
@pytest.fixture
def fastapi_client_with_readonly_token(...):
    # similar to fastapi_client but token only has 'read' scope, no chat_write
    ...
```

## Acceptance criteria

1. ✅ `db.py init_db()` 多兩 table，跑 `python -c "from db import init_db; init_db()"` 不錯
2. ✅ `auth.py` `Scope` enum 多 `CHAT_READ` / `CHAT_WRITE`
3. ✅ `chat.py` exports `dispatch / classify_intent / create_conversation / persist_message / handle_compilation / HANDLERS / load_history`
4. ✅ `server.py` 加 POST `/api/chat`
5. ✅ `config.py` 加 `ARKIV_CHAT_MODEL` + `ARKIV_INTENT_MODEL` defaults
6. ✅ `tests/test_chat.py` 3/3 PASS
7. ✅ 既有 tests 不變 (9 pre-existing fails 數字一致)

## Edge cases (B.4a 範圍)

- POST `/api/chat` 不帶 `conversation_id` → create new
- POST 帶 invalid `conversation_id` (不在 DB) → 400 or 404 (用 FastAPI HTTPException(status_code=400))
- `classify_intent` LLM 回非 JSON → fallback `general` intent (本 dispatch 因為 stub 所以 raise NotImplementedError — B.4b 補)
- ⚠️ 本子 dispatch 不處理 mini Ollama timeout / oversize prompt → B.4c

## Codex prompt template

```
You are working on the arkiv repo (vulture-s/arkiv, currently at v0.4.1+ post-llm-router refactor commit).

Task: Implement Feature B Iteration 2 sub-dispatch a (B.4a) — chat baseline.

Read first:
1. docs/chat-rag-4a-handover.md (this file) — full spec
2. docs/llm-router-handover.md — to understand llm.py interface (just shipped in B.0)
3. db.py / auth.py / server.py / config.py — current state

Implement:
1. db.py: add 2 CREATE TABLE IF NOT EXISTS in init_db()
2. auth.py: add Scope.CHAT_READ / CHAT_WRITE enum entries (DO NOT touch middleware logic)
3. chat.py: create per Implementation guide §chat.py 大綱 (only handle_compilation real, others raise NotImplementedError("B.4b"))
4. server.py: add POST /api/chat per Implementation guide §server.py
5. config.py: add ARKIV_CHAT_MODEL + ARKIV_INTENT_MODEL defaults
6. tests/test_chat.py: 3 cases per Tests section

Constraints:
- ALWAYS write `request: Request` AND `_tok: dict = Depends(...)` with full type annotations (per Feature A learning)
- Don't touch auth.py middleware / token CRUD / llm.py / ingest.py / mhl.py / offload.py / camera_report.py / src-tauri/* / docs/*
- Don't implement refinement / similarity / analytics / general handlers (B.4b)
- Use existing conftest.fastapi_client fixture (inject admin token with chat_write scope)

Verify:
1. python -c "from db import init_db; init_db()" — no error
2. pytest tests/test_chat.py -v → 3/3 PASS
3. pytest tests/ -v → 9 pre-existing fails unchanged
4. POST /api/chat smoke (mock classify_intent + vector_search + llm_chat) returns 200 with conversation_id

Commit message: `feat(chat): B.4a baseline — DB + intent classifier + compilation handler + POST /api/chat`

Use Windows-safe pytest TMP: `export TMP=/c/tmp` if on Windows fleet.
```

## Status

- [ ] Codex dispatched
- [ ] Codex commit applied
- [ ] CC audit + smoke
- [ ] Commit pushed to arkiv main

接下來 → `docs/chat-rag-4b-handover.md` (B.4b 補 4 handler + 2 GET endpoint)
