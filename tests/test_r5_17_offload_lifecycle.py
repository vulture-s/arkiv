"""R5-17 (round-5 #45/#19): offload lifecycle hardening — backend half.

  * /api/offload gives each source card its OWN resumable state file and always
    --resume's it (no copy-from-zero on retry; no clobber between concurrent cards),
  * a per-source single-flight rejects a second concurrent run over the same card
    with 409 (the two would otherwise tear the shared state JSON),
  * offload._save_state writes atomically (tmp + os.replace) so a crash mid-write
    can't corrupt the resume state.

The AbortController / Stop-button half (#45 UI) is frontend-only — verified via
`vite build` (the CI frontend-build gate), the repo has no JS unit harness.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest


def _state_dir(monkeypatch, tmp_path):
    config = importlib.import_module("config")
    d = tmp_path / "arkiv-state" / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "THUMBNAILS_DIR", d)
    return d.parent  # the endpoint uses THUMBNAILS_DIR.parent as state_cwd


def _expected_state_path(state_cwd, src):
    h = hashlib.sha1(str(Path(src).expanduser().resolve()).encode("utf-8")).hexdigest()[:16]
    return state_cwd / "offload-state-{0}.json".format(h)


def test_endpoint_writes_per_source_resumable_state(fastapi_client, tmp_path, monkeypatch):
    state_cwd = _state_dir(monkeypatch, tmp_path)
    card = tmp_path / "cardA"; card.mkdir()
    (card / "A.MP4").write_bytes(b"aaa")
    dst = tmp_path / "dstA"; dst.mkdir()
    r = fastapi_client.post("/api/offload", json={"src": str(card), "dst": [str(dst)]})
    assert r.status_code == 200
    _ = r.text  # drain the stream so the subprocess completes
    sp = _expected_state_path(state_cwd, card)
    assert sp.exists(), "endpoint must write a per-source state file it can resume"
    state = json.loads(sp.read_text(encoding="utf-8"))
    assert "files" in state and Path(state["source"]).name == "cardA"
    assert not sp.with_name(sp.name + ".tmp").exists(), "no half-written temp left behind"


def test_distinct_sources_get_distinct_state_files(fastapi_client, tmp_path, monkeypatch):
    state_cwd = _state_dir(monkeypatch, tmp_path)
    paths = []
    for name in ("card1", "card2"):
        card = tmp_path / name; card.mkdir()
        (card / "X.MP4").write_bytes(b"x")
        dst = tmp_path / (name + "-dst"); dst.mkdir()
        r = fastapi_client.post("/api/offload", json={"src": str(card), "dst": [str(dst)]})
        assert r.status_code == 200
        _ = r.text
        paths.append(_expected_state_path(state_cwd, card))
    assert paths[0] != paths[1]
    assert paths[0].exists() and paths[1].exists(), "two cards must not share one state file"


def test_concurrent_same_source_is_409(fastapi_client, server_module, tmp_path, monkeypatch):
    _state_dir(monkeypatch, tmp_path)
    card = tmp_path / "busycard"; card.mkdir()
    (card / "A.MP4").write_bytes(b"a")
    dst = tmp_path / "busydst"; dst.mkdir()
    key = str(card.resolve())
    assert server_module._acquire_offload_slot(key)  # simulate a run already in flight
    try:
        r = fastapi_client.post("/api/offload", json={"src": str(card), "dst": [str(dst)]})
        assert r.status_code == 409
    finally:
        server_module._release_offload_slot(key)
    # slot freed → a fresh run over the same card is accepted again
    r2 = fastapi_client.post("/api/offload", json={"src": str(card), "dst": [str(dst)]})
    assert r2.status_code == 200
    _ = r2.text


def test_offload_slot_guard_semantics(server_module):
    k = "/tmp/some/card"
    assert server_module._acquire_offload_slot(k) is True
    assert server_module._acquire_offload_slot(k) is False       # same key blocked
    assert server_module._acquire_offload_slot(k + "2") is True  # a different card is fine
    server_module._release_offload_slot(k)
    server_module._release_offload_slot(k + "2")
    assert server_module._acquire_offload_slot(k) is True        # released → reusable
    server_module._release_offload_slot(k)


def test_save_state_is_atomic(tmp_path, monkeypatch):
    offload = importlib.import_module("offload")
    sp = tmp_path / "offload-state.json"
    offload._save_state(sp, {"v": 1})
    assert json.loads(sp.read_text(encoding="utf-8")) == {"v": 1}
    assert not sp.with_name(sp.name + ".tmp").exists(), "no temp leftover after a clean save"

    # A crash DURING os.replace must leave the previous good state intact — the
    # write goes to a temp file and never truncates the real one in place.
    def boom(src, dst):
        raise RuntimeError("simulated crash mid-replace")
    monkeypatch.setattr(offload.os, "replace", boom)
    with pytest.raises(RuntimeError):
        offload._save_state(sp, {"v": 2})
    monkeypatch.undo()
    assert json.loads(sp.read_text(encoding="utf-8")) == {"v": 1}, "old state must survive a failed write"
