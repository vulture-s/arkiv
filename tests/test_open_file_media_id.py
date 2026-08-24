""""Show in Finder" was a guaranteed 403 on any library indexed before the
relative-path migration.

The chain: `_display_path` refuses to leak the operator's directory tree, so a row
whose stored path is absolute and outside PROJECT_ROOT comes back as a bare
basename. The UI shows that string and hands it straight back to `/api/open-file`.
The server then asks `db.is_processed("clip.mp4")`, which matches nothing, and
answers 403 — on a file it has indexed, is showing on screen, and is streaming.

`media_id` names the row instead. The security direction is convergent, not
relaxed: an id can only name a row that already exists, and the path it yields is
the library's own stored path, so that branch has no attacker-controlled path in
it at all.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest


@pytest.fixture
def no_launch(monkeypatch):
    """Never actually open Finder/Explorer from a test."""
    calls = []
    fake = types.ModuleType("subprocess")
    fake.Popen = lambda *a, **k: calls.append(a)
    monkeypatch.setitem(sys.modules, "subprocess", fake)
    return calls


def _seed_legacy(db, sample_record, tmp_path):
    """A row stored with an absolute path outside PROJECT_ROOT — the pre-migration
    shape, and the one `_display_path` reduces to a basename."""
    src = tmp_path / "外接硬碟" / "案件A" / "clip.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x00")
    db.upsert(sample_record(path=str(src), filename="clip.mp4", ext=".mp4"))
    return 1, src


def test_the_basename_the_ui_receives_is_exactly_what_the_server_rejects(
    fastapi_client, server_module, sample_record, tmp_path
):
    """The premise of this whole fix, asserted rather than assumed."""
    db = importlib.import_module("db")
    import pathres
    mid, src = _seed_legacy(db, sample_record, tmp_path)

    shown = pathres._display_path(str(src))

    assert shown == "clip.mp4"
    assert db.is_processed(shown) is False
    r = fastapi_client.post("/api/open-file", json={"path": shown, "reveal": True})
    assert r.status_code == 403


def test_revealing_by_id_works_on_that_same_library(
    fastapi_client, server_module, sample_record, tmp_path, no_launch
):
    db = importlib.import_module("db")
    mid, _src = _seed_legacy(db, sample_record, tmp_path)

    r = fastapi_client.post("/api/open-file", json={"media_id": mid, "reveal": True})

    assert r.status_code == 200, r.text
    assert no_launch, "nothing was launched"


def test_an_unknown_id_is_refused(fastapi_client, server_module):
    r = fastapi_client.post("/api/open-file", json={"media_id": 4242, "reveal": True})
    assert r.status_code == 403


def test_the_path_branch_still_works_for_a_migrated_library(
    fastapi_client, server_module, sample_record, tmp_path, no_launch
):
    """Relative rows already round-tripped correctly; the id is an addition, not a
    replacement."""
    db = importlib.import_module("db")
    src = tmp_path / "clip2.mp4"
    src.write_bytes(b"\x00")
    db.upsert(sample_record(path=str(src), filename="clip2.mp4", ext=".mp4"))

    r = fastapi_client.post("/api/open-file", json={"path": str(src), "reveal": False})

    assert r.status_code == 200, r.text


def test_an_arbitrary_path_is_still_refused(fastapi_client, server_module, no_launch):
    """The id branch must not have loosened the path branch."""
    r = fastapi_client.post("/api/open-file",
                            json={"path": "/etc/passwd", "reveal": False})
    assert r.status_code == 403
    assert no_launch == []


def test_an_empty_body_is_refused_rather_than_opening_something(
    fastapi_client, server_module, no_launch
):
    """`path` became optional to make room for `media_id`; neither given must not
    fall through to resolving the empty string."""
    r = fastapi_client.post("/api/open-file", json={"reveal": False})
    assert r.status_code == 403
    assert no_launch == []


def test_an_indexed_row_whose_file_is_gone_is_a_404_not_a_403(
    fastapi_client, server_module, sample_record, tmp_path
):
    """Different causes, different answers: 403 means "not yours to open", 404
    means "the NAS is unplugged". Collapsing them sends the user hunting for a
    permissions problem that doesn't exist."""
    db = importlib.import_module("db")
    mid, src = _seed_legacy(db, sample_record, tmp_path)
    src.unlink()

    r = fastapi_client.post("/api/open-file", json={"media_id": mid, "reveal": True})

    assert r.status_code == 404


def test_the_ui_sends_the_id_when_it_has_one():
    """A server fix nobody reaches is not a fix. The Inspector's reveal button is
    the only caller."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "frontend" / "src"
    api = (root / "lib" / "api.js").read_text(encoding="utf-8")
    main = (root / "routes" / "MainLive.svelte").read_text(encoding="utf-8")

    assert "media_id: mediaId" in api
    assert "revealFile(inspPath, selected && selected.id)" in main
