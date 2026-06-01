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
