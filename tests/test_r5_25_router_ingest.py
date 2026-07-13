"""R5-25 (round-5 #51): ingest + WS-progress routes peeled to routers/ingest.py.

The final router peel. Pins the split: the leaf module owns the /api/ingest family
(engines/scan/run/reingest) + the WS progress channel (/ws/ingest + /api/ingest/ws)
+ their models/helpers; the shared single-flight slot + broadcaster stay ONE
instance imported from state.py (the audit-H3 double-whisper-OOM guard); server.py
no longer defines any of it and mounts the router. WS-auth semantics are covered by
test_auth.py (test_ws_ingest_*); ingest-option flag mapping by test_ingest_options.py.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_ingest_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "ingest.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_exactly_the_ingest_routes():
    import routers.ingest as ri
    pairs = set()
    for r in ri.router.routes:
        path = getattr(r, "path", None)
        if path is None:
            continue
        methods = getattr(r, "methods", None)
        if methods:  # HTTP route
            for m in methods:
                if m != "HEAD":
                    pairs.add((path, m))
        else:  # websocket route — no .methods
            pairs.add((path, "WS"))
    assert pairs == {
        ("/api/ingest/engines", "GET"),
        ("/api/ingest/scan", "POST"),
        ("/api/ingest", "POST"),
        ("/api/media/{media_id}/reingest", "POST"),
        ("/ws/ingest", "WS"),
        ("/api/ingest/ws", "POST"),
    }


def test_ingest_slot_and_broadcaster_are_the_shared_state_instances():
    """The H3 guard: REST/reingest/WS ingest all serialize through the SAME slot +
    broadcaster in state.py. The router must import them, never fork a copy."""
    import routers.ingest as ri
    import state

    assert ri._acquire_ingest_slot is state._acquire_ingest_slot
    assert ri._release_ingest_slot is state._release_ingest_slot
    assert ri.ingest_ws is state.ingest_ws


def test_origin_allowlist_is_the_shared_webguard_instance():
    import routers.ingest as ri
    import webguard

    assert ri._ALLOWED_ORIGINS is webguard._ALLOWED_ORIGINS


def test_server_no_longer_defines_ingest_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "def ingest_media" not in src
    assert "def _ws_authorized" not in src
    assert "@app.websocket" not in src
    assert "include_router(ingest_router)" in src


def test_ingest_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        # 401 (not 404) proves the routes are mounted + scope-gated
        assert c.post("/api/ingest", json={"path": "/x"}).status_code == 401
        assert c.post("/api/ingest/scan", json={"path": "/x"}).status_code == 401
        assert c.get("/api/ingest/engines").status_code == 401
        assert c.post("/api/media/1/reingest").status_code == 401
        # a bogus sibling path is a real 404
        assert c.post("/api/ingestzz", json={}).status_code == 404
