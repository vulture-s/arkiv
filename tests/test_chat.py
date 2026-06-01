import json

import pytest
import requests
from fastapi.testclient import TestClient
from unittest.mock import patch


@pytest.fixture
def fastapi_client_with_readonly_token(server_module):
    import admin

    token = admin.create_token(name="pytest-readonly", scopes=["videos_read"])
    headers = {"Authorization": "Bearer {0}".format(token["raw_token"])}
    with TestClient(server_module.app, headers=headers) as client:
        yield client


def _mock_chat_path(mock_cls, mock_chat, mock_search):
    mock_cls.return_value = {
        "intent": "compilation",
        "search_params": {"query": "黃昏"},
        "limit": 5,
        "tokens_used": 100,
        "latency_ms": 50,
    }
    mock_chat.return_value = {
        "text": "找到 3 個黃昏鏡頭",
        "tokens_used": 200,
        "latency_ms": 100,
        "provider": "ollama",
        "model": "qwen2.5:14b",
    }
    mock_search.return_value = [{"media_id": 1}, {"media_id": 2}, {"media_id": 3}]


def _intent(name):
    return {
        "intent": name,
        "search_params": {"query": name},
        "limit": 5,
        "tokens_used": 10,
        "latency_ms": 5,
    }


def _seed_media(db, sample_record, count=3):
    ids = []
    for idx in range(count):
        db.upsert(
            sample_record(
                path="/tmp/chat_{0}.mp4".format(idx),
                filename="chat_{0}.mp4".format(idx),
                duration_s=3600 + idx,
                processed_at="2026-05-0{0}T00:00:00".format(idx + 1),
            )
        )
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id FROM media ORDER BY id").fetchall()
    for row in rows:
        ids.append(row["id"])
    return ids


def test_chat_create_conversation_returns_conv_id(fastapi_client):
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("chat.vector_search") as mock_search:
        _mock_chat_path(mock_cls, mock_chat, mock_search)
        response = fastapi_client.post("/api/chat", json={"prompt": "給我黃昏鏡頭"})

    assert response.status_code == 200
    data = response.json()
    assert data["conversation_id"]
    assert data["intent"] == "compilation"
    assert data["assistant_text"] == "找到 3 個黃昏鏡頭"
    assert data["scene_ids"] == [1, 2, 3]
    assert data["tokens_used"] == 300
    assert data["latency_ms"] == 150


def test_chat_continues_existing_conversation(fastapi_client):
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("chat.vector_search") as mock_search:
        _mock_chat_path(mock_cls, mock_chat, mock_search)
        first = fastapi_client.post("/api/chat", json={"prompt": "第一條"})
        conv_id = first.json()["conversation_id"]
        second = fastapi_client.post(
            "/api/chat",
            json={"prompt": "第二條", "conversation_id": conv_id},
        )

    assert second.status_code == 200
    assert second.json()["conversation_id"] == conv_id

    import db

    with db.get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM chat_messages WHERE conversation_id = ?",
            (conv_id,),
        ).fetchone()["c"]
    assert count == 4


def test_chat_requires_chat_write_scope(fastapi_client_with_readonly_token):
    response = fastapi_client_with_readonly_token.post("/api/chat", json={"prompt": "x"})
    assert response.status_code == 403


def test_chat_invalid_conversation_id_returns_400(fastapi_client):
    response = fastapi_client.post(
        "/api/chat",
        json={"prompt": "x", "conversation_id": "missing-conv"},
    )
    assert response.status_code == 400


def test_chat_refinement_filters_prior_results(fastapi_client, tmp_db, sample_record):
    import db

    ids = _seed_media(db, sample_record, count=3)
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat:
        mock_cls.return_value = _intent("compilation")
        mock_chat.return_value = {
            "text": json.dumps({"filtered_ids": ids[:2], "reason": "只保留符合條件的鏡頭"}),
            "tokens_used": 33,
            "latency_ms": 12,
        }
        import chat

        conv_id = chat.create_conversation(None, "先建立對話")
        chat.persist_message(
            conv_id,
            role="assistant",
            content="prior",
            intent="compilation",
            scene_ids=ids,
            tokens_used=1,
            stage="done",
            latency_ms=1,
        )
        mock_cls.return_value = _intent("refinement")
        response = fastapi_client.post(
            "/api/chat",
            json={"prompt": "只要室內的", "conversation_id": conv_id},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "refinement"
    assert data["scene_ids"] == ids[:2]
    assert "從 3 個 refine 到 2 個" in data["assistant_text"]


def test_chat_refinement_without_prior_results_falls_back_to_compilation(fastapi_client):
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("chat.vector_search") as mock_search:
        mock_cls.side_effect = [_intent("refinement"), _intent("compilation")]
        mock_chat.return_value = {"text": "fallback result", "tokens_used": 20, "latency_ms": 8}
        mock_search.return_value = [{"media_id": 7}]
        response = fastapi_client.post("/api/chat", json={"prompt": "只要室內的"})

    assert response.status_code == 200
    assert response.json()["intent"] == "compilation"
    assert response.json()["scene_ids"] == [7]
    mock_search.assert_called_once()


def test_chat_similarity_uses_reference_id(fastapi_client):
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("vectordb.find_similar", create=True) as mock_similar:
        mock_cls.return_value = _intent("similarity")
        mock_similar.return_value = [{"media_id": 11}, {"media_id": 12}]
        mock_chat.return_value = {"text": "找到相似鏡頭", "tokens_used": 25, "latency_ms": 9}
        response = fastapi_client.post(
            "/api/chat",
            json={"prompt": "跟 scene 42 像的", "project_scope": ["projectA"]},
        )

    assert response.status_code == 200
    assert response.json()["intent"] == "similarity"
    assert response.json()["scene_ids"] == [11, 12]
    mock_similar.assert_called_once_with(42, n_results=20, project_scope=["projectA"])


def test_chat_analytics_count_intent(fastapi_client, tmp_db, sample_record):
    import db

    _seed_media(db, sample_record, count=2)
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat:
        mock_cls.return_value = _intent("analytics")
        mock_chat.side_effect = [
            {
                "text": json.dumps({"metric": "count", "time_range": {"start": None, "end": None}}),
                "tokens_used": 15,
                "latency_ms": 6,
            },
            {"text": "目前共有 2 個素材", "tokens_used": 18, "latency_ms": 7},
        ]
        response = fastapi_client.post("/api/chat", json={"prompt": "目前有幾個素材？"})

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "analytics"
    assert data["scene_ids"] == []
    assert data["assistant_text"] == "目前共有 2 個素材"
    assert "找到 2 個 media" in mock_chat.call_args_list[1].args[0]


def test_chat_general_intent_no_vector_search(fastapi_client):
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("chat.vector_search") as mock_search:
        mock_cls.return_value = _intent("general")
        mock_chat.return_value = {"text": "可以，我會用繁中回答。", "tokens_used": 12, "latency_ms": 4}
        response = fastapi_client.post("/api/chat", json={"prompt": "你好"})

    assert response.status_code == 200
    assert response.json()["intent"] == "general"
    assert response.json()["scene_ids"] == []
    mock_search.assert_not_called()


def test_chat_history_endpoint_returns_messages(fastapi_client):
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("chat.vector_search") as mock_search:
        _mock_chat_path(mock_cls, mock_chat, mock_search)
        created = fastapi_client.post("/api/chat", json={"prompt": "給我黃昏鏡頭"})
        conv_id = created.json()["conversation_id"]
        response = fastapi_client.get("/api/chat/history/{0}".format(conv_id))

    assert response.status_code == 200
    data = response.json()
    assert data["conversation"]["id"] == conv_id
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    assert data["messages"][1]["scene_ids_json"] == "[1, 2, 3]"


def test_chat_history_404_for_missing_conv(fastapi_client):
    response = fastapi_client.get("/api/chat/history/nonexistent_id")
    assert response.status_code == 404


def test_chat_conversations_list_endpoint(fastapi_client):
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("chat.vector_search") as mock_search:
        _mock_chat_path(mock_cls, mock_chat, mock_search)
        first = fastapi_client.post("/api/chat", json={"prompt": "第一個對話"}).json()["conversation_id"]
        second = fastapi_client.post("/api/chat", json={"prompt": "第二個對話"}).json()["conversation_id"]
        response = fastapi_client.get("/api/chat/conversations")

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["conversations"]]
    assert first in ids
    assert second in ids


def test_chat_handles_ollama_timeout(fastapi_client):
    with patch("chat.classify_intent", side_effect=requests.exceptions.Timeout):
        response = fastapi_client.post("/api/chat", json={"prompt": "test"})

    assert response.status_code == 200
    data = response.json()
    assert "暫時無回應" in data["assistant_text"]
    assert data["intent"] == "general"
    assert data["scene_ids"] == []


def test_chat_trims_oversize_prompt(fastapi_client):
    long_prompt = "x" * 10000
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("chat.vector_search") as mock_search:
        mock_cls.return_value = _intent("general")
        mock_chat.return_value = {"text": "ok", "tokens_used": 10, "latency_ms": 2}
        response = fastapi_client.post("/api/chat", json={"prompt": long_prompt})

    assert response.status_code == 200
    called_prompt = mock_cls.call_args.args[0]
    assert len(called_prompt) <= 4000
    assert "prompt 過長已截斷" in called_prompt
    mock_search.assert_not_called()


def test_chat_classifier_fallback_on_invalid_intent(fastapi_client):
    with patch("chat.llm_chat") as mock_chat, patch("chat.vector_search") as mock_search:
        mock_chat.return_value = {
            "text": '{"intent": "bogus_intent", "search_params": {"query": "x"}, "limit": 999}',
            "tokens_used": 50,
            "latency_ms": 10,
        }
        response = fastapi_client.post("/api/chat", json={"prompt": "test"})

    assert response.status_code == 200
    assert response.json()["intent"] == "general"
    mock_search.assert_not_called()


def test_chat_project_scope_passes_to_vectordb(fastapi_client):
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("chat.vector_search") as mock_search:
        mock_cls.return_value = _intent("compilation")
        mock_chat.return_value = {"text": "scoped result", "tokens_used": 20, "latency_ms": 8}
        mock_search.return_value = [{"media_id": 7}]
        response = fastapi_client.post(
            "/api/chat",
            json={"prompt": "test", "project_scope": ["projectA"]},
        )

    assert response.status_code == 200
    assert response.json()["scene_ids"] == [7]
    assert mock_search.call_args.kwargs.get("project_scope") == ["projectA"]


def test_chat_full_flow_compilation_to_refinement(fastapi_client, tmp_db, sample_record):
    import db

    ids = _seed_media(db, sample_record, count=3)
    with patch("chat.classify_intent") as mock_cls, patch("chat.llm_chat") as mock_chat, patch("chat.vector_search") as mock_search:
        mock_cls.side_effect = [
            _intent("compilation"),
            _intent("compilation"),
            _intent("refinement"),
        ]
        mock_search.return_value = [{"media_id": ids[0]}, {"media_id": ids[1]}, {"media_id": ids[2]}]
        mock_chat.side_effect = [
            {"text": "先找到 3 個鏡頭", "tokens_used": 20, "latency_ms": 8},
            {
                "text": json.dumps({"filtered_ids": ids[:1], "reason": "只保留第一個"}),
                "tokens_used": 30,
                "latency_ms": 9,
            },
        ]

        first = fastapi_client.post("/api/chat", json={"prompt": "給我訪談鏡頭"})
        conv_id = first.json()["conversation_id"]
        second = fastapi_client.post(
            "/api/chat",
            json={"prompt": "只要第一個", "conversation_id": conv_id},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["scene_ids"] == ids
    assert second.json()["intent"] == "refinement"
    assert second.json()["scene_ids"] == ids[:1]


def test_chat_conversation_isolation_between_tokens(server_module):
    """A chat token must not read another token's conversation history/list —
    the ownership column was recorded but never enforced on read (IDOR)."""
    import admin, auth
    from fastapi.testclient import TestClient
    from unittest.mock import patch

    def _tok(name):
        t = admin.create_token(name=name, scopes=["chat_write", "chat_read"])
        return t["raw_token"]
    raw_a, raw_b = _tok("user-a"), _tok("user-b")

    with TestClient(server_module.app) as client:
        with patch("chat.classify_intent") as cls, patch("chat.llm_chat") as lc, patch("chat.vector_search"):
            cls.return_value = _intent("general")
            lc.return_value = {"text": "ok", "tokens_used": 1, "latency_ms": 1}
            r = client.post("/api/chat", json={"prompt": "A's secret"},
                            headers={"Authorization": f"Bearer {raw_a}"})
            assert r.status_code == 200
            conv_a = r.json()["conversation_id"]

        # B must NOT see A's conversation in the list...
        lst = client.get("/api/chat/conversations", headers={"Authorization": f"Bearer {raw_b}"})
        assert lst.status_code == 200
        assert all(c["id"] != conv_a for c in lst.json()["conversations"])
        # ...nor read its history (404, not the content)
        hist = client.get(f"/api/chat/history/{conv_a}", headers={"Authorization": f"Bearer {raw_b}"})
        assert hist.status_code == 404
        # A can still read its own
        own = client.get(f"/api/chat/history/{conv_a}", headers={"Authorization": f"Bearer {raw_a}"})
        assert own.status_code == 200
