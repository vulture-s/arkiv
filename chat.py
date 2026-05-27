"""arkiv chat RAG surface.

B.4a ships the intent classifier skeleton and compilation handler only.
Remaining handlers stay as B.4b stubs.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from nanoid import generate as nanoid_generate

from config import ARKIV_CHAT_MODEL, ARKIV_INTENT_MODEL
from db import get_conn
from llm import chat as llm_chat
from vectordb import search as vector_search


INTENT_PROMPT = """你是 video archive 助手。將用戶 prompt 分類成下列其中一個 intent，回 JSON 不要任何其他文字：

- compilation: 想要剪輯素材清單（例："給我所有黃昏鏡頭"）
- refinement: 對剛剛結果再過濾（例："只要室內的"）
- similarity: 找跟某 scene 相似（例："跟這個像的"）
- analytics: 庫存統計（例："我這個月拍了幾小時"）
- general: 其他

回 JSON: {"intent": "<one of above>", "search_params": {"query": "<extracted keyword>"}, "limit": <int default 20>}
"""


def classify_intent(prompt: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    result = llm_chat(
        prompt,
        model=ARKIV_INTENT_MODEL,
        system=INTENT_PROMPT,
        conversation=history,
        json_mode=True,
    )
    try:
        parsed = json.loads(result["text"])
    except (TypeError, ValueError):
        parsed = {"intent": "general", "search_params": {"query": prompt}, "limit": 20}

    if not isinstance(parsed, dict):
        parsed = {"intent": "general", "search_params": {"query": prompt}, "limit": 20}
    if not isinstance(parsed.get("search_params"), dict):
        parsed["search_params"] = {"query": prompt}
    if not parsed["search_params"].get("query"):
        parsed["search_params"]["query"] = prompt
    try:
        parsed["limit"] = int(parsed.get("limit", 20))
    except (TypeError, ValueError):
        parsed["limit"] = 20

    parsed["tokens_used"] = result.get("tokens_used", 0)
    parsed["latency_ms"] = result.get("latency_ms", 0)
    return parsed


def handle_compilation(
    prompt: str,
    history: List[Dict[str, Any]],
    project_scope: Optional[List[str]],
    conversation_id: str,
) -> Dict[str, Any]:
    del project_scope, conversation_id
    intent = classify_intent(prompt, history)
    results = vector_search(
        intent["search_params"]["query"],
        n_results=intent.get("limit", 20),
    )
    summary_prompt = (
        "用戶問：{0}\n搜到 {1} 個 scene。"
        "用一段繁中說明找到什麼。如果結果不夠，提示用戶 refine。"
    ).format(prompt, len(results))
    summary = llm_chat(summary_prompt, model=ARKIV_CHAT_MODEL, conversation=history)
    return {
        "assistant_text": summary["text"],
        "scene_ids": [r.get("media_id") for r in results if r.get("media_id")],
        "intent": "compilation",
        "tokens_used": intent.get("tokens_used", 0) + summary.get("tokens_used", 0),
        "stage": "done",
        "latency_ms": intent.get("latency_ms", 0) + summary.get("latency_ms", 0),
    }


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


def load_history(conversation_id: str, limit: int = 10) -> List[Dict[str, str]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM chat_messages "
            "WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def conversation_exists(conversation_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM chat_conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return row is not None


def dispatch(
    prompt: str,
    conversation_id: str,
    project_scope: Optional[List[str]] = None,
) -> Dict[str, Any]:
    history = load_history(conversation_id, limit=10)
    intent_result = classify_intent(prompt, history)
    handler = HANDLERS.get(intent_result.get("intent"), handle_compilation)
    return handler(prompt, history, project_scope, conversation_id)


def create_conversation(
    user_token_id: Optional[str],
    first_prompt: str,
    project_scope: Optional[List[str]] = None,
) -> str:
    conv_id = nanoid_generate(size=12)
    title = first_prompt[:50]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_conversations (id, user_token_id, title, project_scope_json) "
            "VALUES (?, ?, ?, ?)",
            (
                conv_id,
                user_token_id,
                title,
                json.dumps(project_scope, ensure_ascii=False) if project_scope else None,
            ),
        )
    return conv_id


def persist_message(
    conversation_id: str,
    role: str,
    content: str,
    intent: Optional[str] = None,
    scene_ids: Optional[List[Any]] = None,
    tokens_used: int = 0,
    stage: Optional[str] = None,
    latency_ms: Optional[int] = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages (id, conversation_id, role, content, intent, "
            "scene_ids_json, tokens_used, stage, latency_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                nanoid_generate(size=12),
                conversation_id,
                role,
                content,
                intent,
                json.dumps(scene_ids, ensure_ascii=False) if scene_ids else None,
                tokens_used,
                stage,
                latency_ms,
            ),
        )
