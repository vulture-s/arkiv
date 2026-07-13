"""Request-input parsers / option builders for the API layer.

R5-25 / round-5 #51: the APIRouter split is blocked by ~50 cross-group helpers —
a naive cut has each router do `from server import _ingest_cmd_opts`, and since
`server` imports the routers, that's a partially-initialized-module ImportError.
The fix is to extract the shared, server-state-free helpers into leaf service
modules that the routers (and server) import. This is the request-option
cluster: helpers that turn a raw request input (a `?ids=` query, an IngestRequest
body) into validated internal options, raising HTTPException on bad input.

Depends only on config + fastapi + stdlib — no server state — so it sits at the
bottom of the import graph. server.py re-exports these names for backward compat
(existing call sites + tests referencing `server._ingest_cmd_opts` etc. keep
working unchanged).

`_ingest_cmd_opts` takes an IngestRequest but only reads attributes (duck-typed
via a string annotation), so the pydantic model stays in server.py — no import
of server needed, no cycle.
"""
from fastapi import HTTPException

import config


def _parse_ids_query(ids_query):
    """Decode ?ids=1,2,3 query string → [int]. Returns None when no filter requested."""
    if not ids_query:
        return None
    parsed = []
    for raw in ids_query.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed.append(int(raw))
        except ValueError:
            raise HTTPException(400, f"無效的 media id: {raw!r}")
    return parsed  # may be [] if all entries were blank → caller treats as filter-with-no-rows


# brick 4 — whisper language codes the setup picker offers (whisper supports many
# more; this is the curated UI set). Omit / None = auto-detect or the preset hint.
_INGEST_LANGUAGES = [
    {"code": "zh", "label": "中文"},
    {"code": "en", "label": "English"},
    {"code": "ja", "label": "日本語"},
    {"code": "ko", "label": "한국어"},
]
_INGEST_LANGUAGE_CODES = {lang["code"] for lang in _INGEST_LANGUAGES}


def _ingest_cmd_opts(body: "IngestRequest") -> list:
    """Translate IngestRequest options into ingest.py CLI flags. Shared by the
    REST (/api/ingest) and WebSocket (/api/ingest/ws) triggers so the two never
    drift. --dir / --limit stay with the callers; this is only the extra knobs."""
    opts: list = []
    if body.skip_vision:
        opts.append("--skip-vision")
    if body.refresh:
        opts.append("--refresh")
    if body.recursive:
        opts.append("--recursive")
    if body.max_failures and body.max_failures > 0:
        opts += ["--max-failures", str(int(body.max_failures))]
    if body.skip_failed:
        opts.append("--skip-failed")
    if body.no_embed:
        opts.append("--no-embed")
    # brick 4 — only emit when explicitly set (and valid) so defaults stay untouched.
    if body.whisper_guard is not None and body.whisper_guard in config.WHISPER_GUARD_LAYERS:
        opts += ["--whisper-guard", str(int(body.whisper_guard))]
    if body.language and body.language in _INGEST_LANGUAGE_CODES:
        opts += ["--language", body.language]
    return opts
