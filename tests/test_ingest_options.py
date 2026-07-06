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


# ── brick 4: whisper guard preset + language ──────────────────────────────────

def test_brick4_whisper_guard_and_language_map_to_flags():
    body = server.IngestRequest(path="/x", whisper_guard=2, language="zh")
    opts = server._ingest_cmd_opts(body)
    assert opts[opts.index("--whisper-guard") + 1] == "2"
    assert opts[opts.index("--language") + 1] == "zh"


def test_brick4_defaults_omit_flags():
    # None preset + None language = unchanged engine behaviour, no flags
    opts = server._ingest_cmd_opts(server.IngestRequest(path="/x"))
    assert "--whisper-guard" not in opts and "--language" not in opts


def test_brick4_invalid_values_dropped():
    # an out-of-range preset / unknown language code must not reach the CLI
    opts = server._ingest_cmd_opts(
        server.IngestRequest(path="/x", whisper_guard=99, language="xx")
    )
    assert "--whisper-guard" not in opts and "--language" not in opts


def test_brick4_engines_endpoint_shape(fastapi_client, monkeypatch):
    # keep the endpoint deterministic + offline — don't let it hit a real Ollama
    import vision
    monkeypatch.setattr(vision, "list_vision_models", lambda: ["qwen3-vl:8b"])
    r = fastapi_client.get("/api/ingest/engines")
    assert r.status_code == 200
    data = r.json()
    assert data["default_mode"] in {m["mode"] for m in data["whisper_modes"]}
    assert data["whisper_modes"] and all("name" in m for m in data["whisper_modes"])
    assert {lang["code"] for lang in data["languages"]} >= {"zh", "en"}


# ── brick 4b: vision model picker sourced from installed Ollama models ─────────

class _FakeResp:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.ok = ok

    def json(self):
        return self._payload


def _clear_vision_caches():
    import vision
    vision._vision_capable_cache.clear()


def test_brick4b_is_vision_model_prefers_ollama_capabilities(monkeypatch):
    import vision
    _clear_vision_caches()
    # Ollama reports capabilities → trust them over the name (a "vision" model
    # named plainly is still detected; a "vl"-named model reported non-vision is
    # correctly excluded).
    caps = {"qwen3-vl:8b": ["completion", "vision"], "text-only:latest": ["completion"]}
    monkeypatch.setattr(vision.requests, "post",
                        lambda url, json, timeout: _FakeResp({"capabilities": caps[json["name"]]}))
    assert vision._is_vision_model("qwen3-vl:8b") is True
    assert vision._is_vision_model("text-only:latest") is False


def test_brick4b_is_vision_model_falls_back_to_name_heuristic(monkeypatch):
    import vision
    _clear_vision_caches()
    # /api/show unreachable (older Ollama / down) → name heuristic decides
    def _boom(*a, **k):
        raise Exception("no /api/show")
    monkeypatch.setattr(vision.requests, "post", _boom)
    assert vision._is_vision_model("qwen2.5-vl:7b") is True
    assert vision._is_vision_model("qwen2.5vl:7b") is True  # no separator before "vl"
    assert vision._is_vision_model("llama3.2-vision:11b") is True
    assert vision._is_vision_model("llava:13b") is True
    assert vision._is_vision_model("moondream:latest") is True
    assert vision._is_vision_model("llama3:8b") is False
    assert vision._is_vision_model("mistral:7b") is False


def test_brick4b_list_vision_models_filters_and_sorts(monkeypatch):
    import vision
    _clear_vision_caches()
    tags = {"models": [
        {"name": "qwen3-vl:8b"}, {"name": "llama3:8b"}, {"name": "qwen2.5vl:7b"},
        {"name": "llava:13b"}, {"name": "nomic-embed-text:latest"},
    ]}
    monkeypatch.setattr(vision.requests, "get", lambda url, timeout: _FakeResp(tags))
    # no capabilities from /api/show → name heuristic (incl. the no-separator
    # "qwen2.5vl" case that only the widened heuristic catches)
    monkeypatch.setattr(vision.requests, "post", lambda url, json, timeout: _FakeResp({}, ok=False))
    out = vision.list_vision_models()
    assert out == ["llava:13b", "qwen2.5vl:7b", "qwen3-vl:8b"]  # vision-only, sorted


def test_brick4b_list_vision_models_empty_when_ollama_down(monkeypatch):
    import vision
    _clear_vision_caches()
    def _boom(*a, **k):
        raise Exception("connection refused")
    monkeypatch.setattr(vision.requests, "get", _boom)
    assert vision.list_vision_models() == []  # graceful → UI falls back to free text


def test_brick4b_engines_includes_current_model_even_if_undetected(fastapi_client, monkeypatch):
    import vision, settings as settings_store
    monkeypatch.setattr(vision, "list_vision_models", lambda: ["llava:13b"])
    monkeypatch.setattr(settings_store, "effective",
                        lambda key, *a, **k: "custom-vl:latest" if key == "vision.model" else "x")
    data = fastapi_client.get("/api/ingest/engines").json()
    assert "custom-vl:latest" in data["vision_models"]  # active selection always present
    assert data["vision_model"] == "custom-vl:latest"
