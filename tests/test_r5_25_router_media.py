"""R5-25 (round-5 #51): media routes peeled to routers/media.py.

The 14th router peeled — the clip-centric surface. Pins the split: the leaf module
owns exactly the 16 media routes (list + per-clip sub-resources), server.py no
longer defines them, the two specific routes (/api/media/pool + /api/media/
position/{media_id}) precede the dynamic /api/media/{media_id} so it can't shadow
them, and the shared bulk-fetch helpers are re-exported by identity from
mediarecords (so structured_query / search_all — which stay in server — keep the
SAME instance). Routes mounted + auth-guarded (401-not-404).
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_EXPECTED_ROUTES = {
    ("/api/media/position/{media_id}", "GET"),
    ("/api/media/pool", "GET"),
    ("/api/media", "GET"),
    ("/api/media/{media_id}", "GET"),
    ("/api/media/{media_id}/waveform", "GET"),
    ("/api/media/{media_id}/scenes", "GET"),
    ("/api/media/{media_id}/chapters", "GET"),
    ("/api/media/{media_id}/rating", "PATCH"),
    ("/api/media/{media_id}/tags", "GET"),
    ("/api/media/{media_id}/tags", "POST"),
    ("/api/media/{media_id}/tags/{tag_name}", "DELETE"),
    ("/api/media/{media_id}/remotion-props", "GET"),
    ("/api/media/{media_id}/retranscribe", "POST"),
    ("/api/media/{media_id}/transcripts", "GET"),
    ("/api/media/{media_id}/transcript/activate", "POST"),
    ("/api/media/{media_id}/retry-vision", "POST"),
}


def test_media_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "media.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_exactly_the_16_media_routes():
    import routers.media as rm
    pairs = {
        (r.path, m)
        for r in rm.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == _EXPECTED_ROUTES


def test_specific_routes_precede_dynamic_media_id():
    # /api/media/pool and /api/media/position/{media_id} MUST be declared before
    # /api/media/{media_id} — otherwise the typed-int {media_id} matcher shadows
    # the literal `pool` segment (media_id="pool" → 422 instead of the pool list).
    import routers.media as rm
    paths = [r.path for r in rm.router.routes if getattr(r, "path", None)]
    i_pool = paths.index("/api/media/pool")
    i_position = paths.index("/api/media/position/{media_id}")
    i_dynamic = paths.index("/api/media/{media_id}")
    assert i_pool < i_dynamic
    assert i_position < i_dynamic


def test_shared_helpers_are_reexported_by_identity():
    import routers.media as rm
    import mediarecords
    import server
    for name in ("_get_tags_bulk", "_get_light_records_by_ids"):
        assert getattr(rm, name) is getattr(mediarecords, name)
        assert getattr(server, name) is getattr(mediarecords, name)


def test_server_no_longer_defines_media_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "def list_media" not in src
    assert "def _get_tags_bulk" not in src
    assert "def _get_light_records_by_ids" not in src
    assert "include_router(media_router)" in src


def test_media_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.get("/api/media").status_code == 401
        assert c.get("/api/media/1").status_code == 401
        assert c.get("/api/media/1/tags").status_code == 401
        assert c.patch("/api/media/1/rating", json={}).status_code == 401
        # /api/media/pool must NOT be shadowed by /api/media/{media_id} (media_id=
        # "pool" → 422) — it resolves to the pool route and 401s on the auth dep.
        assert c.get("/api/media/pool").status_code == 401
        # a bogus sibling path is a genuine 404 (nothing is over-matching)
        assert c.get("/api/mediazz").status_code == 404
