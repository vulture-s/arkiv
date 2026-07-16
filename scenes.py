"""Scene-assembly service for the API + MCP layers.

The per-scene derivation (frames → scene boundaries with a computed end_s) is
needed by two consumers that must not import each other: the HTTP route in
routers/media.py, and mcp_server.py, which deliberately does NOT import `server`
— that would pull in the whole FastAPI app + its startup cost. Following the
R5-25 / round-5 #51 leaf pattern (pathres.py, mediarecords.py): extract the
shared, server-state-free logic into a leaf both import, so neither side forks
the shape.

Two divergent scene shapes across MCP and HTTP is precisely the failure mode
this exists to prevent — and not hypothetically. mcp_server carried its own copy
of the path-leak guard for exactly this reason (there was nowhere to share from)
and the copy silently missed a fix to the original for 38 days (#182). Same
trap, so: one derivation, two callers.

Depends only on `pathres` (itself db+stdlib) and stdlib — no server state, no
fastapi, no db — so it sits at the bottom of the import graph and is safe for
the stdio MCP server to import. Callers fetch their own rows and own their own
error surface: this module returns data and never raises HTTPException; the 404
stays in routers/media.py.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from pathres import _resolve_media_path


def _build_scenes(frames: List[Dict[str, Any]], media_duration_s: float) -> List[Dict[str, Any]]:
    """Frames → per-scene dicts. The shared core; carries NO path/URL field.

    Per-scene shape (breaking change 2026-06-29): each scene = one scene-detect
    boundary persisted in the frames table, with end_s computed from the next
    frame's start (or media.duration_s for the last). Consumers (smart-edit,
    OpenMontage arkiv_clip_search, Vyra, Palmier) expect start/end/duration +
    vision metadata per scene, not a per-frame flat list.

    Key insertion order is contract — the HTTP body is byte-frozen by
    tests/test_scenes_contract.py. Do not reorder. Do not swap .get() for a
    conditional: an absent vision field serialises as null, not omitted, and
    consumers rely on key presence rather than value presence.
    """
    scenes = []
    for i, frame in enumerate(frames):
        start_s = float(frame["timestamp_s"])
        if i + 1 < len(frames):
            end_s = float(frames[i + 1]["timestamp_s"])
        else:
            end_s = media_duration_s
        if end_s < start_s:
            # db.get_frames is ORDER BY frame_index — nothing enforces that
            # timestamp_s rises with it, so clamp rather than emit a negative span.
            end_s = start_s
        scenes.append({
            "scene_index": frame["frame_index"],
            "start_s": start_s,
            "end_s": end_s,
            "duration_s": end_s - start_s,
            "description": frame.get("description", ""),
            "content_type": frame.get("content_type"),
            "focus_score": frame.get("focus_score"),
            "atmosphere": frame.get("atmosphere"),
            "energy": frame.get("energy"),
            "edit_position": frame.get("edit_position"),
            "edit_reason": frame.get("edit_reason"),
            "stability": frame.get("stability"),
            "exposure": frame.get("exposure"),
            "audio_quality": frame.get("audio_quality"),
        })
    return scenes


def _media_duration_s(rec: Dict[str, Any]) -> float:
    """`or` rather than an `is None` check is deliberate and pre-existing: a real
    0.0 and a NULL both collapse to 0.0. Preserved verbatim from the route."""
    return float(rec.get("duration_s") or 0.0)


def _keyframe_url(thumbnail_path: Optional[str]) -> Optional[str]:
    """HTTP-only: the /thumbnails/<basename> URL served by the authed thumbnail
    route. Not used by MCP — a server-relative URL is meaningless over stdio."""
    if not thumbnail_path:
        return None
    return "/thumbnails/{0}".format(Path(_resolve_media_path(thumbnail_path)).name)


def _scenes_payload(media_id: int, rec: Dict[str, Any],
                    frames: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The exact HTTP response body. Byte-frozen by tests/test_scenes_contract.py."""
    media_duration_s = _media_duration_s(rec)
    scenes = _build_scenes(frames, media_duration_s)
    for scene, frame in zip(scenes, frames):
        if frame.get("thumbnail_path"):
            # Appended LAST, and conditionally: key order is contract, and a
            # scene with no thumbnail omits the key rather than carrying null.
            scene["keyframe_url"] = _keyframe_url(frame["thumbnail_path"])
    return {
        "media_id": media_id,
        "media_duration_s": media_duration_s,
        "scenes": scenes,
        "total": len(scenes),
    }
