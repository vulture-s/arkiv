"""R5-25 (round-5 #51): bins routes peeled to routers/bins.py.

Sixth router peeled. Pins the split: leaf module, owns the 8 bin routes + the 4
models + the copy helpers, server.py no longer defines them, routes mounted +
auth-guarded, and the H3 ingest single-flight slot is the SAME shared state.py
instance (so copy_bin still serializes against ingest). Bin CRUD + copy behaviour
covered by test_bins.py / test_bin_copy.py.
"""
import pathlib
import re

import pytest
from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_bins_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "bins.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_bins_routes_and_helpers():
    import routers.bins as rb
    pairs = {
        (r.path, m)
        for r in rb.router.routes
        for m in r.methods
        if m != "HEAD"
    }
    assert pairs == {
        ("/api/bins", "GET"),
        ("/api/bins", "POST"),
        ("/api/bins/{bin_id}", "GET"),
        ("/api/bins/{bin_id}", "PATCH"),
        ("/api/bins/{bin_id}", "DELETE"),
        ("/api/bins/{bin_id}/items", "POST"),
        ("/api/bins/{bin_id}/items", "DELETE"),
        ("/api/bins/{bin_id}/copy", "POST"),
    }
    for name in ("BinCreate", "BinItemRef", "BinAddItems", "BinCopyRequest",
                 "_unique_dest", "_copy_clip_verified", "_bin_detail_payload"):
        assert hasattr(rb, name)


def test_bin_name_validator_and_copy_defaults():
    import routers.bins as rb
    with pytest.raises(Exception):
        rb.BinCreate(name="   ")                       # empty-after-clean rejected
    assert rb.BinCreate(name="  my bin ").name == "my bin"  # trimmed
    # copy defaults: reference mode, no create_new
    body = rb.BinCopyRequest(dest="proj")
    assert body.mode == "reference" and body.create_new is False


def test_shares_ingest_slot_singleton_with_server():
    # copy_bin must contend on the SAME H3 slot as the ingest routes, or a
    # concurrent /api/ingest + copy_bin runs two whisper pipelines (double-OOM).
    import server
    import routers.bins as rb
    assert rb._acquire_ingest_slot is server._acquire_ingest_slot
    assert rb._release_ingest_slot is server._release_ingest_slot


def test_server_no_longer_defines_bins_handlers():
    src = (_ROOT / "server.py").read_text(encoding="utf-8")
    assert "class BinCopyRequest" not in src
    assert "def _copy_clip_verified" not in src
    assert not re.search(r"@app\.(get|post|patch|delete)\(\"/api/bins", src)
    assert "include_router(bins_router)" in src


def test_bins_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.get("/api/bins").status_code == 401
        assert c.post("/api/bins/x/copy", json={"dest": "y"}).status_code == 401
        assert c.get("/api/binszz").status_code == 404
