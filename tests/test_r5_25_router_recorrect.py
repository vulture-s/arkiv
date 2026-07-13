"""R5-25 (round-5 #51): correction-dictionary + recorrect routes peeled to
routers/recorrect.py.

Eleventh router peeled. Pins the split: the leaf module owns the 5 routes + its 2
models, server.py no longer defines them, routes mounted + auth-guarded
(401-not-404). _rebuild_embeddings moved to state.py (ROOT→config.BASE_DIR) so the
recorrect rebuild chain and /api/embed/rebuild share ONE worker + the ONE
embed_rebuild guard. The recorrect apply/backup/revert behaviour is covered by
test_corrections.py; the embed single-flight by test_r5_22_singleflight.py.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_recorrect_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "recorrect.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_recorrect_routes_and_models():
    import routers.recorrect as rr
    pairs = {
        (r.path, m)
        for r in rr.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/corrections", "GET"),
        ("/api/corrections", "PUT"),
        ("/api/recorrect", "POST"),
        ("/api/recorrect/backups", "GET"),
        ("/api/recorrect/revert", "POST"),
    }
    for name in ("CorrectionsBody", "RevertBody", "recorrect",
                 "get_corrections", "recorrect_revert"):
        assert hasattr(rr, name)


def test_rebuild_embeddings_extracted_to_state_and_shared():
    # the worker + guard must be the ONE state instance shared with /api/embed/rebuild
    import routers.recorrect as rr
    import state
    import server
    assert rr._rebuild_embeddings is state._rebuild_embeddings
    assert server._rebuild_embeddings is state._rebuild_embeddings   # re-export intact
    assert rr._embed_guard is state.embed_rebuild
    # _rebuild_embeddings no longer defined in server.py (only re-exported)
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "def _rebuild_embeddings" not in src
    # state uses config.BASE_DIR for embed.py, not the old server.ROOT
    state_src = (_ROOT / "state.py").read_text(encoding="utf-8")
    assert "config.BASE_DIR" in state_src and "embed.py" in state_src


def test_server_no_longer_defines_recorrect_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "class CorrectionsBody" not in src
    assert "def get_corrections" not in src
    assert not re.search(r"@app\.(get|put|post)\(\"/api/(recorrect|corrections)", src)
    assert "include_router(recorrect_router)" in src


def test_recorrect_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.get("/api/corrections").status_code == 401
        assert c.put("/api/corrections", json={"rules": []}).status_code == 401
        assert c.post("/api/recorrect").status_code == 401
        assert c.get("/api/recorrect/backups").status_code == 401
        assert c.post("/api/recorrect/revert", json={}).status_code == 401
        assert c.get("/api/correctionszz").status_code == 404
