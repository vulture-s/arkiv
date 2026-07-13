"""R5-25 (round-5 #51): proxy-management routes peeled to routers/proxy.py.

Tenth router peeled. Pins the split: the leaf module owns the 3 proxy routes +
the _build_proxies / _build_proxies_all workers, server.py no longer defines them,
routes mounted + auth-guarded (401-not-404). The R5-22 (#59) single-flight guard
stays the ONE shared state.proxy_build instance (covered by test_r5_22_singleflight);
_proxy_ready moved to pathres.py (still used by /api/stream), pinned there by
test_r5_25_pathres_module.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_proxy_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "proxy.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_proxy_routes_and_workers():
    import routers.proxy as rp
    pairs = {
        (r.path, m)
        for r in rp.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/proxy/status", "GET"),
        ("/api/proxy/build", "POST"),
        ("/api/proxy/build/{media_id}", "POST"),
    }
    for name in ("proxy_status", "proxy_build", "proxy_build_one",
                 "_build_proxies", "_build_proxies_all"):
        assert hasattr(rp, name)


def test_router_shares_the_state_proxy_guard():
    # the whole-library build must serialise on the ONE shared guard, never a copy
    import routers.proxy as rp
    import state
    assert rp._proxy_guard is state.proxy_build


def test_proxy_ready_moved_to_pathres_not_defined_here():
    src = (_ROOT / "routers" / "proxy.py").read_text(encoding="utf-8")
    assert "def _proxy_ready" not in src   # it lives in pathres now, imported
    import routers.proxy as rp
    import pathres
    assert rp._proxy_ready is pathres._proxy_ready


def test_server_no_longer_defines_proxy_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "def proxy_status" not in src
    assert "def _build_proxies" not in src
    assert not re.search(r"@app\.(get|post)\(\"/api/proxy", src)
    assert "include_router(proxy_router)" in src
    # /api/stream stays in server.py and still uses the (re-exported) _proxy_ready
    assert "def stream_media" in src


def test_proxy_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.get("/api/proxy/status").status_code == 401
        assert c.post("/api/proxy/build").status_code == 401
        assert c.post("/api/proxy/build/1").status_code == 401
        assert c.get("/api/proxy/statuszz").status_code == 404
