"""Embedder drift: same model NAME, different vectors.

_assert_collection_compatible only compares the stamped model name, so an Ollama
upgrade / re-pulled tag / new quantization that changes the vectors passes it and
search degrades silently. vectordb.embedder_drift re-embeds stored chunks and
compares them with their stored vectors; health.py reports the result.

These pin: drift is detected, a matching embedder passes, and "nothing to compare"
is reported as unmeasured — never as a pass.
"""
import importlib

vectordb = importlib.import_module("vectordb")
health = importlib.import_module("health")


class FakeCollection:
    def __init__(self, docs, embs):
        self.docs, self.embs = docs, embs
        self.calls = []

    def get(self, include=None, limit=None, **_):
        self.calls.append({"include": include, "limit": limit})
        return {"ids": [str(i) for i in range(len(self.docs))],
                "documents": self.docs[:limit], "embeddings": self.embs[:limit]}


STORED = {"a": [1.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0]}


def test_matching_embedder_passes(monkeypatch):
    monkeypatch.setattr(vectordb, "embed_batch", lambda texts: [STORED[t] for t in texts])
    col = FakeCollection(["a", "b"], [STORED["a"], STORED["b"]])
    r = vectordb.embedder_drift(col)
    assert r["ok"] is True and r["n"] == 2 and r["min_cos"] > 0.999999
    assert "documents" in col.calls[0]["include"] and "embeddings" in col.calls[0]["include"]


def test_drifted_embedder_is_caught(monkeypatch):
    # cos([1,0,0],[1,0.06,0]) ≈ 0.9982 — the size of a quantization change, well
    # past the measured cross-machine noise (min 0.999986)
    monkeypatch.setattr(vectordb, "embed_batch",
                        lambda texts: [[1.0, 0.06, 0.0] if t == "a" else STORED[t] for t in texts])
    r = vectordb.embedder_drift(FakeCollection(["a", "b"], [STORED["a"], STORED["b"]]))
    assert r["ok"] is False and r["min_cos"] < vectordb.DRIFT_COS_FLOOR


def test_dimension_change_is_drift_not_a_crash(monkeypatch):
    monkeypatch.setattr(vectordb, "embed_batch", lambda texts: [[1.0, 0.0] for _ in texts])
    r = vectordb.embedder_drift(FakeCollection(["a"], [STORED["a"]]))
    assert r["ok"] is False and r["min_cos"] == 0.0


def test_empty_index_is_unmeasured(monkeypatch):
    def boom(texts):
        raise AssertionError("must not embed when there is nothing to compare")
    monkeypatch.setattr(vectordb, "embed_batch", boom)
    assert vectordb.embedder_drift(FakeCollection([], [])) is None
    # rows without stored text cannot be re-embedded either
    assert vectordb.embedder_drift(FakeCollection([None, ""], [STORED["a"], STORED["b"]])) is None


def test_numpy_like_embeddings_are_accepted(monkeypatch):
    # chromadb hands back numpy arrays; `embs or []` on one raises "truth value is ambiguous"
    class Arr(list):
        def __bool__(self):
            raise ValueError("The truth value of an array is ambiguous")
    monkeypatch.setattr(vectordb, "embed_batch", lambda texts: [STORED[t] for t in texts])
    col = FakeCollection(["a"], Arr([Arr(STORED["a"])]))
    assert vectordb.embedder_drift(col)["ok"] is True


# ── health.py wiring ─────────────────────────────────────────────────────────
def test_health_warns_on_drift(monkeypatch, capsys):
    monkeypatch.setattr(vectordb, "embedder_drift",
                        lambda: {"min_cos": 0.98, "n": 5, "ok": False})
    before = health.WARN_COUNT
    health._check_embedder_drift()
    out = capsys.readouterr().out
    assert health.WARN_COUNT == before + 1
    assert "DRIFTED" in out and "embed.py --rebuild" in out


def test_health_passes_when_matching(monkeypatch, capsys):
    monkeypatch.setattr(vectordb, "embedder_drift",
                        lambda: {"min_cos": 0.9999991, "n": 5, "ok": True})
    before = health.PASS
    health._check_embedder_drift()
    assert health.PASS == before + 1
    assert "min cosine 0.999999" in capsys.readouterr().out


def test_health_unmeasured_is_not_a_pass(monkeypatch, capsys):
    monkeypatch.setattr(vectordb, "embedder_drift", lambda: None)
    before = health.PASS
    health._check_embedder_drift()
    assert health.PASS == before
    assert "not measured" in capsys.readouterr().out


def test_health_backend_error_is_not_a_pass(monkeypatch, capsys):
    def down():
        raise ConnectionError("chroma locked")
    monkeypatch.setattr(vectordb, "embedder_drift", down)
    before = health.PASS
    health._check_embedder_drift()
    assert health.PASS == before
    assert "not measured: ConnectionError" in capsys.readouterr().out


def test_health_main_runs_the_drift_check():
    """Unit tests above call _check_embedder_drift directly — pin that main() wires it
    in (after the embed-model presence check), or the check exists but never runs."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(health.main))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_check_embedder_drift"]
    assert len(calls) == 1
