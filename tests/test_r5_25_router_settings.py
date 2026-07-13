"""R5-25 (round-5 #51): settings routes peeled to routers/settings.py.

Second router peeled. Pins the split: the router is a leaf, owns the 3 settings
routes + its scope helper + body model, server.py no longer defines them, and the
routes stay mounted + auth-guarded. Behavioural coverage lives in
test_settings_g5.py.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_settings_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "settings.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_settings_routes_and_helpers():
    import routers.settings as rs
    pairs = {
        (r.path, m)
        for r in rs.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/settings", "GET"),
        ("/api/settings", "PUT"),
        ("/api/settings/{key}", "DELETE"),
    }
    assert hasattr(rs, "SettingsUpdate")            # body model moved with it
    assert hasattr(rs, "_resolve_settings_scope")   # scope helper moved with it


def test_server_no_longer_defines_settings_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "class SettingsUpdate" not in src
    assert "def _resolve_settings_scope" not in src
    assert not re.search(r"@app\.(get|put|delete)\(\"/api/settings", src)
    assert "include_router(settings_router)" in src


def test_settings_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.get("/api/settings").status_code == 401
        assert c.put("/api/settings", json={"scope": "global", "values": {}}).status_code == 401
        assert c.delete("/api/settings/x").status_code == 401
        assert c.get("/api/settings_nonexistent").status_code == 404
