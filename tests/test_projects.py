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
