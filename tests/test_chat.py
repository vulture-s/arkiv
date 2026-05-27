import pytest
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
