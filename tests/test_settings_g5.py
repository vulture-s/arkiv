"""Phase 9.7 G5② — persisted settings overrides (module + API)."""
import importlib

import pytest


# ---- module-level unit tests (default ← global ← project) ----

def test_effective_falls_back_to_config_default(tmp_db):
    settings = importlib.reload(importlib.import_module("settings"))
    config = importlib.import_module("config")
    assert settings.effective("vision.num_ctx") == config.OLLAMA_VISION_NUM_CTX
    assert settings.effective("export.default_dir") == ""
    assert settings.effective("transcription.default_mode") == config.WHISPER_GUARD_DEFAULT_MODE


def test_global_override_then_project_override_layering(tmp_db):
    settings = importlib.reload(importlib.import_module("settings"))
    settings.put({"vision.num_ctx": 8192})
    assert settings.effective("vision.num_ctx") == 8192
    # project layer wins over global for that project, global untouched elsewhere
    settings.put({"vision.num_ctx": 4096}, scope="/some/project")
    assert settings.effective("vision.num_ctx", project="/some/project") == 4096
    assert settings.effective("vision.num_ctx") == 8192
    assert settings.effective("vision.num_ctx", project="/other") == 8192


def test_reset_drops_override(tmp_db):
    settings = importlib.reload(importlib.import_module("settings"))
    config = importlib.import_module("config")
    settings.put({"vision.num_ctx": 8192})
    settings.reset("vision.num_ctx")
    assert settings.effective("vision.num_ctx") == config.OLLAMA_VISION_NUM_CTX


def test_unknown_key_rejected(tmp_db):
    settings = importlib.reload(importlib.import_module("settings"))
    with pytest.raises(settings.SettingError):
        settings.put({"vision.no_such_key": 1})
    with pytest.raises(settings.SettingError):
        settings.effective("nope.nope")


def test_int_range_validation(tmp_db):
    settings = importlib.reload(importlib.import_module("settings"))
    with pytest.raises(settings.SettingError):
        settings.put({"transcription.default_mode": 9})  # max 4
    with pytest.raises(settings.SettingError):
        settings.put({"transcription.default_mode": "abc"})


def test_validate_all_then_write_is_atomic(tmp_db):
    """A bad key in the batch must roll back the whole PUT (nothing stored)."""
    settings = importlib.reload(importlib.import_module("settings"))
    config = importlib.import_module("config")
    with pytest.raises(settings.SettingError):
        settings.put({"vision.num_ctx": 8192, "vision.bad": 1})
    # the good key must NOT have been written
    assert settings.effective("vision.num_ctx") == config.OLLAMA_VISION_NUM_CTX


def test_bool_coercion_round_trips(tmp_db):
    settings = importlib.reload(importlib.import_module("settings"))
    settings.put({"ingest.recursive": False})
    assert settings.effective("ingest.recursive") is False
    settings.put({"ingest.recursive": "true"})
    assert settings.effective("ingest.recursive") is True


# ---- API tests ----

def test_get_settings_returns_schema_with_sources(fastapi_client):
    r = fastapi_client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "global"
    keys = {s["key"]: s for s in body["settings"]}
    assert "vision.num_ctx" in keys
    assert keys["vision.num_ctx"]["source"] == "default"
    assert keys["vision.num_ctx"]["type"] == "int"


def test_put_settings_persists_and_reports_source(fastapi_client):
    r = fastapi_client.put(
        "/api/settings", json={"scope": "global", "values": {"vision.num_ctx": 2048}}
    )
    assert r.status_code == 200, r.text
    assert "vision.num_ctx" in r.json()["written"]
    g = fastapi_client.get("/api/settings").json()
    row = next(s for s in g["settings"] if s["key"] == "vision.num_ctx")
    assert row["value"] == 2048
    assert row["source"] == "global"


def test_put_invalid_value_is_422(fastapi_client):
    r = fastapi_client.put(
        "/api/settings", json={"scope": "global", "values": {"transcription.default_mode": 99}}
    )
    assert r.status_code == 422


def test_put_unknown_scope_is_400(fastapi_client):
    r = fastapi_client.put(
        "/api/settings",
        json={"scope": "/not/a/known/project", "values": {"vision.num_ctx": 2048}},
    )
    assert r.status_code == 400


def test_delete_setting_resets(fastapi_client):
    fastapi_client.put(
        "/api/settings", json={"scope": "global", "values": {"export.default_dir": "/tmp/x"}}
    )
    r = fastapi_client.delete("/api/settings/export.default_dir")
    assert r.status_code == 200
    g = fastapi_client.get("/api/settings").json()
    row = next(s for s in g["settings"] if s["key"] == "export.default_dir")
    assert row["source"] == "default"
