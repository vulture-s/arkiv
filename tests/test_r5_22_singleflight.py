"""R5-22 (round-5 #52/#59): state.SingleFlight for embed/retranscribe/proxy.

#52 — embed-rebuild / retranscribe used bare module-global bools rebound via
`global` in route handlers. Once the APIRouter split moves those routes to their
own modules, `from server import _embed_rebuild_active` would import a FROZEN COPY
of the bool → the importing router's guard reads False forever. Wrapping the flag
in a state.SingleFlight OBJECT means every module holds one live instance.

#59 — /api/proxy/build had no single-flight (unlike its siblings); a double-click
launched parallel full-library ffmpeg loops. Now guarded by state.proxy_build,
released by the _build_proxies_all wrapper.
"""
import importlib

import pytest


def _insert_media(path="clip.mov", filename="clip.mov"):
    db = importlib.import_module("db")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO media (path, filename) VALUES (?, ?)", (path, filename))


# ── #52: SingleFlight primitive + cross-module liveness ─────────────────────
def test_singleflight_semantics():
    import state
    sf = state.SingleFlight("t")
    assert sf.acquire() is True
    assert sf.acquire() is False    # already held → refused
    assert sf.active is True
    sf.release()
    assert sf.active is False
    assert sf.acquire() is True     # released → reusable
    sf.release()


def test_progress_dict_mutated_in_place_not_rebound():
    import state
    sf = state.SingleFlight("p")
    ref = sf.progress               # a poller captures the dict reference
    sf.reset_progress(total=3, done=0)
    assert ref is sf.progress and ref["total"] == 3   # same object, updated in place
    sf.progress["done"] = 2
    assert ref["done"] == 2


def test_guard_is_one_live_instance_across_modules(server_module):
    # The crux of #52: server imports the guard OBJECT from state, so a mutation
    # via server's alias is visible on state.embed_rebuild — a frozen bool copy
    # would read False forever in the importing module.
    import state
    assert server_module._embed_guard is state.embed_rebuild
    assert server_module._retranscribe_guard is state.retranscribe
    assert server_module._proxy_guard is state.proxy_build
    try:
        assert server_module._embed_guard.acquire()
        assert state.embed_rebuild.active is True
    finally:
        server_module._embed_guard.release()


# ── #52: endpoints refuse concurrent runs via the shared guard ──────────────
def test_embed_rebuild_409_when_guard_held(fastapi_client, server_module):
    _insert_media("a.mp4", "a.mp4")  # non-empty library → past the early return
    assert server_module._embed_guard.acquire()
    try:
        r = fastapi_client.post("/api/embed/rebuild")
        assert r.status_code == 409
    finally:
        server_module._embed_guard.release()


# ── #59: proxy build single-flight + wrapper release ────────────────────────
def test_proxy_build_409_when_guard_held(fastapi_client, server_module, tmp_path, monkeypatch):
    config = importlib.import_module("config")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    _insert_media("clip.mov", "clip.mov")  # proxy doesn't exist → to_build non-empty
    assert server_module._proxy_guard.acquire()
    try:
        r = fastapi_client.post("/api/proxy/build")
        assert r.status_code == 409
    finally:
        server_module._proxy_guard.release()


def test_proxy_build_wrapper_releases_slot(server_module, monkeypatch):
    calls = []
    monkeypatch.setattr(server_module, "_build_proxies", lambda items: calls.append(items))
    assert server_module._proxy_guard.acquire()  # route would have acquired it
    server_module._build_proxies_all([{"id": 1, "path": "x"}])
    assert server_module._proxy_guard.active is False, "wrapper must free the slot"
    assert calls == [[{"id": 1, "path": "x"}]]


def test_proxy_build_wrapper_releases_on_exception(server_module, monkeypatch):
    def boom(items):
        raise RuntimeError("ffmpeg blew up")
    monkeypatch.setattr(server_module, "_build_proxies", boom)
    assert server_module._proxy_guard.acquire()
    with pytest.raises(RuntimeError):
        server_module._build_proxies_all([])
    assert server_module._proxy_guard.active is False, "slot freed even on failure"
