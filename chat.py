"""arkiv chat RAG surface.

B.4a ships the intent classifier skeleton and compilation handler only.
Remaining handlers stay as B.4b stubs.
"""
from __future__ import annotations

import json
import re
import requests
from typing import Any, Dict, List, Optional

from nanoid import generate as nanoid_generate

from config import ARKIV_CHAT_MODEL, ARKIV_INTENT_MODEL
from db import get_conn
from llm import chat as llm_chat
from vectordb import EmbeddingDimensionMismatch, search as vector_search


INTENT_PROMPT = """你是 video archive 助手。將用戶 prompt 分類成下列其中一個 intent，回 JSON 不要任何其他文字：

- compilation: 想要剪輯素材清單（例："給我所有黃昏鏡頭"）
- refinement: 對剛剛結果再過濾（例："只要室內的"）
- similarity: 找跟某 scene 相似（例："跟這個像的"）
- analytics: 庫存統計（例："我這個月拍了幾小時"）
- general: 其他

回 JSON: {"intent": "<one of above>", "search_params": {"query": "<extracted keyword>"}, "limit": <int default 20>}
"""

MAX_PROMPT_CHARS = 4000
KNOWN_INTENTS = {"compilation", "refinement", "similarity", "analytics", "general"}


def _trim_prompt(prompt: str) -> str:
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt
    return prompt[:MAX_PROMPT_CHARS - 50] + "\n[...prompt 過長已截斷]"


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
    if parsed.get("intent") not in KNOWN_INTENTS:
        parsed["intent"] = "general"
    if not isinstance(parsed.get("search_params"), dict):
        parsed["search_params"] = {"query": prompt}
    if not parsed["search_params"].get("query"):
        parsed["search_params"]["query"] = prompt
    try:
        parsed["limit"] = int(parsed.get("limit", 20))
    except (TypeError, ValueError):
        parsed["limit"] = 20
    parsed["limit"] = min(parsed["limit"], 100)

    parsed["tokens_used"] = result.get("tokens_used", 0)
    parsed["latency_ms"] = result.get("latency_ms", 0)
    return parsed


def handle_compilation(
    prompt: str,
    history: List[Dict[str, Any]],
    project_scope: Optional[List[str]],
    conversation_id: str,
) -> Dict[str, Any]:
    del conversation_id
    intent = classify_intent(prompt, history)
    results = vector_search(
        intent["search_params"]["query"],
        n_results=intent.get("limit", 20),
        project_scope=project_scope,
    )
    summary_prompt = (
        "用戶問：{0}\n從素材庫搜到 {1} 段符合的素材。"
        "用一段繁中說明『找到了哪些素材』。這是搜尋結果，不是剪好的成片——"
        "arkiv 不會自動剪輯。措辭要誠實：說『找到 N 段符合的鏡頭，"
        "可以選來導出 EDL / FCPXML / SRT 拿去 Resolve 剪』，"
        "絕對不要說『我幫你剪成一段』或暗示已經剪好。"
        "如果結果不夠精準，提示用戶 refine 查詢。"
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


def _media_summary(row) -> str:
    parts = [row["filename"] or "untitled"]
    if row["transcript"]:
        parts.append(row["transcript"][:120])
    if row["frame_tags"]:
        parts.append(row["frame_tags"][:160])
    return " ".join(parts)


def handle_refinement(
    prompt: str,
    history: List[Dict[str, Any]],
    project_scope: Optional[List[str]],
    conversation_id: str,
) -> Dict[str, Any]:
    with get_conn() as conn:
        last_assistant = conn.execute(
            "SELECT scene_ids_json FROM chat_messages "
            "WHERE conversation_id = ? AND role = 'assistant' AND scene_ids_json IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()

        if not last_assistant or not last_assistant["scene_ids_json"]:
            return handle_compilation(prompt, history, project_scope, conversation_id)

        try:
            prior_ids = json.loads(last_assistant["scene_ids_json"])
        except (TypeError, ValueError):
            prior_ids = []

        if not prior_ids:
            return handle_compilation(prompt, history, project_scope, conversation_id)

        placeholders = ",".join("?" * len(prior_ids))
        rows = conn.execute(
            "SELECT id, filename, transcript, frame_tags FROM media "
            "WHERE id IN ({0})".format(placeholders),
            prior_ids,
        ).fetchall()

    scene_text = "; ".join(
        "{0}: {1}".format(row["id"], _media_summary(row)[:120]) for row in rows
    )
    filter_prompt = (
        "用戶上輪結果 {0} 個 scene，refine 條件：{1}\n"
        "場景簡述 (id: desc): {2}\n"
        "回 JSON: {{\"filtered_ids\": [<ids>], \"reason\": \"<一句中文>\"}}"
    ).format(len(rows), prompt, scene_text)
    result = llm_chat(filter_prompt, model=ARKIV_CHAT_MODEL, conversation=history, json_mode=True)
    try:
        parsed = json.loads(result["text"])
        filtered = parsed.get("filtered_ids", prior_ids)
    except (TypeError, ValueError):
        parsed = {"reason": "filter parse fail，保留全部"}
        filtered = prior_ids

    if not isinstance(filtered, list):
        filtered = prior_ids
    return {
        "assistant_text": "從 {0} 個 refine 到 {1} 個。{2}".format(
            len(prior_ids), len(filtered), parsed.get("reason", "")
        ),
        "scene_ids": filtered,
        "intent": "refinement",
        "tokens_used": result.get("tokens_used", 0),
        "stage": "done",
        "latency_ms": result.get("latency_ms", 0),
    }


def handle_similarity(
    prompt: str,
    history: List[Dict[str, Any]],
    project_scope: Optional[List[str]],
    conversation_id: str,
) -> Dict[str, Any]:
    match = re.search(r"(?:scene|id)\s*(\d+)", prompt, re.IGNORECASE)
    if match:
        ref_id = int(match.group(1))
    else:
        with get_conn() as conn:
            last_assistant = conn.execute(
                "SELECT scene_ids_json FROM chat_messages "
                "WHERE conversation_id = ? AND role = 'assistant' AND scene_ids_json IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        if not last_assistant or not last_assistant["scene_ids_json"]:
            return handle_compilation(prompt, history, project_scope, conversation_id)
        try:
            prior_ids = json.loads(last_assistant["scene_ids_json"])
        except (TypeError, ValueError):
            prior_ids = []
        if not prior_ids:
            return handle_compilation(prompt, history, project_scope, conversation_id)
        ref_id = int(prior_ids[0])

    import vectordb

    find_similar = getattr(vectordb, "find_similar", None)
    if find_similar is None:
        return handle_compilation(prompt, history, project_scope, conversation_id)

    similar = find_similar(ref_id, n_results=20, project_scope=project_scope)
    summary = llm_chat(
        "跟 scene {0} 相似的 {1} 個 scene。一句繁中說明。".format(ref_id, len(similar)),
        model=ARKIV_CHAT_MODEL,
        conversation=history,
    )
    return {
        "assistant_text": summary["text"],
        "scene_ids": [r.get("media_id") for r in similar if r.get("media_id")],
        "intent": "similarity",
        "tokens_used": summary.get("tokens_used", 0),
        "stage": "done",
        "latency_ms": summary.get("latency_ms", 0),
    }


def handle_analytics(
    prompt: str,
    history: List[Dict[str, Any]],
    project_scope: Optional[List[str]],
    conversation_id: str,
) -> Dict[str, Any]:
    del project_scope, conversation_id
    analytics_prompt = (
        "從用戶問題抓出統計參數，回 JSON：\n"
        "{{\"metric\": \"count\" | \"duration_sum\" | \"by_month\", "
        "\"time_range\": {{\"start\": \"YYYY-MM\" or null, \"end\": \"YYYY-MM\" or null}}}}\n"
        "用戶問：{0}"
    ).format(prompt)
    params_result = llm_chat(
        analytics_prompt,
        model=ARKIV_INTENT_MODEL,
        conversation=history,
        json_mode=True,
    )
    try:
        params = json.loads(params_result["text"])
    except (TypeError, ValueError):
        params = {"metric": "count", "time_range": {"start": None, "end": None}}
    if not isinstance(params, dict):
        params = {"metric": "count", "time_range": {"start": None, "end": None}}
    if not isinstance(params.get("time_range"), dict):
        params["time_range"] = {"start": None, "end": None}

    where = ""
    args = []
    if params["time_range"].get("start"):
        where += " AND processed_at >= ?"
        args.append(params["time_range"]["start"] + "-01")
    if params["time_range"].get("end"):
        where += " AND processed_at < ?"
        args.append(params["time_range"]["end"] + "-32")

    metric = params.get("metric", "count")
    with get_conn() as conn:
        if metric == "duration_sum":
            row = conn.execute(
                "SELECT COALESCE(SUM(duration_s), 0) AS s FROM media WHERE 1=1 {0}".format(where),
                args,
            ).fetchone()
            stat = "總時長 {0:.1f} 小時".format((row["s"] or 0) / 3600)
        elif metric == "by_month":
            rows = conn.execute(
                "SELECT substr(processed_at, 1, 7) AS month, COUNT(*) AS n "
                "FROM media WHERE 1=1 {0} GROUP BY month ORDER BY month".format(where),
                args,
            ).fetchall()
            stat = "\n".join("- {0}: {1}".format(r["month"], r["n"]) for r in rows)
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM media WHERE 1=1 {0}".format(where),
                args,
            ).fetchone()
            stat = "找到 {0} 個 media".format(row["n"])

    summary = llm_chat(
        "統計結果：\n{0}\n用一段繁中描述。".format(stat),
        model=ARKIV_CHAT_MODEL,
        conversation=history,
    )
    return {
        "assistant_text": summary["text"],
        "scene_ids": [],
        "intent": "analytics",
        "tokens_used": params_result.get("tokens_used", 0) + summary.get("tokens_used", 0),
        "stage": "done",
        "latency_ms": params_result.get("latency_ms", 0) + summary.get("latency_ms", 0),
    }


def handle_general(
    prompt: str,
    history: List[Dict[str, Any]],
    project_scope: Optional[List[str]],
    conversation_id: str,
) -> Dict[str, Any]:
    del project_scope, conversation_id
    system = (
        "你是 arkiv video archive 助手。用戶問題不需要查 video 庫，請直接回答。"
        "如果用戶其實想找 scene，提示他換問法（例：『給我所有黃昏鏡頭』）。"
    )
    result = llm_chat(prompt, model=ARKIV_CHAT_MODEL, system=system, conversation=history)
    return {
        "assistant_text": result["text"],
        "scene_ids": [],
        "intent": "general",
        "tokens_used": result.get("tokens_used", 0),
        "stage": "done",
        "latency_ms": result.get("latency_ms", 0),
    }


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
    prompt = _trim_prompt(prompt)
    history = load_history(conversation_id, limit=10)
    try:
        intent_result = classify_intent(prompt, history)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
        return {
            "assistant_text": "LLM 服務暫時無回應（{0}），請稍後再試。".format(
                type(exc).__name__
            ),
            "scene_ids": [],
            "tokens_used": 0,
            "stage": "error",
            "intent": "general",
            "latency_ms": 0,
        }
    except requests.exceptions.HTTPError:
        return {
            "assistant_text": (
                "LLM 請求被拒，通常是模型未安裝。請在 Ollama 主機執行："
                "ollama pull {0}".format(ARKIV_INTENT_MODEL)
            ),
            "scene_ids": [],
            "tokens_used": 0,
            "stage": "error",
            "intent": "general",
            "latency_ms": 0,
        }

    intent = intent_result.get("intent")
    if intent not in KNOWN_INTENTS:
        intent = "general"
    handler = HANDLERS.get(intent, handle_general)
    try:
        result = handler(prompt, history, project_scope, conversation_id)
    except EmbeddingDimensionMismatch:
        return {
            "assistant_text": (
                "向量索引與目前的 embedding 模型不相容，請在 arkiv 主機執行："
                "python embed.py --rebuild"
            ),
            "scene_ids": [],
            "tokens_used": intent_result.get("tokens_used", 0),
            "stage": "error",
            "intent": intent,
            "latency_ms": intent_result.get("latency_ms", 0),
        }
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
        return {
            "assistant_text": "處理時 LLM 失聯（{0}），已記錄 intent={1}。".format(
                type(exc).__name__,
                intent,
            ),
            "scene_ids": [],
            "tokens_used": intent_result.get("tokens_used", 0),
            "stage": "error",
            "intent": intent,
            "latency_ms": intent_result.get("latency_ms", 0),
        }
    except requests.exceptions.HTTPError:
        return {
            "assistant_text": (
                "LLM 請求被拒，通常是模型未安裝。請在 Ollama 主機執行："
                "ollama pull {0}".format(ARKIV_CHAT_MODEL)
            ),
            "scene_ids": [],
            "tokens_used": intent_result.get("tokens_used", 0),
            "stage": "error",
            "intent": intent,
            "latency_ms": intent_result.get("latency_ms", 0),
        }
    result["intent"] = result.get("intent", intent)
    return result


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
