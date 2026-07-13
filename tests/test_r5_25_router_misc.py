"""R5-25 (round-5 #51): leftover singleton routes peeled to routers/misc.py.

Twelfth router peeled. Pins the split: the leaf module owns /api/stream,
/api/embed/rebuild, /api/open-file, /api/client-log (+ _log_safe + the two request
models), server.py no longer defines them, routes mounted + auth-guarded. The app
shell (SPA "/" + /thumbnails + lifespan) stays in server.py. Stream codec /
proxy-fallback behaviour is covered by test_server.py + test_v081_edges.py; the
embed single-flight by test_r5_22_singleflight.py; _log_safe by test_server.py.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_misc_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "misc.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_misc_routes_and_helpers():
    import routers.misc as rm
    pairs = {
        (r.path, m)
        for r in rm.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/stream/{media_id}", "GET"),
        ("/api/embed/rebuild", "POST"),
        ("/api/open-file", "POST"),
        ("/api/client-log", "POST"),
    }
    for name in ("stream_media", "embed_rebuild", "open_file", "client_log",
                 "_log_safe", "OpenFileRequest", "ClientLogRequest"):
        assert hasattr(rm, name)


def test_log_safe_strips_control_chars_and_truncates():
    import routers.misc as rm
    out = rm._log_safe("hello\nFAKE LOG\x1b[31m evil\r\n", 100)
    assert "\n" not in out and "\r" not in out and "\x1b" not in out
    assert "hello" in out and "evil" in out
    assert rm._log_safe("x" * 5000, 16) == "x" * 16
    assert rm._log_safe("音樂 OK", 100) == "音樂 OK"  # CJK preserved


def test_misc_router_shares_state_embed_guard():
    import routers.misc as rm
    import state
    assert rm._embed_guard is state.embed_rebuild
    assert rm._rebuild_embeddings is state._rebuild_embeddings


def test_server_no_longer_defines_misc_handlers_but_keeps_shell():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "def stream_media" not in src
    assert "def open_file" not in src
    assert "def client_log" not in src
    assert "def _log_safe" not in src
    assert not re.search(r"@app\.(get|post)\(\"/api/(stream|embed/rebuild|open-file|client-log)", src)
    assert "include_router(misc_router)" in src
    # the app shell stays in server.py
    assert "def serve_index" in src
    assert "def _load_index" in src


def test_misc_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.get("/api/stream/1").status_code in (401, 403)   # auth dep first
        assert c.post("/api/embed/rebuild").status_code == 401
        assert c.post("/api/open-file", json={"path": "/x"}).status_code == 401
        # client-log is deliberately unauthenticated (WebView diagnostics sink)
        assert c.post("/api/client-log", json={"level": "info", "msg": "hi"}).status_code == 200
        assert c.get("/api/streamzz/1").status_code == 404
