"""R5-25 (round-5 #51): analytics routes peeled to routers/analytics.py.

Eighth router peeled. Pins the split: the leaf module owns the 5 dashboard
aggregate routes + its two group-local helpers (_current_project_registry_name,
_thumb_url), server.py no longer defines them, routes mounted + auth-guarded
(401-not-404). The /api/stats registry-name behaviour is covered end-to-end by
test_bins.py::test_stats_reports_current_project_registry_name.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_analytics_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "analytics.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_analytics_routes_and_helpers():
    import routers.analytics as ra
    pairs = {
        (r.path, m)
        for r in ra.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/stats", "GET"),
        ("/api/tags", "GET"),
        ("/api/collections", "GET"),
        ("/api/duration-by-lang", "GET"),
        ("/api/size-by-ext", "GET"),
    }
    for name in ("_current_project_registry_name", "_thumb_url",
                 "get_stats", "get_all_tags", "list_collections"):
        assert hasattr(ra, name)


def test_thumb_url_helper_semantics():
    import routers.analytics as ra
    assert ra._thumb_url(None) is None
    assert ra._thumb_url("") is None
    assert ra._thumb_url("/data/.arkiv/thumbnails/abc.jpg") == "/thumbnails/abc.jpg"
    # Windows-style separators collapse to the served basename too.
    assert ra._thumb_url("C:\\arkiv\\thumbnails\\x.png") == "/thumbnails/x.png"


def test_server_no_longer_defines_analytics_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert not re.search(r"@app\.get\(\"/api/(stats|tags|collections|duration-by-lang|size-by-ext)\"", src)
    assert "def _current_project_registry_name" not in src
    assert "def _thumb_url" not in src
    assert "include_router(analytics_router)" in src


def test_analytics_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        # each aggregate route is mounted (401 from the auth dep, not a 404 miss)
        for path in ("/api/stats", "/api/tags", "/api/collections",
                     "/api/duration-by-lang", "/api/size-by-ext"):
            assert c.get(path).status_code == 401, path
        assert c.get("/api/statszz").status_code == 404


# ── manual-tag collections end-to-end (list_collections joins the tags table) ─
def _seed_clip(db, name):
    db.upsert({
        "path": "/tmp/{0}".format(name), "filename": name, "ext": ".mp4",
        "duration_s": 30.0, "size_mb": 5.0, "width": 1920, "height": 1080,
        "fps": 30.0, "has_audio": 1, "transcript": "", "lang": "zh",
        "frame_tags": "", "thumbnail_path": "",
        "processed_at": "2026-05-01T09:00:00",
    })


def test_collections_surfaces_manual_tag_collection(fastapi_client):
    import importlib
    db = importlib.import_module("db")
    _seed_clip(db, "a.mp4")
    db.add_tag(1, "a-roll", "manual")
    r = fastapi_client.get("/api/collections")
    assert r.status_code == 200, r.text
    cols = {c["key"]: c for c in r.json()["collections"]}
    assert "a_roll" in cols, "manual a-roll tag should form the a_roll collection"
    assert any(it["id"] == 1 for it in cols["a_roll"]["items"])


def test_collections_ignore_auto_source_editorial_tag(fastapi_client):
    # An auto (vision) tag that happens to be named 'a-roll' must NOT enter an
    # editorial collection without the user's hand — list_collections filters
    # the tags table to source='manual' (Codex audit).
    import importlib
    db = importlib.import_module("db")
    _seed_clip(db, "b.mp4")
    db.add_tag(1, "a-roll", "auto")
    r = fastapi_client.get("/api/collections")
    assert r.status_code == 200, r.text
    cols = {c["key"]: c for c in r.json()["collections"]}
    assert "a_roll" not in cols or all(
        it["id"] != 1 for it in cols["a_roll"]["items"]
    ), "an auto-source 'a-roll' tag must not create editorial membership"
