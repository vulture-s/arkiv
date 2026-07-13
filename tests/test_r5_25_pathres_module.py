"""R5-25 (round-5 #51): path-resolution helpers extracted to pathres.py.

The APIRouter split is blocked by ~50 cross-group helpers that every router
would otherwise pull via `from server import ...` — a router→server→router
import cycle (partially-initialized module → ImportError). The fix is to extract
the shared, server-state-free helpers into leaf service modules the routers and
server both import. pathres.py is the first: the non-leaking display-path +
media-path resolution used by ~every media/bins/export/stream route.

These tests pin two things a future refactor must not silently regress:
  1. pathres is a genuine leaf — it imports no server state (no `import server`),
     so it can sit below the routers in the import graph.
  2. server.py re-exports the names by identity, so existing call sites and
     tests referencing `server._resolve_media_path` etc. keep working unchanged.
Plus the leak-guard behaviour itself, so the move didn't alter semantics.
"""
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_NAMES = (
    "_basename_safe",
    "_looks_absolute",
    "_display_path",
    "_resolve_record",
    "_resolve_frame",
    "_resolve_media_path",
    "_proxy_ready",  # moved here in the proxy peel (R5-25 PR21); shared by /api/stream + proxy routes
)


# ── module boundary ──────────────────────────────────────────────────────────
def test_pathres_is_a_leaf_module():
    # pathres must not import server (directly or via `import server`), else it
    # can't sit below the routers in the import graph — the whole point of #51.
    src = (_ROOT / "pathres.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_server_reexports_pathres_by_identity():
    import server
    import pathres
    for name in _NAMES:
        assert getattr(server, name) is getattr(pathres, name), (
            "server.{0} must BE pathres.{0} (a re-export), so call sites and "
            "tests referencing server.{0} keep working".format(name)
        )


def test_pathres_functions_are_importable_standalone():
    import pathres
    for name in _NAMES:
        assert callable(getattr(pathres, name))


# ── leak-guard behaviour preserved by the move ───────────────────────────────
def test_basename_safe_normalises_both_separators():
    import pathres
    assert pathres._basename_safe("C:\\Users\\me\\proj\\clip.mov") == "clip.mov"
    assert pathres._basename_safe("/Volumes/home/proj/clip.mov") == "clip.mov"
    assert pathres._basename_safe("clip.mov") == "clip.mov"
    assert pathres._basename_safe("proj/") == "proj"       # trailing slash stripped
    assert pathres._basename_safe("") == ""


@pytest.mark.parametrize("p", [
    "/Volumes/home/x",   # POSIX absolute
    "\\\\host\\share",   # UNC
    "C:\\Users\\me",     # Windows drive + backslash
    "C:/Users/me",       # Windows drive + forward slash
])
def test_looks_absolute_true(p):
    import pathres
    assert pathres._looks_absolute(p) is True


@pytest.mark.parametrize("p", ["", "proj/clip.mov", "clip.mov", "a:relative"])
def test_looks_absolute_false(p):
    import pathres
    assert pathres._looks_absolute(p) is False


def test_display_path_reduces_out_of_root_absolute_to_basename(monkeypatch):
    # to_relative can't relativize a path outside PROJECT_ROOT → still absolute →
    # reduced to basename rather than leaking the operator's directory tree.
    import pathres
    monkeypatch.setattr(pathres.db, "to_relative", lambda p: p)  # identity → stays absolute
    assert pathres._display_path("/Volumes/home/secret/footage/clip.mov") == "clip.mov"


def test_display_path_returns_relative_untouched(monkeypatch):
    import pathres
    monkeypatch.setattr(pathres.db, "to_relative", lambda p: "proj/clip.mov")
    assert pathres._display_path("/anything") == "proj/clip.mov"


def test_display_path_passthrough_empty():
    import pathres
    assert pathres._display_path("") == ""
    assert pathres._display_path(None) is None


def test_resolve_record_and_frame_use_display_path(monkeypatch):
    import pathres
    monkeypatch.setattr(pathres, "_display_path", lambda p: "SAFE:" + p)
    rec = pathres._resolve_record({"path": "/abs/a", "thumbnail_path": "/abs/t"})
    assert rec == {"path": "SAFE:/abs/a", "thumbnail_path": "SAFE:/abs/t"}
    frame = pathres._resolve_frame({"thumbnail_path": "/abs/f"})
    assert frame == {"thumbnail_path": "SAFE:/abs/f"}
