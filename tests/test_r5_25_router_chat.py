"""R5-25 (round-5 #51): chat routes peeled to routers/chat.py.

Fourth router peeled. Pins the split: leaf module, owns the 3 chat routes +
the ownership filter + the request/response models (incl. the prompt validator),
server.py no longer defines them, routes mounted + auth-guarded. Behaviour +
ownership enforcement covered by the chat tests in test_server.py.
"""
import pathlib
import re

import pytest
from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_chat_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "chat.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_chat_routes_and_helpers():
    import routers.chat as rc
    pairs = {
        (r.path, m)
        for r in rc.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/chat", "POST"),
        ("/api/chat/history/{conv_id}", "GET"),
        ("/api/chat/conversations", "GET"),
    }
    for name in ("ChatRequest", "ChatResponse", "_chat_owner_filter"):
        assert hasattr(rc, name)


def test_chat_prompt_validator_and_owner_filter():
    import routers.chat as rc
    with pytest.raises(Exception):
        rc.ChatRequest(prompt="   ")            # empty-after-strip rejected
    assert rc.ChatRequest(prompt="hi").prompt == "hi"
    # loopback / admin → no ownership restriction; a plain token → restricted
    assert rc._chat_owner_filter({"id": "loopback"}) == ("", ())
    assert rc._chat_owner_filter({"scopes": ["admin"]}) == ("", ())
    sql, params = rc._chat_owner_filter({"id": "tok9", "scopes": ["chat_read"]})
    assert "user_token_id" in sql and params == ("tok9",)


def test_server_no_longer_defines_chat_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "class ChatRequest" not in src
    assert "def _chat_owner_filter" not in src
    assert not re.search(r"@app\.(get|post)\(\"/api/chat", src)
    assert "include_router(chat_router)" in src
    # _split_csv (used by search) must NOT have been dragged into the chat router;
    # it later moved to routers/search.py with /api/search/all (R5-25 search peel).
    import routers.search
    assert hasattr(routers.search, "_split_csv")
    assert "def _split_csv" not in src


def test_chat_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.post("/api/chat", json={"prompt": "hi"}).status_code == 401
        assert c.get("/api/chat/history/x").status_code == 401
        assert c.get("/api/chat/conversations").status_code == 401
        assert c.get("/api/chat_nonexistent").status_code == 404
