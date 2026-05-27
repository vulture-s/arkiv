import importlib


def test_chat_returns_expected_schema(monkeypatch):
    llm = importlib.import_module("llm")
    captured = {}

    class FakeResponse(object):
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {"content": "router ok"},
                "eval_count": 7,
                "prompt_eval_count": 3,
            }

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(llm.requests, "post", fake_post)
    result = llm.chat("hello", system="sys", conversation=[{"role": "assistant", "content": "a"}])

    assert result["text"] == "router ok"
    assert result["tokens_used"] == 10
    assert result["provider"] == "ollama"
    assert result["model"] == llm.OLLAMA_CHAT_MODEL
    assert result["latency_ms"] >= 0
    assert captured["url"] == "{0}/api/chat".format(llm.OLLAMA_URL)
    assert captured["json"]["messages"][0] == {"role": "system", "content": "sys"}
    assert captured["json"]["messages"][-1] == {"role": "user", "content": "hello"}
    assert captured["timeout"] == 120


def test_embed_returns_list_of_floats(monkeypatch):
    llm = importlib.import_module("llm")
    captured = {}

    class FakeResponse(object):
        def raise_for_status(self):
            return None

        def json(self):
            return {"embedding": [0.1, 0.2, 0.3]}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(llm.requests, "post", fake_post)
    result = llm.embed("x" * (llm.EMBED_MAX_CHARS + 123))

    assert result == [0.1, 0.2, 0.3]
    assert captured["url"] == "{0}/api/embeddings".format(llm.OLLAMA_URL)
    assert captured["json"]["model"] == llm.OLLAMA_EMBED_MODEL
    assert len(captured["json"]["prompt"]) == llm.EMBED_MAX_CHARS
    assert captured["timeout"] == 30


def test_vision_returns_expected_schema(monkeypatch):
    llm = importlib.import_module("llm")
    captured = {}

    class FakeResponse(object):
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "response": "vision ok",
                "eval_count": 4,
                "prompt_eval_count": 1,
            }

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(llm.requests, "post", fake_post)
    result = llm.vision("describe", "abcd")

    assert result["text"] == "vision ok"
    assert result["tokens_used"] == 5
    assert result["provider"] == "ollama"
    assert result["model"] == llm.OLLAMA_VISION_MODEL
    assert result["latency_ms"] >= 0
    assert captured["url"] == "{0}/api/generate".format(llm.OLLAMA_URL)
    assert captured["json"]["images"] == ["abcd"]
    assert captured["timeout"] == 300


def test_chat_json_mode_sets_format(monkeypatch):
    llm = importlib.import_module("llm")
    captured = {}

    class FakeResponse(object):
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "{}"}}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm.chat("return json", json_mode=True)

    assert captured["json"]["format"] == "json"


def test_token_tracking_not_negative(monkeypatch):
    llm = importlib.import_module("llm")

    class FakeResponse(object):
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {"content": "x"},
                "eval_count": -4,
                "prompt_eval_count": None,
            }

    monkeypatch.setattr(llm.requests, "post", lambda *args, **kwargs: FakeResponse())
    result = llm.chat("hi")

    assert result["tokens_used"] == 0


def test_chat_uses_default_model_if_none(monkeypatch):
    llm = importlib.import_module("llm")
    captured = {}

    class FakeResponse(object):
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "x"}}

    def fake_post(url, json, timeout):
        captured["model"] = json["model"]
        return FakeResponse()

    monkeypatch.setattr(llm.requests, "post", fake_post)
    monkeypatch.setattr(llm, "OLLAMA_CHAT_MODEL", "sentinel-model")
    llm.chat("hello")

    assert captured["model"] == "sentinel-model"
