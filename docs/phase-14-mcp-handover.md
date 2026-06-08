# Phase 14 — arkiv MCP Server (handover)

Exposes arkiv's local media knowledge layer to MCP clients (Claude Desktop,
Claude Code, OpenClaw) over **stdio**, so an agent can search and read your
footage library directly — no HTTP round-trip, no auth token.

> Status: implemented + tested on branch `feat/phase-14-mcp-server`. Not merged,
> not tagged. Mac (Python 3.12) verified; see "Verification" below.

## What ships

- `mcp_server.py` — a `FastMCP("arkiv")` stdio server.
- `tests/test_mcp_server.py` — 18 unit tests (mocked db/vectordb) + 1 async
  tool-registration test.
- `requirements.txt` — adds optional `mcp>=1.2`.

## Tools (all read-only)

| tool | args | returns (JSON string) |
|------|------|------------------------|
| `search_media` | `query: str, limit=20` | list of `{id, filename, path, score, excerpt, tags, lang, duration_s}` |
| `get_media` | `media_id: int` | full record (relative paths, tags) or `null` |
| `get_transcript` | `media_id: int` | `{id, filename, lang, transcript}` or `null` |
| `list_recent` | `limit=20` | list of lightweight records |
| `library_stats` | — | `{total, with_transcript, with_thumb, total_duration_s, total_size_mb, langs}` |
| `list_tags` | `limit=30` | list of `{name, count}` |

Search tries semantic (vector) search first and falls back to SQL
filename/transcript `LIKE` when the vector index is empty/unavailable.

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

- `pytest tests/test_mcp_server.py` → 18 passed.
- Full suite → 422 passed / 3 skipped (was 404/3; +18, zero regressions).
- End-to-end stdio smoke (real subprocess + `mcp` client): `tools/list` returns
  all 6 tools; `library_stats` / `search_media` / `list_recent` return valid JSON
  against an empty DB without error.

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
