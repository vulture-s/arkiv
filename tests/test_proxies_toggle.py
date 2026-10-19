"""Issue #558: a library can turn ingest Phase 3 (browser-playback proxies) off.

Proxies only help where the player cannot decode the original. On a Mac (the
desktop app's WKWebView, Safari) HEVC plays as-is, and Phase 3 was measured at
~5 h / ~9 GB on 871 phone clips — half the whole ingest — for nothing.

Three things have to hold, and each one is a way this could quietly go wrong:

  1. `auto` is the old behaviour exactly — the default must not move.
  2. `never` skips the transcode and NOTHING ELSE. Auto-embed runs after Phase 3;
     a skip that also skipped embedding would leave search empty.
  3. `build_proxies()` and `/api/stream`'s 409 are a pair. In a `never` library
     the 409 must not keep implying the next ingest will build the proxy — it
     won't — and `--regenerate-proxies`, the command the 409 names, has to work
     on a library that never had a single proxy.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import pathres

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def ing():
    return importlib.import_module("ingest")


# ── the setting ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,want", [
    (None, "auto"), ("auto", "auto"), ("never", "never"), (" NEVER ", "never"),
    ("bogus", "auto"), ("", "auto"),
])
def test_env_value_parses_and_a_typo_means_auto(tmp_path, raw, want):
    """A typo must not be what silently stops proxies being built."""
    env = dict(os.environ)
    env.pop("ARKIV_PROXIES", None)
    if raw is not None:
        env["ARKIV_PROXIES"] = raw
    env["ARKIV_PROJECT_ROOT"] = str(tmp_path)
    out = subprocess.run([sys.executable, "-c", "import config; print(config.PROXIES_MODE)"],
                         cwd=str(REPO), env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", check=True)
    assert out.stdout.strip().splitlines()[-1] == want


def test_the_setting_defaults_to_the_env_value(tmp_db, monkeypatch):
    config = importlib.import_module("config")
    settings = importlib.import_module("settings")
    assert settings.ingest_proxies() == "auto"
    monkeypatch.setattr(config, "PROXIES_MODE", "never")
    assert settings.ingest_proxies() == "never"


def test_the_library_setting_beats_the_env(tmp_db, monkeypatch):
    config = importlib.import_module("config")
    settings = importlib.import_module("settings")
    monkeypatch.setattr(config, "PROXIES_MODE", "never")
    settings.put({"ingest.proxies": "auto"}, scope=settings.current_scope())
    assert settings.ingest_proxies() == "auto"


def test_only_the_two_modes_are_writable(tmp_db):
    settings = importlib.import_module("settings")
    with pytest.raises(settings.SettingError):
        settings.put({"ingest.proxies": "always"})


def test_the_cli_beats_the_setting(tmp_db, ing):
    settings = importlib.import_module("settings")
    settings.put({"ingest.proxies": "never"}, scope=settings.current_scope())
    assert ing.proxies_mode(None) == "never"
    assert ing.proxies_mode("auto") == "auto"


# ── Phase 3 itself ───────────────────────────────────────────────────────────
def test_auto_runs_build_proxies_with_the_old_banner(ing, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ing, "build_proxies", lambda: calls.append(1) or (2, 3))
    assert ing.run_proxy_phase("auto") == (2, 3)
    assert calls == [1]
    out = capsys.readouterr().out
    assert out == "\n" + "─" * 60 + "\nPhase 3: Proxy generation for browser-incompatible codecs...\n"


def test_never_does_not_build_and_says_how_to_later(ing, monkeypatch, capsys):
    monkeypatch.setattr(ing, "build_proxies", lambda: pytest.fail("Phase 3 ran in a never library"))
    assert ing.run_proxy_phase("never") is None
    assert "--regenerate-proxies" in capsys.readouterr().out


# ── through main(): the flag reaches Phase 3, and embedding still happens ───
@pytest.fixture
def run_main(tmp_db, tmp_path, monkeypatch, ing, capsys):
    db = importlib.import_module("db")
    config = importlib.import_module("config")
    health = importlib.import_module("health")
    embed = importlib.import_module("embed")

    calls = {"proxies": 0, "embed": 0}

    def fake_build():
        calls["proxies"] += 1
        return (0, 0)

    def fake_embed(*a, **k):
        calls["embed"] += 1

    monkeypatch.setattr(health, "preflight_paths", lambda: (True, []))
    monkeypatch.setattr(ing.tr, "warm_up", lambda: None)
    monkeypatch.setattr(ing.tr, "warm_up_ollama", lambda: None)
    monkeypatch.setattr(ing, "build_proxies", fake_build)
    monkeypatch.setattr(embed, "run_embed", fake_embed)
    monkeypatch.setattr(ing, "_file_hash", lambda p: None)
    monkeypatch.setattr(ing, "detect_gpu", lambda: "test")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)

    def go(*extra):
        media = tmp_path / "CLIP"
        media.mkdir(exist_ok=True)
        (media / "a.mov").write_bytes(b"\x00" * 8)

        def fake_process(path, skip_vision, existing=None, refresh=False, file_hash=None):
            return {"path": db.to_relative(str(path)), "filename": path.name,
                    "ext": path.suffix.lower(), "duration_s": 1.0, "_frames": []}

        monkeypatch.setattr(ing, "process_file", fake_process)
        monkeypatch.setattr(sys, "argv", ["ingest.py", "--dir", str(media),
                                          "--skip-vision", *extra])
        try:
            ing.main()
        except SystemExit:
            pass
        capsys.readouterr()
        return dict(calls)
    return go


def test_no_flag_builds_proxies_and_embeds(run_main):
    assert run_main() == {"proxies": 1, "embed": 1}


@pytest.mark.parametrize("flag", [("--skip-proxies",), ("--proxies", "never")])
def test_skipping_proxies_still_embeds(run_main, flag):
    assert run_main(*flag) == {"proxies": 0, "embed": 1}


def test_a_never_library_skips_without_a_flag(run_main):
    settings = importlib.import_module("settings")
    settings.put({"ingest.proxies": "never"}, scope=settings.current_scope())
    assert run_main() == {"proxies": 0, "embed": 1}


def test_the_cli_can_turn_a_never_library_back_on(run_main):
    settings = importlib.import_module("settings")
    settings.put({"ingest.proxies": "never"}, scope=settings.current_scope())
    assert run_main("--proxies", "auto") == {"proxies": 1, "embed": 1}


def test_the_two_flags_are_exclusive(ing, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ingest.py", "--dir", "/tmp", "--skip-proxies",
                                      "--proxies", "auto"])
    with pytest.raises(SystemExit) as e:
        ing.main()
    assert e.value.code == 2


# ── --regenerate-proxies on a library that never had one ────────────────────
def test_regenerate_builds_proxies_for_a_library_that_never_had_any(
        tmp_db, tmp_path, sample_record, monkeypatch, ing, capsys):
    """The 409 in a never library points here. Before #558 this command only
    rebuilt files already in proxies/ and printed "No existing proxies to
    regenerate." — so on exactly the library it was recommended for, it did
    nothing."""
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    src = tmp_path / "cam" / "C0001.mov"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\x00" * 16)
    db.upsert(sample_record(path=str(src), filename=src.name, ext=".mov", codec="hevc"))
    mid = _id_of(db, src)
    monkeypatch.setattr(config, "PROXIES_DIR", tmp_path / "proxies")
    monkeypatch.setattr(config, "PROXIES_MODE", "never")  # an explicit request ignores it
    built = []
    monkeypatch.setattr(ing, "generate_proxy", lambda mid, path, **k: built.append(mid) and None)

    ing._regenerate_proxies()

    assert built == [mid]


# ── /api/stream ──────────────────────────────────────────────────────────────
def _id_of(db, src):
    """The server seeds a sample library into an empty DB, so the clip's id is
    looked up, never assumed to be 1."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM media").fetchall()
    for mid, path in rows:
        if db.resolve_path(path) == str(src):
            return mid
    raise AssertionError("seeded clip not found")


@pytest.fixture
def hevc_clip(fastapi_client, tmp_path, sample_record):
    db = importlib.import_module("db")
    src = tmp_path / "cam" / "A001.mov"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x00" * 16)
    db.upsert(sample_record(path=str(src), filename=src.name, ext=".mov",
                            duration_s=12.0, fps=30.0, has_audio=1, codec="hevc"))
    return _id_of(db, src)


def test_auto_library_409_is_unchanged(fastapi_client, server_module, hevc_clip, monkeypatch):
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 12.0)
    r = fastapi_client.get("/api/stream/%d" % hevc_clip)
    assert r.status_code == 409
    assert r.json() == {
        "need_proxy": True,
        "media_id": hevc_clip,
        "filename": "A001.mov",
        "reason": "browser-incompatible codec (HEVC/ProRes); proxy required for playback",
        "hint": "POST /api/proxy/build to queue proxy generation",
    }


def test_never_library_409_says_so(fastapi_client, server_module, hevc_clip, monkeypatch):
    settings = importlib.import_module("settings")
    misc = importlib.import_module("routers.misc")
    settings.put({"ingest.proxies": "never"}, scope=settings.current_scope())
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 12.0)

    r = fastapi_client.get("/api/stream/%d" % hevc_clip)

    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "proxies_disabled" == misc.PROXIES_DISABLED_CODE
    assert body["need_proxy"] is True, "the file still needs one; a per-clip build still works"
    assert "--regenerate-proxies" in body["message"]
    assert "--regenerate-proxies" in body["hint"]
    assert "HEVC/ProRes" in body["reason"], "the codec reason is kept"


def test_never_library_container_409_says_so_too(
        fastapi_client, server_module, tmp_path, sample_record, monkeypatch):
    """The container branch (AVCHD .mts) is the other 409 — both must carry it."""
    db = importlib.import_module("db")
    settings = importlib.import_module("settings")
    src = tmp_path / "AVCHD" / "00001.mts"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\x00" * 16)
    db.upsert(sample_record(path=str(src), filename=src.name, ext=".mts", codec="h264"))
    settings.put({"ingest.proxies": "never"}, scope=settings.current_scope())
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 12.0)

    r = fastapi_client.get("/api/stream/%d" % _id_of(db, src))

    assert r.status_code == 409
    assert r.json()["code"] == "proxies_disabled"


def test_never_library_still_serves_a_proxy_that_exists(
        fastapi_client, server_module, hevc_clip, tmp_path, monkeypatch):
    """A clip someone built a proxy for by hand plays — `never` stops building,
    it does not hide what is already there."""
    config = importlib.import_module("config")
    db = importlib.import_module("db")
    settings = importlib.import_module("settings")
    settings.put({"ingest.proxies": "never"}, scope=settings.current_scope())
    monkeypatch.setattr(pathres, "_probe_duration", lambda path: 12.0)
    monkeypatch.setattr(config, "PROXIES_DIR", tmp_path / "px")
    rec = db.get_record_by_id(hevc_clip)
    proxy = config.proxy_path_for(hevc_clip, db.resolve_path(rec["path"]))
    proxy.parent.mkdir(parents=True, exist_ok=True)
    proxy.write_bytes(b"\x00" * 64)
    monkeypatch.setattr(importlib.import_module("routers.misc"), "_proxy_ready",
                        lambda p: Path(p) == proxy)

    assert fastapi_client.get("/api/stream/%d" % hevc_clip).status_code == 200
