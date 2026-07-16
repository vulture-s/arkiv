"""End-to-end stdio smoke for the MCP server — a real subprocess, a real client.

docs/phase-14-mcp-handover.md and CHANGELOG both claimed "an end-to-end stdio
smoke" since Phase 14, but no such artifact was ever checked in: it was an ad-hoc
manual run. This is that claim, made true.

It earns its keep beyond the unit tests, which all call `*_impl` functions
directly and so never touch the MCP layer at all. Everything between an agent and
those impls — FastMCP registration, the stdio JSON-RPC framing, tool dispatch,
schema coercion of arguments, `_j` serialisation — is exercised only here. A tool
can be perfectly correct at the impl level and still be unreachable, misnamed, or
carry a broken schema.

Runs anywhere `mcp` is importable — including CI. It used to self-skip on any
env without a real chromadb, because `mcp_server` imported `vectordb` (→ chromadb)
at module scope and the server could not boot without it. That import is now lazy
(only `search_media` pulls it in, inside a try that degrades to SQL), so the
server starts on a box with no vector backend and this smoke needs no skip. Only
gate left is the 3.9 leg, where the MCP SDK is absent (`importorskip("mcp")`).
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_REPO = Path(__file__).resolve().parent.parent

# Seeds a project the same way an ingest would, in a subprocess so that this
# test's own config/db import state is untouched — the server subprocess must
# read the DB from disk exactly as a real deployment does.
_SEED = """
import db
db.init_db()
db.upsert({
    "path": "clips/e2e.mp4", "filename": "e2e.mp4", "ext": ".mp4",
    "duration_s": 30.0, "size_mb": 5.0, "width": 1920, "height": 1080,
    "fps": 25.0, "has_audio": 1, "lang": "zh",
    "transcript": "第一句第二句",
    "segments_json": '[{"id":0,"seek":0,"start":0.0,"end":2.4,"text":"第一句",'
                     '"tokens":[50364,2503],"temperature":0.0,"avg_logprob":-0.3,'
                     '"compression_ratio":1.2,"no_speech_prob":0.01}]',
    "words_json": '[{"word":"第一","start":0.1,"end":0.4,"score":0.98}]',
    "processed_at": "2026-07-16T00:00:00",
})
db.upsert_frame(
    media_id=1, frame_index=0, timestamp_s=0.0,
    thumbnail_path="thumbnails/e2e_f0.jpg",
    description="手持走入店內", content_type="Establishing",
    focus_score=3, atmosphere="紀實",
)
db.upsert_frame(media_id=1, frame_index=1, timestamp_s=12.0, description="seg 1")
"""


@pytest.fixture
def seeded_project(tmp_path):
    env = dict(os.environ, ARKIV_PROJECT_ROOT=str(tmp_path))
    env.pop("ARKIV_DB_PATH", None)
    proc = subprocess.run(
        [sys.executable, "-c", _SEED],
        cwd=str(_REPO), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, "seed failed:\n{0}\n{1}".format(proc.stdout, proc.stderr)
    return env


async def _call(session, name, **args):
    result = await session.call_tool(name, args)
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_mcp_stdio_end_to_end(seeded_project):
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(_REPO / "mcp_server.py")],
        env=seeded_project,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ── the server is reachable and advertises what it should ──────────
            tools = {t.name for t in (await session.list_tools()).tools}
            assert {
                "search_media", "get_media", "get_transcript",
                "list_recent", "library_stats", "list_tags", "get_scenes",
            } <= tools

            # ── get_scenes: the timecoded breakdown, over the real protocol ────
            scenes = await _call(session, "get_scenes", media_id=1)
            assert scenes["media_id"] == 1
            assert scenes["media_duration_s"] == 30.0
            assert scenes["total"] == 2
            first = scenes["scenes"][0]
            assert first["start_s"] == 0.0
            assert first["end_s"] == 12.0            # next frame's start
            assert first["description"] == "手持走入店內"
            assert first["keyframe_path"] == "thumbnails/e2e_f0.jpg"
            assert scenes["scenes"][1]["end_s"] == 30.0   # last → media duration
            # the MCP surface must never emit the HTTP URL form
            assert "keyframe_url" not in first

            # ── unknown id is null, not an error and not an empty list ─────────
            assert await _call(session, "get_scenes", media_id=999999) is None

            # ── get_transcript: segments on by default, words off ─────────────
            tr = await _call(session, "get_transcript", media_id=1)
            assert tr["duration_s"] == 30.0
            assert tr["segments"] == [{"start": 0.0, "end": 2.4, "text": "第一句"}]
            assert tr["has_words"] is True
            assert "words" not in tr
            # decoder internals must not survive the round trip
            assert "tokens" not in json.dumps(tr)

            # ── …and on request ───────────────────────────────────────────────
            tr_w = await _call(session, "get_transcript", media_id=1, include_words=True)
            assert tr_w["words"] == [
                {"word": "第一", "start": 0.1, "end": 0.4, "score": 0.98}
            ]
            assert tr_w["words_truncated"] is False

            # ── the pre-existing tools still answer ────────────────────────────
            stats = await _call(session, "library_stats")
            assert stats["total"] == 1

            # ── search_media works with NO vector backend (the CI reality) ─────
            # On CI chromadb isn't installed, so the lazy `import vectordb` in
            # search_media_impl fails and it degrades to a SQL filename/transcript
            # LIKE. The seeded row is filename "e2e.mp4" — a text match finds it.
            # (Normal shape is a bare list; a stale index would wrap it in
            # {items, search_degraded} — accept either.)
            sm = await _call(session, "search_media", query="e2e")
            hits = sm["items"] if isinstance(sm, dict) else sm
            assert any(h["id"] == 1 for h in hits), sm


@pytest.mark.asyncio
async def test_mcp_stdio_never_leaks_absolute_paths(tmp_path):
    """The server's stated red line, checked at the far end of the wire rather
    than at the impl. Seeded with an out-of-root absolute path — what a legacy
    row from another machine looks like."""
    env = dict(os.environ, ARKIV_PROJECT_ROOT=str(tmp_path))
    env.pop("ARKIV_DB_PATH", None)
    seed = """
import db
db.init_db()
db.upsert({
    "path": "/Volumes/SomeoneElse/private/secret.mp4", "filename": "secret.mp4",
    "ext": ".mp4", "duration_s": 10.0, "lang": "zh",
    "thumbnail_path": "C:/Users/me/private/secret.jpg",
    "processed_at": "2026-07-16T00:00:00",
})
db.upsert_frame(media_id=1, frame_index=0, timestamp_s=0.0,
                thumbnail_path="C:/Users/me/private/f0.jpg", description="x")
"""
    proc = subprocess.run(
        [sys.executable, "-c", seed],
        cwd=str(_REPO), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    params = StdioServerParameters(
        command=sys.executable, args=[str(_REPO / "mcp_server.py")], env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            media = json.dumps(await _call(session, "get_media", media_id=1))
            scenes = json.dumps(await _call(session, "get_scenes", media_id=1))
            recent = json.dumps(await _call(session, "list_recent"))

    for surface, payload in (("get_media", media), ("get_scenes", scenes),
                             ("list_recent", recent)):
        assert "/Volumes/SomeoneElse" not in payload, surface
        assert "C:/Users/me" not in payload, surface
        assert "private" not in payload, surface


def test_mcp_boots_and_degrades_without_chromadb(seeded_project):
    """The server must import and serve on a box with NO vector backend.

    Six of the seven tools never touch a vector index, yet before the lazy-import
    change `import mcp_server` died at module load if chromadb was missing. Here a
    FRESH interpreter (no conftest fake) blocks both `vectordb` and `chromadb`,
    imports the server, and drives the impls: the non-vector tools work and
    `search_media` degrades to a SQL text match instead of raising. This is the
    condition that let the stdio smoke above stop self-skipping on CI.
    """
    script = textwrap.dedent(
        """
        import sys
        # simulate a machine with no vector backend installed
        sys.modules["chromadb"] = None
        sys.modules["vectordb"] = None

        import mcp_server  # must NOT import vectordb at module scope anymore

        st = mcp_server.library_stats_impl()
        assert st["total"] == 1, st
        assert mcp_server.get_scenes_impl(1)["total"] == 2

        # the lazy `import vectordb` fails -> vdb=None -> SQL filename LIKE
        hits = mcp_server.search_media_impl("e2e", limit=5)
        assert any(h["id"] == 1 for h in hits), hits

        print("NOCHROMA_OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_REPO), env=seeded_project, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, "boot-without-chromadb failed:\n{0}\n{1}".format(
        proc.stdout, proc.stderr
    )
    assert "NOCHROMA_OK" in proc.stdout, proc.stdout
