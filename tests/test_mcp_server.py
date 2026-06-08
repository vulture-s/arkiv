"""Unit tests for the Phase 14 arkiv MCP server.

All tests mock `db` / `vectordb` — no real DB, vector index, or Ollama. They
exercise the impl functions (the testable core) plus the path-safety red line
and the JSON tool wrappers.
"""
import json

import pytest

# The MCP SDK needs Python 3.10+ and is gated out of requirements.txt on 3.9, so
# importing mcp_server (-> `from mcp.server.fastmcp import FastMCP`) would fail at
# collection on a supported 3.9 env. Skip the whole module there (Codex P2).
pytest.importorskip("mcp")

import db
import vectordb as vdb
import mcp_server as m


# ── fakes ─────────────────────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        return _FakeCursor(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── _safe_path: the no-absolute-leak red line ─────────────────────────────────
def test_safe_path_none():
    assert m._safe_path(None) is None
    assert m._safe_path("") == ""


def test_safe_path_relative_passthrough(monkeypatch):
    monkeypatch.setattr(db, "to_relative", lambda p: "sub/clip.mp4")
    assert m._safe_path("/anything") == "sub/clip.mp4"


def test_safe_path_out_of_root_falls_back_to_basename(monkeypatch):
    # to_relative passes out-of-root absolute paths through unchanged; _safe_path
    # MUST NOT return that absolute path (it would leak the operator's tree).
    monkeypatch.setattr(db, "to_relative", lambda p: "/Users/secret/footage/x.mov")
    out = m._safe_path("/Users/secret/footage/x.mov")
    assert out == "x.mov"
    assert not out.startswith("/")


# ── search_media_impl ─────────────────────────────────────────────────────────
def test_search_empty_query_returns_empty():
    assert m.search_media_impl("") == []
    assert m.search_media_impl("   ") == []


def test_search_semantic_path(monkeypatch):
    monkeypatch.setattr(db, "to_relative", lambda p: "vids/a.mp4")
    monkeypatch.setattr(
        vdb, "search",
        lambda q, n_results=10: [{"media_id": 7, "score": 0.912345, "excerpt": "waffle"}],
    )
    monkeypatch.setattr(
        db, "get_record_by_id",
        lambda mid: {"id": 7, "filename": "a.mp4", "path": "/abs/vids/a.mp4",
                     "lang": "zh", "duration_s": 12.0, "transcript": "long..."},
    )
    monkeypatch.setattr(db, "get_tags", lambda mid: [{"name": "food"}, {"name": "indoor"}])

    out = m.search_media_impl("waffle", limit=5)
    assert len(out) == 1
    item = out[0]
    assert item["id"] == 7
    assert item["score"] == 0.9123          # rounded to 4 dp
    assert item["excerpt"] == "waffle"
    assert item["tags"] == ["food", "indoor"]
    assert item["path"] == "vids/a.mp4"     # sanitized, relative
    assert "transcript" not in item          # lightweight — no heavy fields


def test_search_dedups_repeated_media_id(monkeypatch):
    monkeypatch.setattr(db, "to_relative", lambda p: "a.mp4")
    monkeypatch.setattr(
        vdb, "search",
        lambda q, n_results=10: [{"media_id": 1, "score": 0.9}, {"media_id": 1, "score": 0.8}],
    )
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: {"id": 1, "filename": "a.mp4"})
    monkeypatch.setattr(db, "get_tags", lambda mid: [])
    out = m.search_media_impl("x")
    assert len(out) == 1


def test_search_falls_back_to_sql_when_vector_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("chroma dim mismatch")

    monkeypatch.setattr(vdb, "search", _boom)
    monkeypatch.setattr(db, "to_relative", lambda p: "b.mp4")
    monkeypatch.setattr(db, "get_tags", lambda mid: [{"name": "x"}])
    rows = [{"id": 3, "filename": "b.mp4", "path": "/abs/b.mp4", "transcript": "hi"}]
    monkeypatch.setattr(db, "get_conn", lambda: _FakeConn(rows))

    out = m.search_media_impl("hi", limit=10)
    assert len(out) == 1
    assert out[0]["id"] == 3
    assert out[0]["path"] == "b.mp4"
    assert out[0]["tags"] == ["x"]


def test_search_respects_limit(monkeypatch):
    monkeypatch.setattr(db, "to_relative", lambda p: "x.mp4")
    monkeypatch.setattr(
        vdb, "search",
        lambda q, n_results=10: [{"media_id": i, "score": 0.5} for i in range(50)],
    )
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: {"id": mid, "filename": "x.mp4"})
    monkeypatch.setattr(db, "get_tags", lambda mid: [])
    out = m.search_media_impl("x", limit=3)
    assert len(out) == 3


# ── get_media_impl / get_transcript_impl ──────────────────────────────────────
def test_get_media_found(monkeypatch):
    monkeypatch.setattr(db, "to_relative", lambda p: "vids/a.mp4")
    monkeypatch.setattr(
        db, "get_record_by_id",
        lambda mid: {"id": 9, "filename": "a.mp4", "path": "/abs/vids/a.mp4",
                     "thumbnail_path": "/abs/t/9.jpg", "transcript": "full text"},
    )
    monkeypatch.setattr(db, "get_tags", lambda mid: [{"name": "k"}])
    out = m.get_media_impl(9)
    assert out["id"] == 9
    assert out["path"] == "vids/a.mp4"
    assert out["thumbnail_path"] == "vids/a.mp4"   # both run through _safe_path
    assert out["transcript"] == "full text"         # full record keeps transcript
    assert out["tags"] == ["k"]


def test_get_media_not_found(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: None)
    assert m.get_media_impl(123) is None


def test_get_transcript(monkeypatch):
    monkeypatch.setattr(
        db, "get_record_by_id",
        lambda mid: {"id": 2, "filename": "c.mp4", "lang": "ja", "transcript": "テスト"},
    )
    out = m.get_transcript_impl(2)
    assert out == {"id": 2, "filename": "c.mp4", "lang": "ja", "transcript": "テスト"}


def test_get_transcript_not_found(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: None)
    assert m.get_transcript_impl(404) is None


# ── pass-through impls ────────────────────────────────────────────────────────
def test_list_recent_orders_descending(monkeypatch):
    """Codex P2: 'most recent' must query id DESC, not reuse the ASC paginator."""
    monkeypatch.setattr(db, "to_relative", lambda p: "r.mp4")
    captured = {}

    class _CapCursor:
        def fetchall(self):
            return [{"id": 9, "filename": "r.mp4", "path": "/abs/r.mp4"}]

    class _CapConn:
        def execute(self, sql, params=None):
            captured["sql"] = sql
            return _CapCursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(db, "get_conn", lambda: _CapConn())
    out = m.list_recent_impl(5)
    assert out[0]["path"] == "r.mp4"
    assert "DESC" in captured["sql"].upper()  # newest-first


def test_safe_path_windows_and_unc_absolute(monkeypatch):
    """Codex P1: os.path.isabs misses Windows/UNC on POSIX — must still basename."""
    monkeypatch.setattr(db, "to_relative", lambda p: p)  # passthrough (out-of-root)
    assert m._safe_path("C:\\Users\\me\\footage\\x.mov") == "x.mov"
    assert m._safe_path("\\\\nas\\share\\clip.mov") == "clip.mov"
    # forward-slash drive-like dir is a POSIX relative path — preserved
    assert m._safe_path("C:/camera/clip.mov") == "C:/camera/clip.mov"


def test_library_stats(monkeypatch):
    monkeypatch.setattr(db, "get_stats", lambda: {"total": 42})
    assert m.library_stats_impl() == {"total": 42}


def test_list_tags(monkeypatch):
    monkeypatch.setattr(db, "get_top_tags", lambda limit: [{"name": "a", "count": 3}])
    assert m.list_tags_impl(10) == [{"name": "a", "count": 3}]


# ── MCP tool wrappers produce valid JSON ──────────────────────────────────────
def test_tool_wrappers_return_valid_json(monkeypatch):
    monkeypatch.setattr(db, "get_stats", lambda: {"total": 1, "langs": {"zh": 1}})
    parsed = json.loads(m.library_stats())
    assert parsed["total"] == 1


def test_tool_json_keeps_cjk_readable(monkeypatch):
    monkeypatch.setattr(
        db, "get_record_by_id",
        lambda mid: {"id": 1, "filename": "明燒肉.mp4", "lang": "zh", "transcript": "中文"},
    )
    raw = m.get_transcript(1)
    assert "中文" in raw                      # ensure_ascii=False
    assert json.loads(raw)["transcript"] == "中文"


# ── tools are registered with FastMCP ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_tools_registered():
    tools = await m.mcp.list_tools()
    names = {t.name for t in tools}
    assert {"search_media", "get_media", "get_transcript",
            "list_recent", "library_stats", "list_tags"} <= names
