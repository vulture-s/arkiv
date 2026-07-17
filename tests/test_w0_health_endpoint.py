"""Wave-0 support: GET /api/health (JSON self-diagnostic) + GET /api/version.

A packaged .app user has no terminal to run `python health.py`, so a broken box
was un-diagnosable. These unauthenticated endpoints let a user (or support) curl
ONE URL for a machine-readable readiness report — booleans + model names only,
no absolute paths.
"""
import json

import config


def test_api_version(fastapi_client):
    r = fastapi_client.get("/api/version")
    assert r.status_code == 200
    assert r.json()["version"] == config.VERSION
    assert r.json()["version"]  # non-empty


def test_api_health_shape_and_no_path_leak(fastapi_client):
    r = fastapi_client.get("/api/health")
    # 200 when every required dep is present, 503 otherwise (e.g. ollama down in CI)
    assert r.status_code in (200, 503)
    body = r.json()
    assert body["version"] == config.VERSION
    assert isinstance(body["ready"], bool)
    checks = body["checks"]
    # the key runtime deps are reported
    for k in ("ffmpeg", "ffprobe", "ollama", "exiftool"):
        assert k in checks and isinstance(checks[k]["ok"], bool)
    # NO absolute filesystem paths leak (safe to expose unauthenticated)
    blob = json.dumps(body)
    assert "/Users/" not in blob and "/home/" not in blob and "C:\\" not in blob


def test_api_health_status_matches_ready(fastapi_client):
    r = fastapi_client.get("/api/health")
    ready = r.json()["ready"]
    assert (r.status_code == 200) == ready
