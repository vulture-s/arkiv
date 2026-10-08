"""MCP coverage ledger — every HTTP route answers "what does an AI client get here?"

Each route served by the REST API is in exactly ONE of the two tables below:

  MCP_EQUIVALENTS   the route's read capability is reachable through an MCP tool
  NOT_EXPOSED       the route deliberately has no MCP tool, with a category + reason

tests/test_mcp_coverage_ledger.py fails when a route is in neither table, in both,
or when a table names a route or tool that no longer exists. Adding an endpoint
therefore forces the question "should AI see this?" at review time, instead of
leaving the REST and MCP surfaces to drift apart silently.

This is test-time data, NOT a runtime allow-list: nothing at runtime reads it, and
the MCP server's own read-only contract (mcp_server.py) is what actually governs
what a client can do.

Known blind spot: routes are enumerated from the OpenAPI schema, so the
`/ws/ingest` websocket (an ingest job, which MCP would not expose anyway) and the
`/assets` static mount are outside this ledger.
"""

# Categories for NOT_EXPOSED. "gap" is the only one that is NOT a decision: it
# marks a read-only route that would plausibly help an AI client but has no tool
# yet. It is listed so the gap is visible, not to approve it.
CATEGORIES = {
    "write": "Mutates library data. The MCP server is read-only by contract; AI "
             "writes need a registered-action pipeline (validation, preview, "
             "audit) before any of these can be exposed.",
    "admin": "Operator surface (tokens, trash, cache, settings, projects, logs). "
             "Not something an AI client should reach.",
    "job": "Starts or polls a long-running background job with side effects "
           "(ingest, proxy, transcription, sample library, offload).",
    "export": "Produces a file for a human tool chain (download or write to "
              "disk). An AI client gets the same data from the read tools.",
    "ui": "Serves the desktop UI itself (pages, media bytes, layout helpers, the "
          "built-in chat). No meaning outside the app.",
    "gap": "Read-only and plausibly useful to an AI client, but no MCP tool yet. "
           "Open question, not a decision.",
}

# "METHOD /path" -> (tool names, note on how far the tool matches the route)
MCP_EQUIVALENTS = {
    "GET /api/media": (
        ("search_media", "list_recent"),
        "q= search maps to search_media, newest-first listing to list_recent; "
        "the route's column filters and paging have no MCP form",
    ),
    "GET /api/media/{media_id}": (("get_media",), "same record, sanitized paths"),
    "GET /api/media/{media_id}/scenes": (("get_scenes",), ""),
    "GET /api/media/{media_id}/segments": (
        ("get_transcript",), "get_transcript returns segments alongside the text",
    ),
    "GET /api/media/{media_id}/tags": (("get_media",), "tags are part of get_media"),
    "GET /api/stats": (("library_stats",), ""),
    "GET /api/tags": (("list_tags",), "list_tags returns the top N, not all"),
}

# "METHOD /path" -> (category, reason)
NOT_EXPOSED = {
    # ── pages, media bytes, UI helpers ────────────────────────────────────────
    "GET /": ("ui", "SPA entry page"),
    "GET /dit": ("ui", "DIT page"),
    "GET /thumbnails/{name}": ("ui", "image bytes; MCP never ships media"),
    "GET /api/stream/{media_id}": ("ui", "video bytes; MCP never ships media"),
    "GET /api/media/{media_id}/waveform": ("ui", "waveform image for the player"),
    "GET /api/media/position/{media_id}": ("ui", "list-position helper for paging"),
    "GET /api/media/pool": ("ui", "sidebar folder tree"),
    "GET /api/duration-by-lang": ("ui", "dashboard chart"),
    "GET /api/size-by-ext": ("ui", "dashboard chart"),
    "POST /api/client-log": ("ui", "frontend error reporting"),
    "POST /api/open-file": ("ui", "opens a file on the desktop"),
    "POST /api/chat": ("ui", "the built-in assistant; an MCP client is its own assistant"),
    "GET /api/chat/conversations": ("ui", "built-in assistant history"),
    "GET /api/chat/history/{conv_id}": ("ui", "built-in assistant history"),

    # ── read-only gaps ────────────────────────────────────────────────────────
    "POST /api/search/query": ("gap", "structured field conditions; search_media is free text only"),
    "GET /api/search/all": ("gap", "cross-project search; Pro entitlement applies"),
    "GET /api/collections": ("gap", "Smart Collections grouping"),
    "GET /api/bins": ("gap", "user bins"),
    "GET /api/bins/{bin_id}": ("gap", "one bin's items"),
    "GET /api/media/facets/shoot-date": ("gap", "browse by shoot day"),
    "GET /api/media/{media_id}/chapters": ("gap", "chapter markers from scene frames"),
    "GET /api/media/{media_id}/transcripts": ("gap", "archived transcript languages; get_transcript returns only the active one"),

    # ── writes ────────────────────────────────────────────────────────────────
    "PATCH /api/media/{media_id}/rating": ("write", "rating / note"),
    "PATCH /api/media/{media_id}/inout": ("write", "IN/OUT points"),
    "PATCH /api/media/{media_id}/camera": ("write", "camera / angle"),
    "POST /api/media/{media_id}/tags": ("write", "add tag"),
    "DELETE /api/media/{media_id}/tags/{tag_name}": ("write", "remove tag"),
    "POST /api/media/{media_id}/transcript/activate": ("write", "switch active transcript"),
    "DELETE /api/media/{media_id}": ("write", "delete media (moves to trash)"),
    "POST /api/media/bulk-delete": ("write", "delete media in bulk"),
    "POST /api/media/prune-missing": ("write", "drops rows whose files are gone"),
    "POST /api/bins": ("write", "create bin"),
    "PATCH /api/bins/{bin_id}": ("write", "rename bin"),
    "DELETE /api/bins/{bin_id}": ("write", "delete bin"),
    "POST /api/bins/{bin_id}/items": ("write", "add to bin"),
    "DELETE /api/bins/{bin_id}/items": ("write", "remove from bin"),
    "POST /api/bins/{bin_id}/copy": ("write", "copies bin files to disk"),
    "PUT /api/corrections": ("write", "transcript correction rules"),
    "POST /api/recorrect": ("write", "applies correction rules to transcripts"),
    "POST /api/recorrect/revert": ("write", "restores a recorrect backup"),

    # ── background jobs ───────────────────────────────────────────────────────
    "POST /api/ingest": ("job", "ingest"),
    "POST /api/ingest/scan": ("job", "ingest scan"),
    "POST /api/ingest/upload": ("job", "upload + ingest"),
    "POST /api/ingest/ws": ("job", "ingest with progress channel"),
    "GET /api/ingest/engines": ("job", "transcription engine choices for ingest"),
    "POST /api/media/{media_id}/reingest": ("job", "re-ingest one clip"),
    "POST /api/media/{media_id}/retranscribe": ("job", "re-transcribe one clip"),
    "GET /api/media/{media_id}/retranscribe/status": ("job", "job status"),
    "POST /api/media/{media_id}/retry-vision": ("job", "re-run vision on one clip"),
    "GET /api/media/{media_id}/retry-vision/status": ("job", "job status"),
    "POST /api/retranscribe-all": ("job", "re-transcribe the library"),
    "GET /api/retranscribe-all/status": ("job", "job status"),
    "POST /api/embed/rebuild": ("job", "rebuild the vector index"),
    "POST /api/proxy/build": ("job", "build proxies"),
    "POST /api/proxy/build/{media_id}": ("job", "build one proxy"),
    "GET /api/proxy/status": ("job", "job status"),
    "POST /api/proxy/editor": ("job", "build editor proxies"),
    "GET /api/proxy/editor/status": ("job", "job status"),
    "GET /api/proxy/editor/{media_id}": ("job", "editor proxy state for one clip"),
    "POST /api/offload": ("job", "card offload with checksums"),
    "POST /api/offload/preview": ("job", "offload dry run"),
    "POST /api/sample/load": ("job", "sample library"),
    "POST /api/sample/remove": ("job", "sample library"),
    "POST /api/sample/seed": ("job", "sample library"),
    "GET /api/sample/seed/status": ("job", "job status"),
    "GET /api/sample/status": ("job", "sample library state"),

    # ── exports ───────────────────────────────────────────────────────────────
    "GET /api/media/{media_id}/export/{fmt}": ("export", "SRT/VTT/TXT/EDL/FCPXML download"),
    "POST /api/media/{media_id}/export-to": ("export", "writes an export to disk"),
    "GET /api/media/{media_id}/remotion-props": ("export", "props for Remotion renders"),
    "POST /api/export/batch": ("export", "batch export"),
    "GET /api/export/metadata-csv": ("export", "metadata CSV for Resolve"),
    "POST /api/export/metadata-csv-to": ("export", "writes metadata CSV to disk"),
    "GET /api/export/timeline/{fmt}": ("export", "timeline download"),

    # ── operator surface ──────────────────────────────────────────────────────
    "GET /api/admin/tokens": ("admin", "API tokens"),
    "POST /api/admin/tokens": ("admin", "API tokens"),
    "GET /api/admin/tokens/{token_id}": ("admin", "API tokens"),
    "DELETE /api/admin/tokens/{token_id}": ("admin", "API tokens"),
    "GET /api/admin/trash": ("admin", "trash"),
    "POST /api/admin/trash/purge": ("admin", "trash"),
    "POST /api/admin/trash/restore/{trash_id}": ("admin", "trash"),
    "GET /api/cache/info": ("admin", "cache"),
    "POST /api/cache/clear": ("admin", "cache"),
    "GET /api/settings": ("admin", "settings"),
    "PUT /api/settings": ("admin", "settings"),
    "DELETE /api/settings/{key}": ("admin", "settings"),
    "GET /api/corrections": ("admin", "correction rules (the writes are under write)"),
    "GET /api/recorrect/backups": ("admin", "recorrect backups"),
    "GET /api/projects": ("admin", "project registry; an MCP server serves one project root"),
    "POST /api/projects": ("admin", "project registry"),
    "DELETE /api/projects/{name}": ("admin", "project registry"),
    "GET /api/projects/health": ("admin", "project registry"),
    "POST /api/projects/sync": ("admin", "project registry"),
    "GET /api/entitlements": ("admin", "licence state"),
    "GET /api/health": ("admin", "service health"),
    "GET /api/version": ("admin", "service version"),
    "GET /api/logs/tail": ("admin", "server logs"),
}
