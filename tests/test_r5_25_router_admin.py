"""R5-25 (round-5 #51): first APIRouter peeled from server.py → routers/admin.py.

The admin token routes are thin wrappers over the already-extracted `admin`
business-logic module, so they were the cleanest first peel. These tests pin the
split itself: the router module owns the routes + its body model, server.py no
longer defines the handlers, and the routes are still mounted + auth-guarded on
the app. Full authenticated CRUD behaviour is covered by test_auth.py.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_admin_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "admin.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_the_four_token_routes():
    import routers.admin as ra
    # one (path, method) pair per handler, ignoring the auto-added HEAD on GET
    pairs = {
        (r.path, m)
        for r in ra.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/admin/tokens", "POST"),
        ("/api/admin/tokens", "GET"),
        ("/api/admin/tokens/{token_id}", "GET"),
        ("/api/admin/tokens/{token_id}", "DELETE"),
    }
    # the body model moved WITH the router
    assert hasattr(ra, "CreateTokenRequest")


def test_server_no_longer_defines_the_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    # no @app-decorated admin handler and no CreateTokenRequest class left behind
    assert "class CreateTokenRequest" not in src
    assert not re.search(r"@app\.(post|get|delete)\(\"/api/admin/tokens", src)
    # and it IS mounted
    assert "include_router(admin_router)" in src


def test_admin_routes_mounted_and_auth_guarded(server_module):
    # 401 (auth dependency fires) proves the route is MOUNTED; a missing route
    # would 404. Contrast with a genuinely nonexistent path.
    with TestClient(server_module.app) as c:
        assert c.post("/api/admin/tokens", json={"name": "x", "scopes": ["admin"]}).status_code == 401
        assert c.delete("/api/admin/tokens/abc").status_code == 401
        assert c.post("/api/zzz_nonexistent_route", json={}).status_code == 404
