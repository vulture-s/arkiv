"""R5-25 (round-5 #51): batch-retranscribe routes peeled to routers/retranscribe.py.

Ninth router peeled. Pins the split: the leaf module owns the /api/retranscribe-all
POST + /status poller + the _run_retranscribe_all worker + RetranscribeAllRequest,
server.py no longer defines them, routes mounted + auth-guarded (401-not-404). The
batch loop / two-lock ordering / backup-revert behaviour is covered end-to-end by
test_retranscribe_all.py; the ISO-639 validator by test_hardening_round4.py.
"""
import pathlib
import re

from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_retranscribe_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "retranscribe.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_retranscribe_routes():
    import routers.retranscribe as rr
    pairs = {
        (r.path, m)
        for r in rr.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/retranscribe-all", "POST"),
        ("/api/retranscribe-all/status", "GET"),
    }
    for name in ("RetranscribeAllRequest", "_run_retranscribe_all",
                 "retranscribe_all", "retranscribe_all_status"):
        assert hasattr(rr, name)


def test_router_shares_the_state_singleflight_guard():
    # The guard MUST be the one shared instance in state.py — never a per-module
    # copy — so a batch run and a concurrent /api/ingest serialise on the same slot.
    import routers.retranscribe as rr
    import state
    assert rr._retranscribe_guard is state.retranscribe
    assert rr._acquire_ingest_slot is state._acquire_ingest_slot


def test_retranscribe_all_language_validator_moved():
    import routers.retranscribe as rr
    import pydantic
    # null + valid ISO-639 accepted; non-ASCII / too-long rejected at the model.
    assert rr.RetranscribeAllRequest(language=None).language is None
    assert rr.RetranscribeAllRequest(language="ZH").language == "zh"
    for bad in ("中文", "english", "e"):
        try:
            rr.RetranscribeAllRequest(language=bad)
            assert False, "expected validation error for {0!r}".format(bad)
        except pydantic.ValidationError:
            pass


def test_server_no_longer_defines_retranscribe_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "class RetranscribeAllRequest" not in src
    assert "def _run_retranscribe_all" not in src
    assert not re.search(r"@app\.(get|post)\(\"/api/retranscribe-all", src)
    assert "include_router(retranscribe_router)" in src


def test_retranscribe_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.post("/api/retranscribe-all", json={}).status_code == 401
        assert c.get("/api/retranscribe-all/status").status_code == 401
        assert c.post("/api/retranscribe-allzz", json={}).status_code == 404
