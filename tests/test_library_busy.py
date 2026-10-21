"""#561: the desktop app asks /api/library/busy before switching library.

Switching restarts the backend, so it must refuse while a long job holds its
single-flight slot. Each slot is flipped directly — the endpoint reads the same
objects the jobs hold, which is the point.
"""
import state
from routers import offload as offload_router


def test_idle_server_is_not_busy(fastapi_client):
    r = fastapi_client.get("/api/library/busy")
    assert r.status_code == 200
    assert r.json() == {"busy": False, "jobs": []}


def test_ingest_slot_makes_it_busy(fastapi_client):
    assert state._acquire_ingest_slot()
    try:
        assert fastapi_client.get("/api/library/busy").json() == {"busy": True, "jobs": ["ingest"]}
    finally:
        state._release_ingest_slot()


def test_offload_and_single_flight_slots_are_reported(fastapi_client):
    assert offload_router._acquire_offload_slot("/Volumes/CARD")
    assert state.proxy_build.acquire()
    try:
        body = fastapi_client.get("/api/library/busy").json()
        assert body["busy"] is True
        assert body["jobs"] == ["offload", "proxy_build"]
    finally:
        offload_router._release_offload_slot("/Volumes/CARD")
        state.proxy_build.release()
    assert fastapi_client.get("/api/library/busy").json()["busy"] is False


def test_model_download_from_the_setup_wizard_blocks_switching(fastapi_client):
    assert state.model_pull.acquire()
    try:
        assert fastapi_client.get("/api/library/busy").json() == {"busy": True, "jobs": ["model_pull"]}
    finally:
        state.model_pull.release()
