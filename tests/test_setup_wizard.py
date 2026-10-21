"""9.8a first-run setup wizard: status probes, completion flag, model-pull job."""
import time

import pytest

import setup_check
import state


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ARKIV_SETUP_STATE", str(tmp_path / "setup.json"))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf"))
    yield
    state.model_pull.release()
    state.model_pull.reset_progress(running=False, items=[], message="")


def _no_ollama(monkeypatch, have=None):
    def fake():
        return {"running": have is not None, "installed": have is not None,
                "models": [{"kind": "ollama", "role": r, "label": l, "name": "m-" + r,
                            "present": bool(have and ("m-" + r) in have), "size_hint": h}
                           for r, l, _a, h in setup_check.OLLAMA_ROLES]}
    monkeypatch.setattr(setup_check, "ollama_status", fake)


def test_status_shape_and_completion_roundtrip(fastapi_client, monkeypatch):
    _no_ollama(monkeypatch)
    st = fastapi_client.get("/api/setup/status").json()
    assert st["completed"] is False
    assert {"hardware", "transcribe", "ffmpeg", "whisper", "ollama", "pull"} <= set(st)
    assert st["hardware"]["cores"]
    assert fastapi_client.post("/api/setup/complete", json={"skipped": True}).json()["skipped"] is True
    assert fastapi_client.get("/api/setup/status").json()["completed"] is True
    fastapi_client.post("/api/setup/reset")
    assert fastapi_client.get("/api/setup/status").json()["completed"] is False


def test_whisper_presence_reads_the_hf_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_check.config, "WHISPER_MODEL", "org/whisper-x")
    assert setup_check.whisper_status()["present"] is False
    snap = tmp_path / "hf" / "models--org--whisper-x" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")
    assert setup_check.whisper_status()["present"] is True


def test_local_whisper_path_counts_as_present(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_check.config, "WHISPER_MODEL", str(tmp_path))
    assert setup_check.whisper_status() == {"kind": "whisper", "name": str(tmp_path), "present": True, "where": "local"}


def test_no_nvidia_with_cuda_default_is_flagged(monkeypatch):
    monkeypatch.delenv("ARKIV_WHISPER_DEVICE", raising=False)
    d = setup_check.transcribe_device({"apple_silicon": False, "gpu": None})
    assert d["ok"] == "false" and "cpu" in d["note"]
    assert setup_check.transcribe_device({"apple_silicon": True})["device"] == "mlx"


def test_ollama_model_name_matching():
    assert setup_check._model_present("bge-m3", ["bge-m3:latest"])
    assert setup_check._model_present("qwen2.5vl:7b", ["qwen2.5vl:7b"])
    assert not setup_check._model_present("qwen2.5vl:7b", ["qwen2.5vl:3b"])
    assert not setup_check._model_present("bge-m3", ["bge-m3-large:latest"])


def test_pull_refuses_when_ollama_is_down(fastapi_client, monkeypatch, tmp_path):
    monkeypatch.setattr(setup_check.config, "WHISPER_MODEL", str(tmp_path))  # whisper already "present"
    _no_ollama(monkeypatch, have=None)
    r = fastapi_client.post("/api/setup/models/pull", json={})
    assert r.status_code == 409 and "Ollama" in r.json()["detail"]


def test_pull_runs_missing_models_and_reports_each(fastapi_client, monkeypatch, tmp_path):
    from routers import setup as setup_router

    monkeypatch.setattr(setup_check.config, "WHISPER_MODEL", str(tmp_path))
    _no_ollama(monkeypatch, have=["m-embed"])
    pulled = []

    def fake_pull(item):
        if item["name"] == "m-chat":
            raise RuntimeError("registry down")
        pulled.append(item["name"])
        item.update(total=10, completed=10)

    monkeypatch.setattr(setup_router, "_pull_ollama", fake_pull)
    r = fastapi_client.post("/api/setup/models/pull", json={})
    assert r.json() == {"queued": 2}
    for _ in range(100):
        prog = fastapi_client.get("/api/setup/models/pull").json()
        if not prog["running"]:
            break
        time.sleep(0.05)
    assert pulled == ["m-vision"]
    assert [i["status"] for i in prog["items"]] == ["done", "error"]
    assert prog["items"][1]["message"].startswith("RuntimeError: registry down")
    assert prog["message"] == "有 1 個模型下載失敗"
    assert state.model_pull.acquire(), "the slot must be released when the job ends"


def test_friendly_errors():
    from routers.setup import _friendly

    assert _friendly(RuntimeError("pull model manifest: file does not exist")).startswith("找不到這個模型")
    assert _friendly(ConnectionError("Connection refused")).startswith("連不到下載伺服器")
