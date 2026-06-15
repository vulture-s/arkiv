"""S1a — ingest engine options surfaced through the API (server._ingest_cmd_opts).

The redesign's ingest setup dialog (docs/design/redesign-2026 op-01) exposes
transcribe/vision knobs; these must translate to the real ingest.py CLI flags,
shared by the REST and WebSocket triggers so they never drift.
"""
import server


def test_defaults_emit_no_extra_flags():
    # an unconfigured ingest must behave exactly as before (only --dir/--limit,
    # which the callers add — this helper returns just the extra knobs)
    assert server._ingest_cmd_opts(server.IngestRequest(path="/x")) == []


def test_each_option_maps_to_its_flag():
    body = server.IngestRequest(
        path="/x", skip_vision=True, refresh=True, recursive=True,
        max_failures=20, skip_failed=True, no_embed=True,
    )
    opts = server._ingest_cmd_opts(body)
    assert "--skip-vision" in opts
    assert "--refresh" in opts
    assert "--recursive" in opts
    assert opts[opts.index("--max-failures") + 1] == "20"  # value follows the flag
    assert "--skip-failed" in opts
    assert "--no-embed" in opts


def test_zero_max_failures_omits_flag():
    # 0 = the engine default (halt on first failure); must NOT emit the flag
    assert "--max-failures" not in server._ingest_cmd_opts(
        server.IngestRequest(path="/x", max_failures=0)
    )
