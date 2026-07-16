# Phase 14 — arkiv MCP Server (handover)

Exposes arkiv's local media knowledge layer to MCP clients (Claude Desktop,
Claude Code, OpenClaw) over **stdio**, so an agent can search and read your
footage library directly — no HTTP round-trip, no auth token.

> Status: shipped on `main`, released in v0.10.0. Mac (Python 3.12) verified; see
> "Verification" below. Timecode tools (`get_scenes`, `get_transcript` segments)
> landed 2026-07-16 — see "Timecodes" below.

## What ships

- `mcp_server.py` — a `FastMCP("arkiv")` stdio server.
- `tests/test_mcp_server.py` — 47 unit tests (mocked db/vectordb) + 1 async
  tool-registration test.
- `tests/test_mcp_e2e.py` — 3 end-to-end tests: two real-subprocess/real-`mcp`-
  client stdio smokes, plus one that boots the server with chromadb blocked and
  asserts it still serves + degrades search to SQL.
- `requirements.txt` — adds optional `mcp>=1.2` (3.10+ only; the SDK is gated out
  on the 3.9 NAS floor, and both test modules `importorskip` accordingly).

## Tools (all read-only)

| tool | args | returns (JSON string) |
|------|------|------------------------|
| `search_media` | `query: str, limit=20` | list of `{id, filename, path, score, excerpt, tags, lang, duration_s}` |
| `get_media` | `media_id: int` | full record (relative paths, tags) or `null` |
| `get_scenes` | `media_id: int` | `{media_id, media_duration_s, total, scenes: [...]}` or `null` |
| `get_transcript` | `media_id: int, include_words=False` | `{id, filename, lang, transcript, duration_s, segments, has_words}` (+ `words`, `words_truncated` when `include_words`) or `null` |
| `list_recent` | `limit=20` | list of lightweight records |
| `library_stats` | — | `{total, with_transcript, with_thumb, total_duration_s, total_size_mb, langs}` |
| `list_tags` | `limit=30` | list of `{name, count}` |

Search tries semantic (vector) search first and falls back to SQL
filename/transcript `LIKE` when the vector index is empty/unavailable.

## Timecodes — "which clip" vs "which seconds of it"

`search_media` / `get_media` answer *which clip*. `get_scenes` and
`get_transcript.segments` answer *which seconds of it*, which is what a
downstream editing agent actually needs in order to cut.

- **`get_scenes`** — one entry per scene-detect boundary: `start_s` / `end_s` /
  `duration_s` plus the nine vision fields for that scene's keyframe
  (`description`, `content_type`, `focus_score`, `atmosphere`, `energy`,
  `edit_position`, `edit_reason`, `stability`, `exposure`, `audio_quality`) and
  a `keyframe_path`. Every vision key is always present but is `null` on an
  unanalysed clip — check the value, not the key.
- **`get_transcript.segments`** — `[{start, end, text}]`, on by default.
  `words` (`[{word, start, end, score}]`) is **opt-in** via `include_words` and
  capped at 5000: word timing is multi-MB on a long clip, and unlike the HTTP
  API — where the same payload is merely slow (see the `words_json` drop at
  `routers/media.py:383-389`) — over MCP it blows the agent's context window.
  `has_words` advertises availability without paying for it.

Two things worth knowing:

- **Segments are projected, not passed through.** The backends disagree on shape:
  mlx-whisper (every Mac ingest) stores its native dict — `seek`, `id`,
  `tokens`, `temperature`, logprobs and all — while faster-whisper writes six
  keys and whisperx a third shape. Verbatim, the payload would depend on which
  machine ingested the clip. The tool projects onto `{start, end, text}`, which
  is what `transcribe.py:223-224` documents as the contract anyway.
- **Clips ingested before Phase 9.4 have no segment timing**, so `segments` is
  `[]` on them and callers should fall back to the flat `transcript`.

`get_scenes` shares its derivation with the HTTP `/api/media/{id}/scenes` route
via the `scenes.py` leaf, so the two cannot drift. They differ in exactly one
key, pinned by a test: HTTP emits `keyframe_url` (`/thumbnails/<basename>`, for
the authed thumbnail route), MCP emits `keyframe_path` (PROJECT_ROOT-relative) —
a server-relative URL is not actionable over stdio, but a path is, since the
client runs on the same machine.

## Design contract (red lines)

- **Read-only.** No ingest, no delete, no mutation — the server only reads.
- **No absolute-path leak.** Every path field passes through `_safe_path`, which
  returns a PROJECT_ROOT-relative path and falls back to the **basename** for
  out-of-root legacy rows (`db.to_relative` would otherwise pass an absolute path
  through). This mirrors the Phase 16.2 HTTP path-leak hardening and is covered
  by `test_safe_path_out_of_root_falls_back_to_basename`.
- **Reuse, don't fork.** Backed by `db` + `vectordb`; deliberately does **not**
  import `server` (which would pull in the whole FastAPI app + startup cost).
- **Output is JSON with `ensure_ascii=False`** so Chinese stays readable (matches
  `export.py`).

## Run

```bash
cd ~/.arkiv            # or your arkiv project root
source .venv/bin/activate
pip install mcp        # if not already installed
python mcp_server.py   # stdio server; blocks, speaks MCP on stdin/stdout
```

The server reads the same DB the rest of arkiv uses: `config.PROJECT_ROOT`
(`ARKIV_PROJECT_ROOT` env, else the install dir). Point it at a project by
setting `ARKIV_PROJECT_ROOT` before launch.

### Register with a client

Claude Desktop / Claude Code (`.mcp.json` or `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "arkiv": {
      "command": "/Users/you/.arkiv/.venv/bin/python",
      "args": ["/Users/you/.arkiv/mcp_server.py"],
      "env": { "ARKIV_PROJECT_ROOT": "/Users/you/your-project" }
    }
  }
}
```

## Verification

- `pytest tests/test_mcp_server.py` → 47 passed.
- `pytest tests/test_mcp_e2e.py` → 3 passed. Real subprocess + real `mcp` stdio
  client against a seeded temp project: `tools/list` returns all 7 tools;
  `get_scenes` / `get_transcript` / `library_stats` round-trip correctly;
  `search_media` returns the seeded row via SQL text match when no vector backend
  is present; an unknown id returns `null`; and a library seeded with out-of-root
  absolute paths (`/Volumes/…`, `C:/Users/…`) leaks none of them across
  `get_media`, `get_scenes` or `list_recent`. A third test blocks chromadb in a
  fresh interpreter and asserts the server still imports and serves.
- Full suite → 977 passed / 6 skipped (local; chromadb present). On CI 3.12 the
  three e2e tests run instead of skipping.

The e2e module is the only thing that exercises the MCP layer itself: every unit
test calls the `*_impl` functions directly, so FastMCP registration, stdio
JSON-RPC framing, tool dispatch, argument coercion and `_j` serialisation are
covered here and nowhere else. A tool can be perfectly correct at the impl level
and still be unreachable or carry a broken schema.

**It runs on CI** (3.12; the 3.9 leg skips the whole MCP suite because the SDK
needs 3.10+). It used not to: the server ran as a real subprocess needing the
real `chromadb`, `mcp_server` imported `vectordb` → `chromadb` at module level,
and CI installs no heavy backends — so the module self-skipped after probing a
fresh interpreter for chromadb. That import is now lazy: only `search_media`
imports `vectordb`, inside a try that binds it to `None` and degrades to SQL when
it (or chromadb) is absent. **Six of the seven tools never touch a vector index,
so the server boots and serves on a box with no vector backend** — which is both
the CI condition and a real deployment case (an MCP server on a machine with no
chromadb). The dim-mismatch branch stays safe because `vdb` is bound before the
`except vdb.EmbeddingDimensionMismatch` clause is ever evaluated.

> This section previously described an end-to-end stdio smoke that had never
> been checked in — it was an ad-hoc manual run. `tests/test_mcp_e2e.py` is that
> claim made real (2026-07-16), and the lazy-import change (same day) is what let
> it stop self-skipping on CI.

## Known limitations / follow-ups

- **Empty/stale vector index.** With no embeddings, `search_media` degrades to SQL
  text match (by design). Per the v0.x review, a stale 768-dim index vs the new
  bge-m3 (1024-dim) default raises in `vectordb` — `search_media` catches it and
  falls back to SQL, so the MCP server stays up, but semantic results will be
  empty until `python embed.py --rebuild` is run. (The loud dimension guard is a
  separate, recommended fix in `vectordb`.)
- **No write tools.** Ingest/tagging/rating intentionally not exposed. If an
  agent-driven ingest is wanted later, add it behind an explicit opt-in flag.
- **No pagination cursor** on `search_media` / `list_recent` (just `limit`).
