"""Phase 9.7 G5② — persisted settings overrides (module + API)."""
import importlib
import os

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


# ── one spelling per project ─────────────────────────────────────────────────
#
# The scope key is a filesystem path, and a path has several spellings. Before
# this, a row written under one and read under another simply missed — silently,
# which is the same shape as the project rows that were never read at all.

@pytest.fixture
def symlinkable(tmp_path):
    """Creating a symlink needs SeCreateSymbolicLinkPrivilege on Windows, which a
    runner may not have. Skip rather than fail: the thing under test is path
    canonicalisation, not the OS's symlink policy."""
    target = tmp_path / "_probe_target"
    target.mkdir()
    try:
        (tmp_path / "_probe").symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip("symlinks unavailable here: {0}".format(exc))
    (tmp_path / "_probe").unlink()


def test_a_symlinked_root_is_the_same_project(tmp_db, tmp_path, symlinkable):
    """The measured case. `config.PROJECT_ROOT` keeps whatever the env var said,
    while the registry stores its own resolved spelling — so the same library
    reaches the store as two different keys."""
    settings = importlib.reload(importlib.import_module("settings"))
    real = tmp_path / "lib"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    settings.put({"vision.num_ctx": 4096}, scope=str(link))

    assert settings.effective("vision.num_ctx", project=str(real)) == 4096
    assert settings.effective("vision.num_ctx", project=str(link)) == 4096


def test_the_same_directory_written_four_ways_is_one_row(tmp_db, tmp_path, monkeypatch):
    """Trailing slash, `.` hops, and `~` are spellings, not projects."""
    settings = importlib.reload(importlib.import_module("settings"))
    lib = tmp_path / "lib"
    lib.mkdir()
    # POSIX reads HOME; ntpath.expanduser reads USERPROFILE first and never HOME.
    # Setting only HOME made the `~/lib` spelling resolve somewhere else entirely
    # on Windows — a second row, which is the exact failure this test exists for.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    spellings = [str(lib), str(lib) + "/", str(tmp_path / "." / "lib"), "~/lib"]
    for i, spelling in enumerate(spellings):
        settings.put({"vision.num_ctx": 1024 + i}, scope=spelling)

    # last write wins because all four are the same row, not four rows
    assert settings.effective("vision.num_ctx", project=str(lib)) == 1024 + len(spellings) - 1
    with importlib.import_module("db").get_conn() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) c FROM settings WHERE scope <> 'global'").fetchone()["c"]
    assert rows == 1, "four spellings produced {0} rows".format(rows)


def test_reset_finds_the_row_whatever_spelling_it_is_given(tmp_db, tmp_path, symlinkable):
    """`reset` deleted by raw string, so resetting through a different spelling
    reported success and left the override in place."""
    settings = importlib.reload(importlib.import_module("settings"))
    real = tmp_path / "lib"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    settings.put({"vision.num_ctx": 4096}, scope=str(real))

    settings.reset("vision.num_ctx", scope=str(link))

    config = importlib.import_module("config")
    assert settings.effective("vision.num_ctx", project=str(real)) == config.OLLAMA_VISION_NUM_CTX


def test_global_is_not_a_path(tmp_db):
    """`canonical_scope` must leave the one non-path scope alone — resolving it
    would turn 'global' into a directory next to the process's cwd."""
    settings = importlib.reload(importlib.import_module("settings"))
    assert settings.canonical_scope("global") == "global"
    assert settings.canonical_scope(None) is None
    assert settings.canonical_scope("") == ""


def test_current_scope_follows_the_live_project_root(tmp_db, tmp_path, monkeypatch):
    """Read at call time, not frozen at import — a test that repoints
    PROJECT_ROOT at a temp library must not read the developer's own."""
    settings = importlib.reload(importlib.import_module("settings"))
    config = importlib.import_module("config")
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", lib)

    # Expressed with stdlib `normcase`, not with `canonical_scope` itself — the
    # latter would compare the function under test to itself. Windows CI caught
    # the first version, which hard-coded `str(lib.resolve())` and so asserted a
    # POSIX-shaped answer: there the canonical key is lower-cased.
    assert settings.current_scope() == os.path.normcase(str(lib.resolve()))


@pytest.mark.parametrize("root_is_link", [True, False])
def test_the_api_accepts_either_spelling_of_the_current_project(
        fastapi_client, tmp_path, monkeypatch, root_is_link, symlinkable):
    """A caller holding the registry's path for the very library this process is
    serving used to be told the scope was unknown.

    Both directions, because only one of them can catch a missing canonicalisation
    on the REQUEST side: when `tmp_path` is already canonical (it is, on macOS),
    sending the real path proves nothing — the raw string and its canonical form
    are identical. The first version of this test had only that direction and
    stayed green with the request-side call deleted.
    """
    config = importlib.import_module("config")
    real = tmp_path / "lib"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setattr(config, "PROJECT_ROOT", link if root_is_link else real)
    asked = str(real) if root_is_link else str(link)

    r = fastapi_client.put(
        "/api/settings", json={"scope": asked, "values": {"vision.num_ctx": 2048}})

    assert r.status_code == 200, r.text
    assert r.json()["scope"] == os.path.normcase(str(real.resolve()))


def test_an_unknown_path_is_still_refused(fastapi_client, tmp_path):
    """The guard must not have been widened into "any path that resolves"."""
    r = fastapi_client.put(
        "/api/settings",
        json={"scope": str(tmp_path / "not-a-project"), "values": {"vision.num_ctx": 2048}})

    assert r.status_code == 400


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive paths are a Windows property")
def test_case_only_differences_are_one_project_on_windows(tmp_db):
    """`C:\\Lib` and `c:\\lib` are one directory, so they must be one row.
    Runs for real on the `test-windows` CI leg, not just in principle."""
    settings = importlib.reload(importlib.import_module("settings"))
    assert settings.canonical_scope("C:\\Lib\\Media") == settings.canonical_scope("c:\\lib\\media")


@pytest.mark.skipif(os.name == "nt", reason="POSIX paths are case-sensitive")
def test_case_only_differences_are_two_projects_on_posix(tmp_db):
    """The decision this pins is the one NOT taken: the registry casefolds
    unconditionally, and copying that here would merge two genuinely different
    directories into one settings row on Linux. `normcase` asks the platform
    instead of assuming."""
    settings = importlib.reload(importlib.import_module("settings"))
    assert settings.canonical_scope("/srv/Media") != settings.canonical_scope("/srv/media")
