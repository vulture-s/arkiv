"""R5-25 (round-5 #51): projects registry routes peeled to routers/projects.py.

Third router peeled. Pins the split: leaf module, owns the 5 project routes + the
path sanitisers + the ProjectCreate model (incl. its name validator), server.py
no longer defines them, routes mounted + auth-guarded. Behaviour covered by
test_hardening_projpath.py / project registry tests.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_projects_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "projects.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_projects_routes_and_helpers():
    import routers.projects as rp
    pairs = {
        (r.path, m)
        for r in rp.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/projects", "GET"),
        ("/api/projects", "POST"),
        ("/api/projects/{name}", "DELETE"),
        ("/api/projects/sync", "POST"),
        ("/api/projects/health", "GET"),
    }
    assert hasattr(rp, "ProjectCreate")
    assert hasattr(rp, "_project_paths_visible")
    assert hasattr(rp, "_sanitize_project_paths")


def test_project_name_validator_still_enforced():
    # the #22/M21 name validator must travel WITH the model
    import routers.projects as rp
    import pytest
    with pytest.raises(Exception):
        rp.ProjectCreate(name="a/b", path="/x")   # separator rejected
    with pytest.raises(Exception):
        rp.ProjectCreate(name="   ", path="/x")    # empty-after-clean rejected
    assert rp.ProjectCreate(name="  ok name ", path="/x").name == "ok name"  # trimmed


def test_server_no_longer_defines_projects_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "class ProjectCreate" not in src
    assert "def _sanitize_project_paths" not in src
    assert not re.search(r"@app\.(get|post|delete)\(\"/api/projects", src)
    assert "include_router(projects_router)" in src


def test_projects_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.get("/api/projects").status_code == 401
        assert c.post("/api/projects", json={"name": "x", "path": "/y"}).status_code == 401
        assert c.get("/api/projects/health").status_code == 401
        assert c.get("/api/projects_nonexistent").status_code == 404
