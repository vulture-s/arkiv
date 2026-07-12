"""Regression: /api/projects[/health] must not leak absolute project roots to a
remote projects_read-only token (fable-audit 2026-07-12 #22).

(a)+(c) model: full paths only when the caller is admin-scoped. Loopback-trust
grants all scopes, so the LOCAL operator UI keeps full paths (a); a remote admin
token keeps them (c); a remote read-only token gets basenames.

A forwarded header (X-Forwarded-For) is what trips auth._looks_proxied, which
disables loopback-trust and forces real token-scope validation — the only way to
exercise a non-admin caller against the in-process TestClient (which is loopback).
"""
import importlib
import json
from pathlib import Path

REMOTE = {"X-Forwarded-For": "203.0.113.9"}  # → treated as proxied/remote


def _register_secret_project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    root = tmp_path / "影片專案" / "機密客戶A"
    root.mkdir(parents=True)
    projects = importlib.import_module("projects")
    projects.add_project("機密客戶A", str(root))
    return str(root)


def _token(scopes):
    admin = importlib.import_module("admin")
    return admin.create_token(name="t-" + "-".join(scopes), scopes=scopes)["raw_token"]


def test_projects_basenamed_for_remote_readonly_token(fastapi_client, tmp_path, monkeypatch):
    root = _register_secret_project(tmp_path, monkeypatch)
    ro = _token(["projects_read"])
    resp = fastapi_client.get(
        "/api/projects",
        headers={"Authorization": f"Bearer {ro}", **REMOTE},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["projects"][0]["path"] == "機密客戶A"          # basenamed
    blob = json.dumps(data, ensure_ascii=False)
    assert root not in blob                                     # no absolute path
    assert str(tmp_path) not in blob


def test_projects_health_basenamed_for_remote_readonly_token(fastapi_client, tmp_path, monkeypatch):
    root = _register_secret_project(tmp_path, monkeypatch)
    ro = _token(["projects_read"])
    resp = fastapi_client.get(
        "/api/projects/health",
        headers={"Authorization": f"Bearer {ro}", **REMOTE},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["projects"][0]["path"] == "機密客戶A"
    assert root not in json.dumps(data, ensure_ascii=False)


def test_projects_full_path_for_remote_admin_token(fastapi_client, tmp_path, monkeypatch):
    root = _register_secret_project(tmp_path, monkeypatch)
    adm = _token(["admin", "projects_read"])
    resp = fastapi_client.get(
        "/api/projects",
        headers={"Authorization": f"Bearer {adm}", **REMOTE},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["projects"][0]["path"] == str(Path(root).expanduser())


def test_projects_full_path_for_local_loopback(fastapi_client, tmp_path, monkeypatch):
    # default TestClient = loopback, no forwarded header → all scopes → full path
    # (the local operator's registry/mount UI is unaffected).
    root = _register_secret_project(tmp_path, monkeypatch)
    resp = fastapi_client.get("/api/projects")
    assert resp.status_code == 200, resp.text
    assert resp.json()["projects"][0]["path"] == str(Path(root).expanduser())
