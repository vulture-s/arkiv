"""R5-25 (round-5 #51): search routes peeled to routers/search.py.

Fifteenth router peeled. Pins the split: the leaf module owns /api/search/all
(federated) + /api/search/query (structured) with StructuredQuery /
_structured_sort_key / _split_csv, server.py no longer defines them, routes mounted
+ auth-guarded (401-not-404). The two share the mediarecords bulk-fetch helpers
(same instance) with the media group. Federation path-sanitisation + structured
query behaviour are covered by test_server.py / test_federation.py.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_search_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "search.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_search_routes_and_helpers():
    import routers.search as rs
    pairs = {
        (r.path, m)
        for r in rs.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/search/all", "GET"),
        ("/api/search/query", "POST"),
    }
    for name in ("search_all", "structured_query", "StructuredQuery",
                 "_structured_sort_key", "_split_csv"):
        assert hasattr(rs, name)


def test_split_csv_semantics():
    import routers.search as rs
    assert rs._split_csv(None) is None
    assert rs._split_csv("") is None
    assert rs._split_csv(" , ,") is None          # only blanks → None
    assert rs._split_csv("a, b ,c") == ["a", "b", "c"]


def test_structured_sort_key_directions():
    import routers.search as rs
    _, rev = rs._structured_sort_key("duration")
    assert rev is True
    keyfn, rev = rs._structured_sort_key("name")
    assert rev is False
    assert keyfn({"filename": "ABC.mp4"}) == "abc.mp4"


def test_router_shares_mediarecords_helpers_by_identity():
    import routers.search as rs
    import mediarecords
    assert rs._get_light_records_by_ids is mediarecords._get_light_records_by_ids
    assert rs._get_tags_bulk is mediarecords._get_tags_bulk


def test_server_no_longer_defines_search_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "def search_all" not in src
    assert "def structured_query" not in src
    assert "class StructuredQuery" not in src
    assert not re.search(r"@app\.(get|post)\(\"/api/search", src)
    assert "include_router(search_router)" in src


def test_search_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.get("/api/search/all", params={"q": "x"}).status_code == 401
        assert c.post("/api/search/query", json={"conditions": []}).status_code == 401
        assert c.get("/api/search/nope-zz").status_code == 404
