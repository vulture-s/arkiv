"""First-run sample-seed router (routers/sample.py): pins the route surface, the
shared-guard identity, auth, the idempotency short-circuit, and the seed-during-ingest
409 contention path (which must free its own guard)."""
import pathlib
import re

import pytest
from starlette.testclient import TestClient

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_sample_router_is_a_leaf_module():
    src = (_ROOT / "routers" / "sample.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_router_owns_exactly_the_sample_routes():
    import routers.sample as rs
    pairs = {(r.path, m) for r in rs.router.routes for m in r.methods if m != "HEAD"}
    assert pairs == {
        # v1 on-demand re-ingest seed
        ("/api/sample/seed", "POST"), ("/api/sample/seed/status", "GET"),
        # A1 pre-built (instant) sample library
        ("/api/sample/status", "GET"), ("/api/sample/load", "POST"),
        ("/api/sample/remove", "POST"),
    }
    for name in ("sample_seed", "sample_seed_status", "_run_sample_seed",
                 "_sample_basenames", "_already_seeded",
                 "sample_prebuilt_status", "sample_prebuilt_load", "sample_prebuilt_remove"):
        assert hasattr(rs, name)


def test_uses_the_one_shared_state_guard():
    import routers.sample as rs
    import state
    assert rs._sample_guard is state.sample_seed


def test_sample_basenames_are_the_bundled_clips():
    import routers.sample as rs
    assert set(rs._sample_basenames()) == {
        "caminandes_llama.mp4", "coffee_run.mp4", "glass_half.mp4", "wing_it.mp4",
    }


def test_routes_mounted_and_auth_guarded(server_module):
    with TestClient(server_module.app) as c:
        assert c.post("/api/sample/seed").status_code == 401          # ingest_write
        assert c.get("/api/sample/seed/status").status_code == 401    # projects_read


class _BG:
    def __init__(self):
        self.queued = False

    def add_task(self, *a, **k):
        self.queued = True


def test_idempotent_shortcircuit_when_already_seeded(monkeypatch):
    import routers.sample as rs
    monkeypatch.setattr(rs, "_assert_same_site", lambda r: None)
    monkeypatch.setattr(rs, "_already_seeded", lambda names: True)  # all clips present
    bg = _BG()
    res = rs.sample_seed(request=None, background_tasks=bg, _tok={})
    assert res["queued"] == 0 and not bg.queued  # no lock grabbed, no task queued


def test_refuses_when_ingest_busy_and_frees_its_guard(monkeypatch):
    import routers.sample as rs
    import state
    from fastapi import HTTPException
    monkeypatch.setattr(rs, "_assert_same_site", lambda r: None)
    monkeypatch.setattr(rs, "_already_seeded", lambda names: False)
    monkeypatch.setattr(rs, "_acquire_ingest_slot", lambda: False)  # a real ingest holds the slot
    with pytest.raises(HTTPException) as ei:
        rs.sample_seed(request=None, background_tasks=_BG(), _tok={})
    assert ei.value.status_code == 409
    # must have released its own single-flight guard so a later retry isn't rejected
    assert state.sample_seed.acquire() is True
    state.sample_seed.release()
