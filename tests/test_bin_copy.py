"""Copy-into-project orchestration (POST /api/bins/{id}/copy). The reachability
gate + reference/copy modes + create_new bootstrap are tested here with the ingest
subprocess stubbed (deterministic, no ffmpeg/Whisper/Ollama in CI); the real
`ingest --files` indexing path is covered by an end-to-end live-server run."""
import importlib
import io
import subprocess
import sys
from pathlib import Path


def _parse_ndjson(text):
    import json
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class _FakePopen:
    """Stand-in for the ingest subprocess — records the command, emits a couple of
    log lines, exits 0. Captured into `calls` so tests can assert the built cmd."""
    def __init__(self, calls):
        self._calls = calls

    def __call__(self, cmd, **kwargs):
        self._calls.append({"cmd": cmd, "env": kwargs.get("env"), "cwd": kwargs.get("cwd")})
        inst = _FakePopen(self._calls)
        inst.returncode = 0
        inst.stdout = io.StringIO("Ingesting files\n[1/1] clip done\n")
        return inst

    def wait(self):
        self.returncode = 0


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_BINS_PATH", str(tmp_path / "bins.json"))
    monkeypatch.setenv("ARKIV_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.delenv("ARKIV_PROJECT_ROOTS", raising=False)
    bins = importlib.import_module("bins")
    return bins


def _stub_ingest(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", _FakePopen(calls))
    return calls


def _stub_resolve(monkeypatch, mapping):
    """mapping: (project_name, media_id) -> {status, absolute_path, filename}."""
    import bins as bins_mod
    def fake(project_name, media_id):
        return mapping.get((project_name, str(media_id)))
    monkeypatch.setattr(bins_mod, "resolve_source", fake)


def test_copy_skips_index_when_ingest_slot_busy(fastapi_client, tmp_path, monkeypatch):
    """fable-audit 2026-07-12 (#2/#5): copy_bin's index phase must share the H3
    single-flight slot. If a full ingest already holds it, copy_bin copies/gates the
    files but must NOT spawn a second whisper+vision pipeline — it skips indexing and
    says so, rather than risking the double-whisper OOM the guard exists to prevent."""
    import server

    bins = _setup(tmp_path, monkeypatch)
    calls = _stub_ingest(monkeypatch)  # if a subprocess spawns, it lands here

    src = tmp_path / "src" / "clip.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"video-bytes")
    _stub_resolve(monkeypatch, {
        ("libA", "1"): {"status": "ok", "absolute_path": str(src), "filename": "clip.mp4"},
    })
    b = bins.create_bin("busybin")
    bins.add_items(b.id, [{"project_name": "libA", "media_id": "1", "filename": "clip.mp4"}])

    assert server._acquire_ingest_slot() is True  # simulate a concurrent ingest holding the slot
    try:
        r = fastapi_client.post(
            "/api/bins/{0}/copy".format(b.id),
            json={"dest": str(tmp_path / "np"), "create_new": True, "dest_name": "忙案",
                  "mode": "reference", "skip_vision": True, "no_embed": True},
        )
        assert r.status_code == 200, r.text
        events = _parse_ndjson(r.text)
        # the index phase reported busy and was skipped — NO ingest subprocess spawned
        assert any(e.get("type") == "index" and e.get("status") == "busy" for e in events)
        assert calls == []
        done = [e for e in events if e["type"] == "done"][0]["summary"]
        assert done["index_skipped_busy"] is True
    finally:
        server._release_ingest_slot()

    # slot is free again → a normal copy now DOES index (proves we didn't wedge it)
    assert server._acquire_ingest_slot() is True
    server._release_ingest_slot()


def test_copy_reference_gates_unreachable_and_registers_new(fastapi_client, tmp_path, monkeypatch):
    bins = _setup(tmp_path, monkeypatch)
    calls = _stub_ingest(monkeypatch)

    # one real reachable source file, one unreachable (ghost) item
    src = tmp_path / "src" / "clip.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"video-bytes")
    _stub_resolve(monkeypatch, {
        ("libA", "1"): {"status": "ok", "absolute_path": str(src), "filename": "clip.mp4"},
        ("libGhost", "9"): {"status": "project_unregistered", "absolute_path": "", "filename": "gone.mp4"},
    })

    b = bins.create_bin("copybin")
    bins.add_items(b.id, [
        {"project_name": "libA", "media_id": "1", "filename": "clip.mp4"},
        {"project_name": "libGhost", "media_id": "9", "filename": "gone.mp4"},
    ])

    dest = tmp_path / "newproj"
    r = fastapi_client.post(
        "/api/bins/{0}/copy".format(b.id),
        json={"dest": str(dest), "create_new": True, "dest_name": "新案",
              "mode": "reference", "skip_vision": True, "no_embed": True},
    )
    assert r.status_code == 200, r.text
    events = _parse_ndjson(r.text)
    kinds = [e.get("type") for e in events]
    assert "gate" in kinds and "done" in kinds

    done = [e for e in events if e["type"] == "done"][0]["summary"]
    assert done["copied"] == 1  # only the reachable one
    assert done["mode"] == "reference"
    # RED LINE: the unreachable item is SKIPPED and named (never silently dropped)
    assert done["skipped"] == [{"project_name": "libGhost", "media_id": "9", "status": "project_unregistered"}]

    # reference mode indexes the ORIGINAL absolute path (no copy) → ingest --files <src>
    assert len(calls) == 1
    cmd = calls[0]["cmd"]
    assert "--files" in cmd and str(src) in cmd
    assert "--db" in cmd and str(dest / ".arkiv" / "project.db") in cmd
    assert "--skip-vision" in cmd and "--no-embed" in cmd
    # ingest runs AS the dest project (paths relativize against the dest, not the server)
    assert calls[0]["env"]["ARKIV_PROJECT_ROOT"] == str(dest.resolve())
    # source file untouched
    assert src.exists() and src.read_bytes() == b"video-bytes"

    # create_new registered the project
    import projects
    assert any(p.name == "新案" for p in projects.list_registry_projects())


def test_copy_mode_copies_verified_bytes_and_keeps_source(fastapi_client, tmp_path, monkeypatch):
    bins = _setup(tmp_path, monkeypatch)
    calls = _stub_ingest(monkeypatch)

    src = tmp_path / "src" / "clip.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"the-real-bytes-1234567890")
    _stub_resolve(monkeypatch, {
        ("libA", "1"): {"status": "ok", "absolute_path": str(src), "filename": "clip.mp4"},
    })

    b = bins.create_bin("copybin")
    bins.add_items(b.id, [{"project_name": "libA", "media_id": "1", "filename": "clip.mp4"}])

    dest = tmp_path / "newproj_copy"
    r = fastapi_client.post(
        "/api/bins/{0}/copy".format(b.id),
        json={"dest": str(dest), "create_new": True, "mode": "copy",
              "skip_vision": True, "no_embed": True},
    )
    assert r.status_code == 200, r.text
    events = _parse_ndjson(r.text)
    assert any(e.get("type") == "copy" and e.get("done") == 1 for e in events)

    # bytes landed under dest/media, identical to source, and the SOURCE is intact
    copied = dest / "media" / "clip.mp4"
    assert copied.exists()
    assert copied.read_bytes() == b"the-real-bytes-1234567890"
    assert src.exists() and src.read_bytes() == b"the-real-bytes-1234567890"  # never deleted
    # ingest indexes the COPIED file (not the original)
    assert str(copied) in calls[0]["cmd"]


def test_copy_into_existing_project(fastapi_client, tmp_path, monkeypatch):
    bins = _setup(tmp_path, monkeypatch)
    _stub_ingest(monkeypatch)

    # an existing registered destination project
    existing = tmp_path / "existing"
    (existing / ".arkiv").mkdir(parents=True)
    import projects
    projects.add_project("已存在專案", str(existing))

    src = tmp_path / "src" / "clip.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    _stub_resolve(monkeypatch, {("libA", "1"): {"status": "ok", "absolute_path": str(src), "filename": "clip.mp4"}})

    b = bins.create_bin("cb")
    bins.add_items(b.id, [{"project_name": "libA", "media_id": "1", "filename": "clip.mp4"}])

    r = fastapi_client.post(
        "/api/bins/{0}/copy".format(b.id),
        json={"dest": "已存在專案", "create_new": False, "mode": "reference",
              "skip_vision": True, "no_embed": True},
    )
    assert r.status_code == 200, r.text
    done = [e for e in _parse_ndjson(r.text) if e["type"] == "done"][0]["summary"]
    assert done["copied"] == 1 and done["dest"] == "已存在專案"


def test_copy_unknown_dest_project_400(fastapi_client, tmp_path, monkeypatch):
    bins = _setup(tmp_path, monkeypatch)
    b = bins.create_bin("cb")
    r = fastapi_client.post(
        "/api/bins/{0}/copy".format(b.id),
        json={"dest": "沒有這個專案", "create_new": False, "mode": "reference"},
    )
    assert r.status_code == 400
