"""R5-25 (round-5 #51): offload routes peeled to routers/offload.py.

Seventh router peeled. Pins the split: leaf module owns the 2 offload routes +
the /dit redirect + its 2 models + the per-source single-flight primitives,
server.py no longer defines them, routes mounted + auth-guarded. Offload lifecycle
(per-source 409, resume state) covered by test_r5_17_offload_lifecycle.py.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_offload_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "offload.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_offload_routes_and_slot():
    import routers.offload as ro
    pairs = {
        (r.path, m)
        for r in ro.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/offload/preview", "POST"),
        ("/api/offload", "POST"),
        ("/dit", "GET"),
    }
    for name in ("OffloadPreviewRequest", "OffloadRequest",
                 "_acquire_offload_slot", "_release_offload_slot", "_offload_active"):
        assert hasattr(ro, name)


def test_offload_slot_guard_semantics_local():
    import routers.offload as ro
    k = "/tmp/card"
    assert ro._acquire_offload_slot(k) is True
    assert ro._acquire_offload_slot(k) is False        # same key blocked
    assert ro._acquire_offload_slot(k + "2") is True   # different card ok
    ro._release_offload_slot(k)
    ro._release_offload_slot(k + "2")
    assert ro._acquire_offload_slot(k) is True          # released → reusable
    ro._release_offload_slot(k)


def test_server_no_longer_defines_offload_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "class OffloadRequest" not in src
    assert "def _acquire_offload_slot" not in src
    assert not re.search(r"@app\.(get|post)\(\"/api/offload", src)
    assert "include_router(offload_router)" in src


def test_offload_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.post("/api/offload/preview", json={"src": "/x"}).status_code == 401
        assert c.post("/api/offload", json={"src": "/x", "dst": ["/y"]}).status_code == 401
        # /dit is an unauth legacy redirect
        assert c.get("/dit", follow_redirects=False).status_code == 308
        assert c.post("/api/offloadzz", json={}).status_code == 404
