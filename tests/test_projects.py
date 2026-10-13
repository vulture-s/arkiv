import importlib
import os
from pathlib import Path


def test_registry_round_trip_add_list_sync_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "projects.json"))
    projects = importlib.import_module("projects")

    project_root = tmp_path / "proj-a"
    (project_root / ".arkiv").mkdir(parents=True)
    project_db = project_root / ".arkiv" / "project.db"
    project_db.write_text("stub", encoding="utf-8")
    os.utime(str(project_db), None)

    added = projects.add_project("proj-a", str(project_root), ["client", "q2"])
    assert added.name == "proj-a"
    assert added.tags == ["client", "q2"]

    listed = projects.list_registry_projects()
    assert len(listed) == 1
    assert listed[0].to_dict()["path"] == str(project_root)

    synced = projects.sync_projects()
    assert len(synced) == 1
    assert synced[0].last_indexed_at.endswith("Z")

    removed = projects.remove_project("proj-a")
    assert removed.name == "proj-a"
    assert projects.list_registry_projects() == []


def test_discover_projects_unions_registry_and_env_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "projects.json"))
    projects = importlib.import_module("projects")

    registry_root = tmp_path / "registry-root"
    registry_root.mkdir()
    env_root = tmp_path / "env-root"
    env_root.mkdir()

    projects.add_project("registry-root", str(registry_root))
    monkeypatch.setenv("ARKIV_PROJECT_ROOTS", str(env_root))

    discovered = projects.discover_projects()
    names = {project.name for project in discovered}
    assert names == {"registry-root", "env-root"}
    assert any(project.source == "env" for project in discovered)


def test_list_projects_returns_clean_500_on_corrupt_registry(fastapi_client, tmp_path, monkeypatch):
    """A corrupt ~/.arkiv-projects.json must yield a clean 500, not an uncaught
    stack trace, on the read endpoints."""
    import projects as project_registry
    bad = tmp_path / "arkiv-projects.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")
    monkeypatch.setattr(project_registry, "_default_registry_path", lambda: bad)
    resp = fastapi_client.get("/api/projects")
    assert resp.status_code == 500
    assert "registry" in resp.json()["detail"].lower()
    # sync too
    assert fastapi_client.post("/api/projects/sync").status_code == 500


def test_save_registry_uses_unique_tmp_file(tmp_path, monkeypatch):
    """Concurrent saves must not share one '<file>.tmp' (corruption vector)."""
    import projects as project_registry
    reg = tmp_path / "arkiv-projects.json"
    monkeypatch.setattr(project_registry, "_default_registry_path", lambda: reg)
    project_registry.save_registry({"version": 1, "projects": []})
    # the shared, fixed-name tmp must not survive a save (unique + cleaned up)
    assert not (tmp_path / "arkiv-projects.json.tmp").exists()
    assert reg.exists() and "projects" in reg.read_text(encoding="utf-8")

def _registry_with(tmp_path, monkeypatch, entries):
    """Point the registry at a temp file and add each (name, root) in order."""
    import projects as project_registry
    reg = tmp_path / "arkiv-projects.json"
    monkeypatch.setattr(project_registry, "_default_registry_path", lambda: reg)
    for name, root in entries:
        root.mkdir(parents=True, exist_ok=True)
        project_registry.add_project(name, str(root))
    return reg


def test_list_projects_flags_the_open_library(fastapi_client, tmp_path, monkeypatch):
    """`is_current` marks the registry entry that IS config.PROJECT_ROOT.

    The sidebar needs this because it cannot derive it: /api/stats names the open
    library by folder (`.arkiv`) while the registry stores a chosen project name
    against an absolute path, and those never match. Same shape as #343 — the
    judgement has to be made where the root is known.
    """
    import config
    here = tmp_path / "open-one"
    other = tmp_path / "other-one"
    _registry_with(tmp_path, monkeypatch, [("open-one", here), ("other-one", other)])
    monkeypatch.setattr(config, "PROJECT_ROOT", here)

    rows = fastapi_client.get("/api/projects").json()["projects"]
    flags = {r["name"]: r["is_current"] for r in rows}
    assert flags == {"open-one": True, "other-one": False}


def test_is_current_is_computed_before_paths_are_sanitised(server_module, tmp_path, monkeypatch):
    """Ordering guard: `_mark_current` must run BEFORE `_sanitize_project_paths`.

    A non-admin caller gets basenames instead of absolute roots (fable-audit #22).
    A basename cannot be compared to PROJECT_ROOT, so if the two steps were ever
    swapped every row would silently report `is_current: false` — and the only
    visible symptom would be the sidebar failing to highlight the open library,
    which is exactly the kind of thing nobody writes a bug report about.
    """
    import admin
    import config
    from starlette.testclient import TestClient

    here = tmp_path / "open-one"
    _registry_with(tmp_path, monkeypatch, [("open-one", here)])
    monkeypatch.setattr(config, "PROJECT_ROOT", here)

    token = admin.create_token(name="pytest-readonly", scopes=["projects_read"])
    headers = {"Authorization": "Bearer {0}".format(token["raw_token"])}
    with TestClient(server_module.app, headers=headers) as client:
        rows = client.get("/api/projects").json()["projects"]

    assert len(rows) == 1
    # the path really was reduced to a basename for this caller...
    assert rows[0]["path"] == "open-one"
    # ...and the flag survived it anyway.
    assert rows[0]["is_current"] is True


def test_unresolvable_project_root_leaves_every_row_not_current(fastapi_client, tmp_path, monkeypatch):
    """A root we cannot normalise means "no entry is current", not a 500.

    `/api/projects` is on the first paint of the main view; a library root that
    has gone away (unmounted NAS, renamed folder) must degrade to an unhighlighted
    list rather than taking the whole sidebar down with it.
    """
    import config
    import routers.projects as rp

    _registry_with(tmp_path, monkeypatch, [("a", tmp_path / "a"), ("b", tmp_path / "b")])
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path / "gone")

    def _boom(_path):
        raise OSError("root is unreachable")

    monkeypatch.setattr(rp.project_registry, "_normalize_key", _boom)

    resp = fastapi_client.get("/api/projects")
    assert resp.status_code == 200
    assert [r["is_current"] for r in resp.json()["projects"]] == [False, False]
