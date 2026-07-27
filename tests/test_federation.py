import importlib
import sqlite3
import types
from pathlib import Path


def _make_project(tmp_path, name, with_db=True, media_count=100):
    root = tmp_path / name
    db_dir = root / ".arkiv"
    chroma_dir = db_dir / "chroma_db"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "project.db"
    if with_db:
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE media ("
            "id INTEGER PRIMARY KEY, "
            "path TEXT, "
            "filename TEXT, "
            "duration_s REAL, "
            "rating TEXT, "
            "lang TEXT, "
            "ext TEXT, "
            "transcript TEXT"
            ")"
        )
        conn.execute(
            "CREATE TABLE tags ("
            "id INTEGER PRIMARY KEY, "
            "media_id INTEGER, "
            "name TEXT, "
            "source TEXT DEFAULT 'manual'"
            ")"
        )
        for idx in range(1, media_count + 1):
            conn.execute(
                "INSERT INTO media (id, path, filename, duration_s, rating, lang, ext, transcript) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    idx,
                    "clips/{0}.mp4".format(idx),
                    "{0}_{1}.mp4".format(name, idx),
                    float(idx),
                    "good" if idx % 2 else "review",
                    "en",
                    ".mp4",
                    "project {0} row {1} query token".format(name, idx),
                ),
            )
        conn.commit()
        conn.close()
    return root


def _fake_chroma_factory(score_seed):
    class FakeCollection(object):
        def __init__(self, project_name):
            self.project_name = project_name

        def query(self, query_embeddings, n_results, include):
            assert include == ["documents", "metadatas", "distances"]
            return {
                "documents": [[
                    "{0} top hit".format(self.project_name),
                    "{0} second hit".format(self.project_name),
                ]],
                "metadatas": [[
                    {
                        "media_id": "42",
                        "filename": "{0}_42.mp4".format(self.project_name),
                        "path": "clips/42.mp4",
                        "duration_s": 42.0,
                        "lang": "en",
                        "chunk_type": "transcript",
                        "chunk_idx": 0,
                    },
                    {
                        "media_id": "43",
                        "filename": "{0}_43.mp4".format(self.project_name),
                        "path": "clips/43.mp4",
                        "duration_s": 43.0,
                        "lang": "en",
                        "chunk_type": "transcript",
                        "chunk_idx": 1,
                    },
                ]],
                "distances": [[0.05 + score_seed, 0.25 + score_seed]],
            }

    class FakeClient(object):
        def __init__(self, path):
            self.path = path

        def get_collection(self, name):
            project_name = Path(self.path).parent.parent.name
            return FakeCollection(project_name)

    return FakeClient


def test_search_all_projects_merges_scores_and_uses_shared_embed(tmp_path, monkeypatch):
    federation = importlib.import_module("federation")
    projects = []
    for idx, name in enumerate(["alpha", "beta", "gamma"]):
        root = _make_project(tmp_path, name)
        projects.append(importlib.import_module("projects").ProjectMeta(name=name, path=root))

    calls = {"embed": 0}

    def fake_embed(query):
        calls["embed"] += 1
        return [0.42]

    monkeypatch.setattr(federation, "embed_query", fake_embed)
    monkeypatch.setattr(federation.config, "discover_projects", lambda: projects)
    monkeypatch.setattr(federation, "chromadb", types.SimpleNamespace(PersistentClient=_fake_chroma_factory(0.0)))

    payload = federation.search_all_projects("query token", limit=6, per_project_limit=2, timeout=1.0)

    assert calls["embed"] == 1
    assert payload["projects_queried"] == 3
    assert payload["projects_failed"] == 0
    assert len(payload["items"]) == 6
    assert payload["items"][0]["score"] >= payload["items"][1]["score"]
    assert {item["project_name"] for item in payload["items"]} == {"alpha", "beta", "gamma"}
    assert len({(item["project_path"], item["media_id"]) for item in payload["items"] if item["media_id"] == "42"}) == 3


def test_search_all_projects_isolates_missing_db_and_keeps_other_results(tmp_path, monkeypatch):
    federation = importlib.import_module("federation")
    project_mod = importlib.import_module("projects")
    good_a = project_mod.ProjectMeta(name="good-a", path=_make_project(tmp_path, "good-a"))
    good_b = project_mod.ProjectMeta(name="good-b", path=_make_project(tmp_path, "good-b"))
    missing = project_mod.ProjectMeta(name="missing-db", path=_make_project(tmp_path, "missing-db", with_db=False))

    monkeypatch.setattr(federation.config, "discover_projects", lambda: [good_a, missing, good_b])
    monkeypatch.setattr(federation, "embed_query", lambda query: [0.1])
    monkeypatch.setattr(federation, "chromadb", types.SimpleNamespace(PersistentClient=_fake_chroma_factory(0.1)))

    payload = federation.search_all_projects("query token", limit=5, per_project_limit=2, timeout=1.0)

    assert payload["projects_queried"] == 3
    assert payload["projects_failed"] == 1
    assert any(error["stage"] == "db_missing" for error in payload["errors"])
    assert {item["project_name"] for item in payload["items"]} == {"good-a", "good-b"}


def test_project_health_flags_nas_unmounted(monkeypatch):
    health = importlib.import_module("health")
    project = types.SimpleNamespace(path=Path("/Volumes/X/Project"))

    def fake_exists(self):
        text = self.as_posix()
        if text == "/Volumes/X":
            return False
        return True

    def fake_is_dir(self):
        return True

    monkeypatch.setattr(Path, "exists", fake_exists, raising=False)
    monkeypatch.setattr(Path, "is_dir", fake_is_dir, raising=False)

    assert health.project_health(project) == health.HealthStatus.NAS_UNMOUNTED


def test_search_all_projects_times_out_a_slow_project(tmp_path, monkeypatch):
    federation = importlib.import_module("federation")
    project_mod = importlib.import_module("projects")
    project = project_mod.ProjectMeta(name="slow", path=_make_project(tmp_path, "slow"))

    def slow_query(*args, **kwargs):
        import time

        time.sleep(0.2)
        return federation.ProjectQueryResult(
            project_name="slow",
            project_path=str(project.path),
            items=[],
            error=None,
            latency_ms=200,
            status="ok",
        )

    monkeypatch.setattr(federation.config, "discover_projects", lambda: [project])
    monkeypatch.setattr(federation, "embed_query", lambda query: [0.1])
    monkeypatch.setattr(federation, "query_single_project", slow_query)

    payload = federation.search_all_projects("query token", limit=5, per_project_limit=2, timeout=0.01)

    assert payload["projects_failed"] == 1
    assert payload["items"] == []
    assert payload["errors"][0]["stage"] == "timeout"


def test_api_media_q_route_stays_compatible(fastapi_client, sample_record):
    db = importlib.import_module("db")
    record_root = Path.cwd() / "temp" / "compat-media"
    record_root.mkdir(parents=True, exist_ok=True)
    db.upsert(sample_record(
        path=str(record_root / "media_1.mp4"),
        thumbnail_path=str(record_root / "thumb_1.jpg"),
    ))

    response = fastapi_client.get("/api/media", params={"q": "UTF-8"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["search"] is True
    assert payload["total"] >= 1


def test_query_chroma_uses_explicit_embeddings_only(tmp_path, monkeypatch):
    """SECURITY invariant guard (2026-07-26 audit): federation opens EXTERNAL, semi-trusted
    project chroma dirs. chromadb builds+runs a collection's PERSISTED embedding_function only
    when a caller passes query_texts= / documents-without-embeddings (ChromaToast / chroma#6717
    client-SDK-RCE class). Federation must ALWAYS query with explicit query_embeddings= and
    never query_texts= / add(documents=), so a poisoned external collection's EF config is never
    instantiated. This fails if _query_chroma regresses to a text-embedding path."""
    federation = importlib.import_module("federation")
    root = _make_project(tmp_path, "ext")
    project = importlib.import_module("projects").ProjectMeta(name="ext", path=root)

    seen = {"query_kwargs": None}

    class SpyCollection(object):
        def query(self, **kwargs):
            seen["query_kwargs"] = kwargs
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

        def add(self, *args, **kwargs):  # tripwire: never write to an external project's collection
            raise AssertionError("federation must not add() to an external project collection")

    class SpyClient(object):
        def __init__(self, path):
            self.path = path

        def get_collection(self, name, **kwargs):
            return SpyCollection()

    monkeypatch.setattr(federation, "embed_query", lambda query: [0.1, 0.2, 0.3])
    monkeypatch.setattr(federation.config, "discover_projects", lambda: [project])
    monkeypatch.setattr(federation, "chromadb", types.SimpleNamespace(PersistentClient=SpyClient))

    federation.search_all_projects("query token", limit=5, per_project_limit=2, timeout=2.0)

    kwargs = seen["query_kwargs"]
    assert kwargs is not None, "federation never queried the external chroma collection"
    assert "query_embeddings" in kwargs, "federation must query external chroma with explicit query_embeddings="
    assert "query_texts" not in kwargs, (
        "federation must NOT pass query_texts= to an external collection — that builds+runs "
        "its (untrusted) persisted embedding_function config"
    )
