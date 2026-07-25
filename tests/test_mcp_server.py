"""Unit tests for the Phase 14 arkiv MCP server.

All tests mock `db` / `vectordb` — no real DB, vector index, or Ollama. They
exercise the impl functions (the testable core) plus the path-safety red line
and the JSON tool wrappers.
"""
import json
import pathlib
import re

import pytest

# The MCP SDK needs Python 3.10+ and is gated out of requirements.txt on 3.9, so
# importing mcp_server (-> `from mcp.server.fastmcp import FastMCP`) would fail at
# collection on a supported 3.9 env. Skip the whole module there (Codex P2).
pytest.importorskip("mcp")

import db
import vectordb as vdb
import mcp_server as m


@pytest.fixture(autouse=True)
def _readiness_ok(monkeypatch):
    """These unit tests drive the tool wrappers with individual db functions
    mocked; they do not seed a real DB. The wrappers' readiness guard probes
    db.get_conn() for the `media` table, which those mocks don't cover — so
    bypass it here (monkeypatch auto-resets). Readiness itself is exercised over
    the real protocol in tests/test_mcp_e2e.py, which is where it belongs."""
    monkeypatch.setattr(m, "_DB_READY", True)


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
    # The four original keys are unchanged — pre-existing consumers keep working.
    # duration_s/segments/has_words joined them when timecodes were added; a clip
    # with no segment timing (this one) still reports segments: [].
    assert out == {
        "id": 2, "filename": "c.mp4", "lang": "ja", "transcript": "テスト",
        "duration_s": None, "segments": [], "has_words": False,
    }


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
    # `C:/` is a Windows absolute too, and must basename like every other absolute.
    # This asserted the exact opposite until 2026-07-16: Codex round-1 read `C:/x`
    # as a POSIX relative path under a drive-letter-named dir and preserved it.
    # Round-2 (fc35b8f, four hours later) overruled that — "a Unix media dir
    # literally named 'C:' is pathological… no-leak wins" — but only patched
    # server.py, so this surface kept leaking `C:/…` whole. See _safe_path's
    # docstring; the guard is now shared with the HTTP API rather than copied.
    assert m._safe_path("C:/camera/clip.mov") == "clip.mov"


def test_safe_path_is_the_http_guard_not_a_copy(monkeypatch):
    """Anti-fork pin: the MCP leak guard IS pathres._display_path, not a twin.

    The 38-day `C:/` leak happened because this module had to inline its own copy
    (the logic lived in server.py, which mcp_server must not import) and the copy
    missed a later fix to the original. pathres exists now, so there is no reason
    for a second implementation — and an "inline it to avoid the import" change
    would silently reopen exactly that drift. Assert parity across the whole
    input space, not just the case that broke.
    """
    import pathres

    monkeypatch.setattr(db, "to_relative", lambda p: p)  # passthrough (out-of-root)
    for p in (
        "/abs/posix/x.mov",
        "C:\\Users\\me\\x.mov",
        "C:/Users/me/x.mov",
        "\\\\nas\\share\\x.mov",
        "footage/clip.mov",
        "clip.mov",
        "",
        None,
    ):
        assert m._safe_path(p) == pathres._display_path(p), p


def test_mcp_carries_no_second_leak_guard():
    """Source-level twin of the test above — catches a re-inlined copy even if it
    is behaviourally identical *on the day it lands* (which the 6/08 copy was).
    Same idiom as the R5-25 leaf tests: read the source, never import-and-inspect.
    """
    src = (pathlib.Path(__file__).resolve().parent.parent / "mcp_server.py").read_text(
        encoding="utf-8"
    )
    assert not re.search(r"^def _looks_absolute\b", src, re.M)


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


# ── get_transcript timecodes ──────────────────────────────────────────────────
# The shapes below are what the backends ACTUALLY write, not what
# transcribe.py:223's docstring says. They disagree with each other, which is the
# whole reason the impl projects rather than passes through.
_MLX_SEGMENTS = json.dumps([  # transcribe._transcribe_mlx → mlx_whisper native.
    # These 10 keys are verified, not guessed: mlx-whisper large-v3-turbo run
    # against 8s of real footage returned exactly
    #   ['avg_logprob','compression_ratio','end','id','no_speech_prob','seek',
    #    'start','temperature','text','tokens']
    {"id": 0, "seek": 0, "start": 0.0, "end": 2.4, "text": "第一句",
     "tokens": [50364, 2503, 41200, 50484], "temperature": 0.0,
     "avg_logprob": -0.31, "compression_ratio": 1.2, "no_speech_prob": 0.01},
    {"id": 1, "seek": 240, "start": 2.4, "end": 5.0, "text": "第二句",
     "tokens": [50484, 17155, 50614], "temperature": 0.0,
     "avg_logprob": -0.28, "compression_ratio": 1.1, "no_speech_prob": 0.02},
], ensure_ascii=False)

_FW_SEGMENTS = json.dumps([  # transcribe._transcribe_faster_whisper
    {"text": "第一句", "start": 0.0, "end": 2.4, "no_speech_prob": 0.01,
     "avg_logprob": -0.31, "compression_ratio": 1.2},
], ensure_ascii=False)

_WORDS = json.dumps(
    [{"word": "第一", "start": 0.1, "end": 0.4, "score": 0.98},
     {"word": "句", "start": 0.4, "end": 0.6, "score": 0.97}],
    ensure_ascii=False,
)


def _rec(**over):
    base = {"id": 1, "filename": "a.mp4", "lang": "zh", "transcript": "第一句第二句",
            "duration_s": 5.0, "segments_json": _MLX_SEGMENTS, "words_json": _WORDS}
    base.update(over)
    return base


def test_get_transcript_parses_segments_into_objects(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: _rec())
    out = m.get_transcript_impl(1)
    assert out["segments"] == [
        {"start": 0.0, "end": 2.4, "text": "第一句"},
        {"start": 2.4, "end": 5.0, "text": "第二句"},
    ]
    assert out["duration_s"] == 5.0
    assert isinstance(out["segments"][0]["start"], float)   # number, not a string


def test_get_transcript_strips_decoder_internals(monkeypatch):
    """mlx-whisper (every Mac ingest) stores `tokens` — the raw token ids — plus
    seek/id/temperature/logprob internals. An agent needs none of it. Measured on
    8s of real footage: 1128 bytes verbatim vs 213 projected, an 81% cut, and it
    scales linearly with clip length."""
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: _rec())
    out = m.get_transcript_impl(1)
    for seg in out["segments"]:
        assert set(seg) == {"start", "end", "text"}
    dumped = json.dumps(out)
    for internal in ("tokens", "seek", "temperature", "avg_logprob",
                     "compression_ratio", "no_speech_prob"):
        assert internal not in dumped


def test_get_transcript_shape_is_backend_independent(monkeypatch):
    """mlx-whisper and faster-whisper write different segment dicts, so without
    the projection the payload shape would depend on which machine ingested the
    clip. Same input, same output, either way."""
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: _rec(segments_json=_MLX_SEGMENTS))
    mlx = m.get_transcript_impl(1)["segments"][0]
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: _rec(segments_json=_FW_SEGMENTS))
    fw = m.get_transcript_impl(1)["segments"][0]
    assert mlx == fw == {"start": 0.0, "end": 2.4, "text": "第一句"}


def test_get_transcript_omits_words_by_default(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: _rec())
    out = m.get_transcript_impl(1)
    assert "words" not in out            # omitted, not null — you don't pay for it
    assert out["has_words"] is True      # …but you're told it exists


def test_get_transcript_include_words(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: _rec())
    out = m.get_transcript_impl(1, include_words=True)
    assert out["words"] == [
        {"word": "第一", "start": 0.1, "end": 0.4, "score": 0.98},
        {"word": "句", "start": 0.4, "end": 0.6, "score": 0.97},
    ]
    assert out["words_truncated"] is False


def test_get_transcript_truncates_words(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: _rec())
    out = m.get_transcript_impl(1, include_words=True, max_words=1)
    assert len(out["words"]) == 1
    assert out["words_truncated"] is True


def test_get_transcript_has_words_false_without_word_timing(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: _rec(words_json=None))
    out = m.get_transcript_impl(1)
    assert out["has_words"] is False
    assert m.get_transcript_impl(1, include_words=True)["words"] == []


@pytest.mark.parametrize("bad", [
    "{not json",           # corrupt
    '{"a": 1}',            # valid JSON, wrong type (an object, not a list)
    "[1, 2, 3]",           # a list, but not of dicts
    "null",
    "",
])
def test_get_transcript_degrades_on_malformed_json(monkeypatch, bad):
    """A corrupt column must not take the tool down — mirrors export.py:134-141."""
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: _rec(segments_json=bad, words_json=bad))
    out = m.get_transcript_impl(1, include_words=True)
    assert out["segments"] == []
    assert out["words"] == []
    assert out["has_words"] is False
    assert out["transcript"] == "第一句第二句"   # flat text still served


def test_get_transcript_pre_segment_era_clip(monkeypatch):
    """Real case, not hypothetical: the live library's 37 transcripts predate
    Phase 9.4, so segments_json is NULL on every one. They must still answer."""
    monkeypatch.setattr(
        db, "get_record_by_id",
        lambda mid: {"id": 1, "filename": "old.mp4", "lang": "zh",
                     "transcript": "舊的逐字稿", "duration_s": 31.0},
    )
    out = m.get_transcript_impl(1)
    assert out["segments"] == []
    assert out["has_words"] is False
    assert out["transcript"] == "舊的逐字稿"


def test_get_transcript_tool_passes_include_words_through(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: _rec())
    assert "words" not in json.loads(m.get_transcript(1))
    assert len(json.loads(m.get_transcript(1, include_words=True))["words"]) == 2


# ── get_scenes ────────────────────────────────────────────────────────────────
def _fake_frames(**over):
    frame = {
        "id": 7, "media_id": 1, "frame_index": 0, "timestamp_s": 0.0,
        "description": "手持走入店內", "content_type": "Establishing",
        "focus_score": 3, "atmosphere": "紀實", "energy": "中",
        "edit_position": "開場", "edit_reason": "建立場景",
        "stability": "穩定", "exposure": "normal", "audio_quality": "清晰",
        "thumbnail_path": "thumbnails/a.jpg",
    }
    frame.update(over)
    return [frame]


def test_get_scenes_impl_shape(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: {"id": 1, "duration_s": 30.0})
    monkeypatch.setattr(db, "get_frames", lambda mid: _fake_frames())
    monkeypatch.setattr(db, "to_relative", lambda p: p)

    out = m.get_scenes_impl(1)

    assert out["media_id"] == 1
    assert out["media_duration_s"] == 30.0
    assert out["total"] == 1
    scene = out["scenes"][0]
    assert scene["start_s"] == 0.0
    assert scene["end_s"] == 30.0        # only frame → closes at media duration
    assert scene["duration_s"] == 30.0
    assert scene["description"] == "手持走入店內"
    assert scene["keyframe_path"] == "thumbnails/a.jpg"


def test_get_scenes_impl_unknown_id_is_none_not_empty(monkeypatch):
    # The distinction matters: db.get_frames on a bogus id also returns [], so
    # without the rec lookup "no such media" and "no vision analysis yet" would
    # give the agent the same answer.
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: None)
    assert m.get_scenes_impl(999999) is None


def test_get_scenes_no_frames_is_empty_not_none(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: {"id": 1, "duration_s": 30.0})
    monkeypatch.setattr(db, "get_frames", lambda mid: [])
    out = m.get_scenes_impl(1)
    assert out is not None
    assert out["total"] == 0
    assert out["scenes"] == []


@pytest.mark.parametrize("stored,expected", [
    ("/Users/secret/footage/x.jpg", "x.jpg"),        # POSIX absolute
    ("C:\\Users\\me\\x.jpg", "x.jpg"),               # Windows drive, backslash
    ("C:/Users/me/x.jpg", "x.jpg"),                  # Windows drive, forward slash (#182)
    ("\\\\nas\\share\\x.jpg", "x.jpg"),              # UNC
    ("thumbnails/x.jpg", "thumbnails/x.jpg"),        # in-root relative — preserved
])
def test_get_scenes_keyframe_path_never_leaks_absolute(monkeypatch, stored, expected):
    """RED LINE. db.get_frames is SELECT *, so thumbnail_path is raw and absolute
    for out-of-root legacy rows — handing it out unfiltered is exactly the leak
    #182 fixed on the other tools. Scenes must go through the same guard."""
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: {"id": 1, "duration_s": 30.0})
    monkeypatch.setattr(db, "get_frames", lambda mid: _fake_frames(thumbnail_path=stored))
    monkeypatch.setattr(db, "to_relative", lambda p: p)  # out-of-root passthrough

    out = m.get_scenes_impl(1)

    assert out["scenes"][0]["keyframe_path"] == expected


def test_get_scenes_omits_keyframe_path_without_a_thumbnail(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: {"id": 1, "duration_s": 30.0})
    monkeypatch.setattr(db, "get_frames", lambda mid: _fake_frames(thumbnail_path=None))
    out = m.get_scenes_impl(1)
    assert "keyframe_path" not in out["scenes"][0]


def test_get_scenes_never_emits_a_url_or_raw_frame_columns(monkeypatch):
    """keyframe_url is an HTTP concept (no host, no session over stdio), and the
    raw frame's id/media_id/thumbnail_path are noise the agent must not see."""
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: {"id": 1, "duration_s": 30.0})
    monkeypatch.setattr(db, "get_frames", lambda mid: _fake_frames())
    monkeypatch.setattr(db, "to_relative", lambda p: p)

    scene = m.get_scenes_impl(1)["scenes"][0]

    assert "keyframe_url" not in scene
    assert "thumbnail_path" not in scene
    assert "media_id" not in scene
    assert not any(isinstance(v, str) and v.startswith("/thumbnails/") for v in scene.values())


def test_get_scenes_matches_the_http_shape_except_the_keyframe_key(monkeypatch):
    """Anti-fork pin, and the reason scenes.py exists at all.

    The MCP and HTTP scene shapes must differ in EXACTLY one way: keyframe_path
    vs keyframe_url. Everything else — envelope, boundaries, key order, every
    vision field — comes from the shared derivation. mcp_server already forked a
    same-looking copy of the leak guard once and drifted for 38 days (#182); this
    fails the moment someone re-derives scenes on either side.
    """
    import scenes as scenes_mod

    monkeypatch.setattr(db, "to_relative", lambda p: p)
    rec = {"id": 1, "duration_s": 30.0}
    frames = _fake_frames()

    http = scenes_mod._scenes_payload(1, rec, frames)
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: rec)
    monkeypatch.setattr(db, "get_frames", lambda mid: frames)
    mcp_out = m.get_scenes_impl(1)

    assert list(http.keys()) == list(mcp_out.keys())
    assert http["media_duration_s"] == mcp_out["media_duration_s"]
    assert http["total"] == mcp_out["total"]

    for h, c in zip(http["scenes"], mcp_out["scenes"]):
        assert list(h.keys())[:-1] == list(c.keys())[:-1]   # same keys, same order
        assert list(h.keys())[-1] == "keyframe_url"          # …differing only in
        assert list(c.keys())[-1] == "keyframe_path"         #    the last one
        for key in h:
            if key != "keyframe_url":
                assert h[key] == c[key], key


def test_get_scenes_tool_returns_valid_json(monkeypatch):
    monkeypatch.setattr(db, "get_record_by_id", lambda mid: {"id": 1, "duration_s": 30.0})
    monkeypatch.setattr(db, "get_frames", lambda mid: _fake_frames())
    monkeypatch.setattr(db, "to_relative", lambda p: p)

    raw = m.get_scenes(1)

    assert "手持走入店內" in raw                       # ensure_ascii=False
    assert json.loads(raw)["scenes"][0]["start_s"] == 0.0


# ── tools are registered with FastMCP ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_tools_registered():
    tools = await m.mcp.list_tools()
    names = {t.name for t in tools}
    assert {"search_media", "get_media", "get_transcript",
            "list_recent", "library_stats", "list_tags",
            "get_scenes"} <= names
