"""Guards the APIRouter-split foundation (fable-audit 2026-07-12 PR7).

1. Route parity: the app must register every route exactly once, with no duplicate
   (path, method) pair. This is the safety net for the FUTURE per-router split PRs
   — moving routes into APIRouters must not drop or double-register any endpoint.
2. Single-instance guard: server.py re-exports the ingest slot from state.py; the
   flag must be ONE shared instance, or a per-module copy would silently defeat the
   H3 double-whisper-OOM guard.
"""
import importlib


def test_no_duplicate_route_registrations(server_module):
    seen = set()
    dupes = []
    for r in server_module.app.routes:
        methods = tuple(sorted(getattr(r, "methods", None) or ()))
        key = (getattr(r, "path", None), methods)
        if key in seen:
            dupes.append(key)
        seen.add(key)
    assert not dupes, f"duplicate route registrations: {dupes}"
    # sanity: the app actually has a substantial route surface (guards an empty/half
    # -wired app slipping past a future split)
    api_routes = [r for r in server_module.app.routes if getattr(r, "path", "").startswith("/api/")]
    assert len(api_routes) >= 50


def test_critical_routes_present(server_module):
    paths = {getattr(r, "path", "") for r in server_module.app.routes}
    for p in ("/api/projects", "/api/bins/{bin_id}/copy", "/api/offload",
              "/api/cache/clear", "/api/retranscribe-all", "/api/search/all"):
        assert p in paths, f"missing route {p}"


def test_ingest_slot_is_single_shared_instance(server_module):
    state = importlib.import_module("state")
    # server re-exports the SAME function objects
    assert server_module._acquire_ingest_slot is state._acquire_ingest_slot
    assert server_module._release_ingest_slot is state._release_ingest_slot
    assert server_module.ingest_ws is state.ingest_ws

    # acquiring through server is visible through state (one flag, not two copies)
    assert server_module._acquire_ingest_slot() is True
    try:
        assert state._acquire_ingest_slot() is False        # blocked — same slot
        assert server_module._acquire_ingest_slot() is False
    finally:
        state._release_ingest_slot()
    assert server_module._acquire_ingest_slot() is True     # freed
    server_module._release_ingest_slot()
