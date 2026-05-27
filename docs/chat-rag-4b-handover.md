# Handover: B.4b Chat RAG Sub-Dispatch 2/3 (Codex Job 2b-b)

**Date**: 2026-05-27
**Scope**: 4 remaining handler implementations + 2 GET endpoints + tests expansion
**Codex est**: ~1-2 hr, ~250 LOC
**Plan reference**: `~/.claude/plans/roadmap-cuddly-goblet.md` §B.3 / §B.4 / §B.6 Iteration 2 row B.4b
**Prerequisite**: B.4a merged (chat baseline `feat(chat): B.4a baseline ...`)

---

## Goal

把 chat.py 從 B.4a baseline 補滿 — 4 個 handler 實作 (refinement / similarity / analytics / general) + 2 GET endpoint (history / conversations list) + tests 補到 ~10 case。

剩 edge case + integration test + ship 在 B.4c。

## Files to modify

### MODIFY
- `chat.py` — 4 handler 從 `raise NotImplementedError("B.4b")` 改實作
- `server.py` — 加 GET `/api/chat/history/{conv_id}` + GET `/api/chat/conversations`
- `tests/test_chat.py` — 從 3 case 補到 ~10 case (per handler 各 1+ test)

### NEW
- (無新檔)

### 禁區
- B.0 / B.4a 已 ship 部分不准動（chat.py classify_intent / handle_compilation / dispatch / load_history / create_conversation / persist_message 不改）
- db.py schema 不動 (B.4a 已建完整 schema)
- auth.py / llm.py / ingest.py / mhl.py / offload.py / camera_report.py / src-tauri/* / docs/*

## Implementation guide

### `chat.py` 4 handler 實作

#### `handle_refinement` — 對上輪結果再過濾

```python
def handle_refinement(prompt: str, history: list, project_scope: Optional[list],
                      conversation_id: str) -> dict:
    """從 history 最近一條 assistant msg 拿 scene_ids，做二次 filter。"""
    conn = get_conn()
    last_assistant = conn.execute(
        "SELECT scene_ids_json FROM chat_messages "
        "WHERE conversation_id = ? AND role = 'assistant' AND scene_ids_json IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()

    if not last_assistant or not last_assistant["scene_ids_json"]:
        # No prior scene set to refine — fallback to compilation
        return handle_compilation(prompt, history, project_scope, conversation_id)

    prior_ids = json.loads(last_assistant["scene_ids_json"])

    # Pull scene metadata for those ids + LLM filter
    placeholders = ",".join("?" * len(prior_ids))
    rows = conn.execute(
        f"SELECT id, description, tags, location FROM media WHERE id IN ({placeholders})",
        prior_ids,
    ).fetchall()

    filter_prompt = (
        f"用戶上輪結果 {len(rows)} 個 scene，refine 條件：{prompt}\n"
        f"場景簡述 (id: desc): " + "; ".join(f"{r['id']}: {r['description'][:80]}" for r in rows) +
        "\n回 JSON: {\"filtered_ids\": [<ids>], \"reason\": \"<一句中文\"}"
    )
    result = llm_chat(filter_prompt, conversation=history, json_mode=True)
    try:
        parsed = json.loads(result["text"])
        filtered = parsed.get("filtered_ids", prior_ids)
    except json.JSONDecodeError:
        filtered = prior_ids
        parsed = {"reason": "filter parse fail，保留全部"}

    return {
        "assistant_text": f"從 {len(prior_ids)} 個 refine 到 {len(filtered)} 個。{parsed.get('reason', '')}",
        "scene_ids": filtered,
        "tokens_used": result["tokens_used"],
        "stage": "done",
        "latency_ms": result["latency_ms"],
    }
```

#### `handle_similarity` — 找相似 scene

```python
def handle_similarity(prompt: str, history: list, project_scope: Optional[list],
                      conversation_id: str) -> dict:
    """從 prompt 抓 reference media_id (或 last scene)，用 vectordb 找近鄰。"""
    # 嘗試從 prompt 抓 reference (e.g., "跟 scene 42 像的")
    import re
    m = re.search(r"(?:scene|id)\s*(\d+)", prompt, re.IGNORECASE)

    if m:
        ref_id = int(m.group(1))
    else:
        # Fallback: last assistant scene_ids 的第一個
        conn = get_conn()
        last_assistant = conn.execute(
            "SELECT scene_ids_json FROM chat_messages "
            "WHERE conversation_id = ? AND role = 'assistant' AND scene_ids_json IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if not last_assistant:
            return handle_compilation(prompt, history, project_scope, conversation_id)
        ref_id = json.loads(last_assistant["scene_ids_json"])[0]

    # 拿 ref 的 embedding 找近鄰
    from vectordb import find_similar
    similar = find_similar(ref_id, n_results=20, project_scope=project_scope)

    summary = llm_chat(f"跟 scene {ref_id} 相似的 {len(similar)} 個 scene。一句繁中說明。",
                       conversation=history)
    return {
        "assistant_text": summary["text"],
        "scene_ids": [r["media_id"] for r in similar if r.get("media_id")],
        "tokens_used": summary["tokens_used"],
        "stage": "done",
        "latency_ms": summary["latency_ms"],
    }
```

> 注意：`vectordb.find_similar(media_id, n_results)` 可能不存在 — 若沒有，本子 dispatch 加進 `vectordb.py`（用既有 collection.query + ref 的 embedding）。若 vectordb 接口不適配，留 TODO + fallback compilation。

#### `handle_analytics` — 庫存統計

```python
def handle_analytics(prompt: str, history: list, project_scope: Optional[list],
                     conversation_id: str) -> dict:
    """對 db.py 跑 aggregate query (count / sum / group by) + LLM 回 sentence。"""
    # Use LLM to extract intent params (時段 / 統計類型)
    analytics_prompt = (
        "從用戶問題抓出統計參數，回 JSON：\n"
        "{\"metric\": \"count\" | \"duration_sum\" | \"by_project\" | \"by_month\", "
        "\"time_range\": {\"start\": \"YYYY-MM\" or null, \"end\": \"YYYY-MM\" or null}}\n"
        f"用戶問：{prompt}"
    )
    params_result = llm_chat(analytics_prompt, conversation=history, json_mode=True)
    try:
        params = json.loads(params_result["text"])
    except json.JSONDecodeError:
        params = {"metric": "count", "time_range": {"start": None, "end": None}}

    conn = get_conn()
    where = ""
    args = []
    if params["time_range"].get("start"):
        where += " AND created_at >= ?"
        args.append(params["time_range"]["start"] + "-01")
    if params["time_range"].get("end"):
        where += " AND created_at < ?"
        args.append(params["time_range"]["end"] + "-32")  # naive end-of-month

    if params["metric"] == "count":
        row = conn.execute(f"SELECT COUNT(*) AS n FROM media WHERE 1=1 {where}", args).fetchone()
        stat = f"找到 {row['n']} 個 media"
    elif params["metric"] == "duration_sum":
        row = conn.execute(f"SELECT SUM(duration_seconds) AS s FROM media WHERE 1=1 {where}", args).fetchone()
        hours = (row["s"] or 0) / 3600
        stat = f"總時長 {hours:.1f} 小時"
    elif params["metric"] == "by_project":
        rows = conn.execute(f"SELECT project_name, COUNT(*) AS n FROM media WHERE 1=1 {where} GROUP BY project_name ORDER BY n DESC", args).fetchall()
        stat = "\n".join(f"- {r['project_name']}: {r['n']}" for r in rows)
    else:  # by_month
        rows = conn.execute(f"SELECT substr(created_at, 1, 7) AS month, COUNT(*) AS n FROM media WHERE 1=1 {where} GROUP BY month ORDER BY month", args).fetchall()
        stat = "\n".join(f"- {r['month']}: {r['n']}" for r in rows)

    summary = llm_chat(f"統計結果：\n{stat}\n用一段繁中描述。", conversation=history)
    return {
        "assistant_text": summary["text"],
        "scene_ids": [],  # analytics 不返 scene_ids
        "tokens_used": params_result["tokens_used"] + summary["tokens_used"],
        "stage": "done",
        "latency_ms": params_result["latency_ms"] + summary["latency_ms"],
    }
```

#### `handle_general` — 純 chat fallback

```python
def handle_general(prompt: str, history: list, project_scope: Optional[list],
                   conversation_id: str) -> dict:
    """Fallback — 純 LLM chat 不查 db。"""
    system = ("你是 arkiv video archive 助手。用戶問題不需要查 video 庫，請直接回答。"
              "如果用戶其實想找 scene，提示他換問法（例：『給我所有黃昏鏡頭』）。")
    result = llm_chat(prompt, system=system, conversation=history)
    return {
        "assistant_text": result["text"],
        "scene_ids": [],
        "tokens_used": result["tokens_used"],
        "stage": "done",
        "latency_ms": result["latency_ms"],
    }
```

`dispatch()` 不改 — 既有 `HANDLERS.get(intent, fallback)` lookup 自動 cover 新 handler。

### `server.py` 加 2 GET endpoint

```python
@app.get("/api/chat/history/{conv_id}")
def get_chat_history(
    request: Request,
    conv_id: str,
    limit: int = 50,
    _tok: dict = Depends(require_scopes("chat_read"))
) -> dict:
    conn = get_conn()
    # Verify conv exists
    conv = conn.execute("SELECT id, title, project_scope_json, created_at, updated_at "
                        "FROM chat_conversations WHERE id = ?", (conv_id,)).fetchone()
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")

    rows = conn.execute(
        "SELECT id, role, content, intent, scene_ids_json, tokens_used, stage, latency_ms, created_at "
        "FROM chat_messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ?",
        (conv_id, limit),
    ).fetchall()

    return {
        "conversation": dict(conv),
        "messages": [dict(r) for r in rows],
    }


@app.get("/api/chat/conversations")
def list_chat_conversations(
    request: Request,
    limit: int = 50,
    _tok: dict = Depends(require_scopes("chat_read"))
) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, title, project_scope_json, created_at, updated_at "
        "FROM chat_conversations ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return {"conversations": [dict(r) for r in rows]}
```

## Tests to add (extend `tests/test_chat.py` to ~10 case)

```python
# 既有 B.4a 3 case 不動。新增：

def test_chat_refinement_filters_prior_results(fastapi_client):
    """Refinement intent 應該從上輪結果 filter，不重新 search。"""
    # mock first call (compilation) returns 5 ids
    # mock second call (refinement) filters down to 2
    ...

def test_chat_similarity_uses_reference_id(fastapi_client):
    """Similarity intent 從 prompt 抓 ref id 找近鄰。"""
    ...

def test_chat_analytics_count_intent(fastapi_client):
    """Analytics count intent 跑 SELECT COUNT。"""
    # mock LLM intent → analytics + count
    # verify SQL has COUNT(*)
    ...

def test_chat_general_intent_no_db_call(fastapi_client):
    """General intent 不應 call vectordb.search。"""
    with patch("chat.vector_search") as mock_search:
        # POST general intent prompt
        # verify mock_search NOT called
        ...

def test_chat_history_endpoint_returns_messages(fastapi_client):
    """GET /api/chat/history/{conv_id} 返 ordered messages。"""
    ...

def test_chat_history_404_for_missing_conv(fastapi_client):
    r = fastapi_client.get("/api/chat/history/nonexistent_id")
    assert r.status_code == 404

def test_chat_conversations_list_endpoint(fastapi_client):
    """GET /api/chat/conversations 返 list。"""
    ...
```

## Acceptance criteria

1. ✅ `chat.py` 4 handler 全實作 (refinement / similarity / analytics / general)
2. ✅ `server.py` 加 2 GET endpoint
3. ✅ B.4a 既有 3 test 不動 + 新增 7 test 全 PASS = 10/10
4. ✅ 既有 tests (non-chat) 不變 (9 pre-existing fails 數字一致)
5. ✅ Smoke：POST `/api/chat` 連跑 3 條 prompt (compilation → refinement → similarity)，每條 intent 不同、history 累積

## Edge cases (B.4b 範圍)

- Refinement 找不到 prior assistant scene_ids → fallback compilation
- Similarity prompt 沒寫 reference id + 沒 prior history → fallback compilation
- Analytics LLM 回非 JSON → fallback `{"metric": "count", "time_range": null}`
- Analytics `duration_seconds` column 可能 null → `or 0` 防護
- General intent prompt 太短 (< 3 char) → 還是回 LLM 不 special handle

未處理（留給 B.4c）:
- Mini Ollama timeout
- Project scope filter 完整 cross-project federation
- Oversize prompt (> context window)

## Codex prompt template

```
You are working on the arkiv repo (vulture-s/arkiv, currently at v0.4.1+ post-B.4a commit).

Task: Implement Feature B Iteration 2 sub-dispatch b (B.4b) — chat handlers expansion.

Read first:
1. docs/chat-rag-4b-handover.md (this file) — full spec
2. docs/chat-rag-4a-handover.md — for context on what B.4a shipped
3. chat.py — current state (B.4a baseline with 4 stub handlers)
4. server.py — current state

Implement:
1. chat.py: Replace `raise NotImplementedError("B.4b")` in handle_refinement / handle_similarity / handle_analytics / handle_general per Implementation guide
2. server.py: add GET /api/chat/history/{conv_id} + GET /api/chat/conversations per Implementation guide
3. tests/test_chat.py: add 7 new test cases per Tests section

Constraints:
- ALWAYS write `request: Request` AND `_tok: dict = Depends(...)` with full type annotations (per Feature A learning)
- Don't touch B.4a code: classify_intent / handle_compilation / dispatch / load_history / create_conversation / persist_message
- Don't touch llm.py / db.py schema / auth.py / ingest.py / mhl.py / offload.py / camera_report.py / src-tauri/* / docs/*
- If vectordb.find_similar() doesn't exist, add it in vectordb.py (use existing chroma collection.query with ref embedding)
- If chat.py needs additional helper (e.g., regex for ref_id extraction), inline it (don't create new module)

Verify:
1. pytest tests/test_chat.py -v → 10/10 PASS (3 existing B.4a + 7 new B.4b)
2. pytest tests/ -v → 9 pre-existing fails unchanged
3. Smoke: POST /api/chat 連跑 compilation → refinement → similarity，verify intent + scene_ids changes

Commit message: `feat(chat): B.4b — 4 handler 實作 + 2 GET endpoint`

Use Windows-safe pytest TMP: `export TMP=/c/tmp` if on Windows fleet.
```

## Status

- [ ] Codex dispatched
- [ ] Codex commit applied
- [ ] CC audit + smoke
- [ ] Commit pushed to arkiv main

接下來 → `docs/chat-rag-4c-handover.md` (B.4c edge case + integration + ship v0.5)
