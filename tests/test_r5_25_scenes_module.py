"""R5-25 (round-5 #51) follow-on: scene assembly extracted to scenes.py.

The per-scene derivation is needed by both routers/media.py and mcp_server.py,
which must not import each other — mcp_server deliberately avoids `server`, since
that pulls in the whole FastAPI app. Extracting the derivation to a leaf both
import is what keeps the HTTP and MCP scene shapes from forking.

That risk is not hypothetical. mcp_server had to inline its own copy of the
path-leak guard for exactly this reason (nowhere to share from), and the copy
silently missed a fix to the original for 38 days (#182). These tests pin what a
future refactor must not quietly regress:

  1. scenes is a genuine leaf — no `server` import, so it sits below the routers.
  2. scenes is fastapi-free — mcp_server imports it over stdio, and dragging in
     HTTPException would pull the FastAPI app into the MCP server. The 404 stays
     in routers/media.py; this module returns data and never raises.
  3. The derivation semantics the move had to preserve.

The wire format itself is pinned separately and more strictly by
tests/test_scenes_contract.py (byte-identity golden, authored pre-extraction).

Note there is deliberately no `server` re-export to assert here, unlike the other
R5-25 leaves: nothing referenced `server._build_scenes`, so adding one would
create an obligation with zero call sites.
"""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_NAMES = (
    "_build_scenes",
    "_media_duration_s",
    "_keyframe_url",
    "_scenes_payload",
)


# ── module boundary ──────────────────────────────────────────────────────────
def test_scenes_is_a_leaf_module():
    # No `import server`, or it can't sit below the routers in the import graph.
    src = (_ROOT / "scenes.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+server\b", src, re.M)
    assert not re.search(r"^\s*from\s+server\b", src, re.M)


def test_scenes_does_not_import_fastapi():
    # mcp_server imports this over stdio and is deliberately fastapi-free.
    # reqopts/webguard import HTTPException; scenes must stay in the stdlib tier.
    src = (_ROOT / "scenes.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\s+fastapi\b", src, re.M)
    assert not re.search(r"^\s*from\s+fastapi\b", src, re.M)


def test_scenes_has_no_future_annotations_import():
    # The 3.9 floor has no linter behind it — the ONLY thing that catches a
    # PEP604 annotation in a leaf is the 3.9 CI leg blowing up at import time,
    # and that works precisely because annotations are eagerly evaluated here.
    # `from __future__ import annotations` would defer them and silently disable
    # the catch, shipping a break that only surfaces on the NAS.
    src = (_ROOT / "scenes.py").read_text(encoding="utf-8")
    assert "from __future__ import annotations" not in src


def test_scenes_functions_are_importable_standalone():
    import scenes
    for name in _NAMES:
        assert callable(getattr(scenes, name))


# ── derivation semantics preserved by the move ───────────────────────────────
def test_last_scene_ends_at_media_duration():
    import scenes
    out = scenes._build_scenes(
        [{"frame_index": 0, "timestamp_s": 0.0}, {"frame_index": 1, "timestamp_s": 5.0}],
        30.0,
    )
    assert out[0]["end_s"] == 5.0        # next frame's start
    assert out[1]["end_s"] == 30.0       # last → media duration


def test_end_before_start_is_clamped():
    # db.get_frames is ORDER BY frame_index; nothing enforces rising timestamps,
    # so an out-of-order pair must clamp rather than emit a negative span.
    import scenes
    out = scenes._build_scenes(
        [{"frame_index": 0, "timestamp_s": 9.0}, {"frame_index": 1, "timestamp_s": 2.0}],
        30.0,
    )
    assert out[0]["end_s"] == 9.0
    assert out[0]["duration_s"] == 0.0


def test_build_scenes_carries_no_path_field():
    # The whole reason the split is drawn here: keyframe_url is an HTTP-server
    # concept, and db.get_frames is SELECT * so every frame dict carries a raw
    # thumbnail_path — absolute for out-of-root legacy rows. The shared core must
    # never splat one into its output.
    import scenes
    out = scenes._build_scenes(
        [{"frame_index": 0, "timestamp_s": 0.0,
          "thumbnail_path": "/Users/someone/private/x.jpg",
          "id": 99, "media_id": 1}],
        10.0,
    )
    assert "keyframe_url" not in out[0]
    assert "thumbnail_path" not in out[0]
    assert not any("private" in str(v) for v in out[0].values())


def test_build_scenes_emits_absent_vision_fields_as_none():
    # Consumers rely on key presence, not value presence — a partial ingest must
    # still carry all 9 vision keys.
    import scenes
    out = scenes._build_scenes([{"frame_index": 0, "timestamp_s": 0.0}], 10.0)
    for key in ("content_type", "focus_score", "atmosphere", "energy",
                "edit_position", "edit_reason", "stability", "exposure",
                "audio_quality"):
        assert key in out[0]
        assert out[0][key] is None


def test_null_duration_collapses_to_zero():
    # `or 0.0` (not an `is None` check) is pre-existing: a real 0.0 and NULL both
    # collapse. Pinned so the move can be shown not to have "fixed" it.
    import scenes
    assert scenes._media_duration_s({"duration_s": None}) == 0.0
    assert scenes._media_duration_s({}) == 0.0
    assert scenes._media_duration_s({"duration_s": 0.0}) == 0.0
    assert scenes._media_duration_s({"duration_s": 31.0}) == 31.0


def test_keyframe_url_is_none_without_a_thumbnail():
    import scenes
    assert scenes._keyframe_url(None) is None
    assert scenes._keyframe_url("") is None
