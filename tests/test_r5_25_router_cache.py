"""R5-25 (round-5 #51): cache routes peeled to routers/cache.py.

Fifth router peeled — the last clean one before the shared-helper blockers. Pins
the split: leaf module, owns the 2 cache routes + the size helper, server.py no
longer defines them, routes mounted + auth-guarded, and the router's code-root
matches server.ROOT (so waveforms/__pycache__ resolve to the same dir). Cache
behaviour (thumbnails-preserving 'app' clear, chromadb rebuild guard, client-cache
invalidation) is covered by test_hardening_round4.py.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_cache_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "cache.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_cache_routes_and_helper():
    import routers.cache as rc
    pairs = {
        (r.path, m)
        for r in rc.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/cache/info", "GET"),
        ("/api/cache/clear", "POST"),
    }
    assert hasattr(rc, "_dir_size_mb")


def test_router_code_root_matches_server_root():
    # waveforms/__pycache__ must resolve to the same dir server.ROOT points at,
    # or /api/cache/{info,clear} would silently target the wrong (empty) dir.
    import server
    import routers.cache as rc
    assert rc._CODE_ROOT == server.ROOT


def test_shares_embed_guard_singleton_with_server():
    # the chromadb-clear rebuild guard must be the SAME single-flight object the
    # embed-rebuild route acquires, or the 409 guard is defeated.
    import server
    import routers.cache as rc
    assert rc._embed_guard is server._embed_guard


def test_server_no_longer_defines_cache_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "def _dir_size_mb" not in src
    assert not re.search(r"@app\.(get|post)\(\"/api/cache", src)
    assert "include_router(cache_router)" in src


def test_cache_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.get("/api/cache/info").status_code == 401
        assert c.post("/api/cache/clear").status_code == 401
        assert c.get("/api/cache_nonexistent").status_code == 404
