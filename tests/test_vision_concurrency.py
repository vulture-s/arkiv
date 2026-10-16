"""Bounded client-side concurrency in `vision.describe_frames`.

Bench 2026-10-17 (PR #548): two in-flight vision requests against an Ollama at
server NUM_PARALLEL=1 is +26% frames/min on M2 Max. These tests pin what the
concurrent path must NOT change:

* output order is frame order, whatever order the requests finish in;
* never more than N requests in flight;
* per-frame failures keep their serial semantics (light fail → empty result,
  representative fail → error result, unusable → [skipped] + inherited edit_reason);
* concurrency 1 runs the original serial loop — byte-identical output and the
  same call sequence;
* progress events are emitted from the caller's thread, same sequence as serial.

The HTTP layer is faked at `vision.vision` (the `llm.vision` binding `_call_vision`
uses), so `_call_vision` → `_describe_one(_light)` → normalisation all run for real.
"""
from __future__ import annotations

import importlib
import json
import random
import threading
import time

import pytest

import progress


@pytest.fixture
def vis(monkeypatch):
    v = importlib.import_module("vision")
    import settings as _settings
    monkeypatch.setattr(_settings, "vision_model", lambda project=None: "fake-vl")
    monkeypatch.setattr(_settings, "vision_num_ctx", lambda project=None: 2048)
    monkeypatch.setattr(v, "_is_usable_frame", lambda p: "unusable" not in p)
    return v


class FakeOllama:
    """Fake `llm.vision`: answers per image path, with jittered latency so that
    completion order differs from submission order, and counts peak in-flight."""

    def __init__(self, fail=(), delay=0.02, jitter=True):
        self.fail = set(fail)
        self.delay = delay
        self.jitter = jitter
        self.lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0
        self.calls = []
        self.rng = random.Random(1234)

    def __call__(self, prompt, image_b64, model=None, num_ctx=None):
        import base64
        path = base64.b64decode(image_b64).decode()
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            self.calls.append((path, "full" if prompt is _full_prompt() else "light"))
            d = self.delay * (self.rng.random() * 2 if self.jitter else 1)
        try:
            time.sleep(d)
            if path in self.fail:
                raise ConnectionError("boom " + path)
            return {"text": json.dumps({
                "description": "desc of " + path,
                "tags": ["tag-" + path.rsplit("/", 1)[-1]],
                "content_type": "B-Roll",
                "edit_reason": "rep-reason" if prompt is _full_prompt() else "light-reason",
            }, ensure_ascii=False)}
        finally:
            with self.lock:
                self.in_flight -= 1


def _full_prompt():
    return importlib.import_module("vision").PROMPT


@pytest.fixture
def frames(tmp_path):
    """Frame files whose *content* is their own path, so the fake can key on it."""
    def make(names):
        out = []
        for n in names:
            p = tmp_path / n
            p.write_bytes(str(p).encode())
            out.append(str(p))
        return out
    return make


def _run(vis, monkeypatch, paths, n, fake, model=None):
    monkeypatch.setattr(vis.config, "VISION_CONCURRENCY", n, raising=False)
    monkeypatch.setattr(vis, "vision", fake)
    events = []
    with progress.capture(events.append):
        out = vis.describe_frames(paths, model=model)
    return out, events


NAMES = ["f{0:02d}.jpg".format(i) for i in range(9)]


def test_order_matches_serial_byte_for_byte(vis, monkeypatch, frames):
    paths = frames(NAMES[:4] + ["f04_unusable.jpg"] + NAMES[5:])
    fail = {paths[2]}
    serial, ev1 = _run(vis, monkeypatch, paths, 1, FakeOllama(fail=fail))
    for n in (2, 3, 8):
        conc, evn = _run(vis, monkeypatch, paths, n, FakeOllama(fail=fail))
        assert json.dumps(conc, sort_keys=True, ensure_ascii=False) == \
            json.dumps(serial, sort_keys=True, ensure_ascii=False), n
        assert [r["file"] for r in conc] == paths
        assert vis.frames_to_json(conc) == vis.frames_to_json(serial)
        assert evn == ev1  # same progress events, same sequence


def test_in_flight_never_exceeds_n(vis, monkeypatch, frames):
    paths = frames(NAMES)
    for n in (1, 2, 3):
        fake = FakeOllama(delay=0.03, jitter=False)
        _run(vis, monkeypatch, paths, n, fake)
        assert fake.peak <= n
        if n > 1:
            assert fake.peak == n  # and it actually overlaps
        assert len(fake.calls) == len(paths)


def test_single_frame_failure_semantics_unchanged(vis, monkeypatch, frames):
    paths = frames(NAMES[:5])
    rep = paths[len(paths) // 2]
    # A light frame failing → empty result (flagged failed by ingest), others intact.
    out, _ = _run(vis, monkeypatch, paths, 2, FakeOllama(fail={paths[0]}))
    assert out[0]["description"] == "" and "error" not in out[0]
    assert out[0]["edit_reason"] == "rep-reason"  # inheritance still applied
    assert all(r["description"] for i, r in enumerate(out) if i != 0)
    # The representative failing → error result; light frames inherit None.
    out, _ = _run(vis, monkeypatch, paths, 2, FakeOllama(fail={rep}))
    assert out[2]["error"].startswith("boom")
    assert all(r.get("edit_reason") is None for i, r in enumerate(out) if i != 2)
    serial, _ = _run(vis, monkeypatch, paths, 1, FakeOllama(fail={rep}))
    assert out == serial


def test_unexpected_exception_propagates(vis, monkeypatch, frames):
    paths = frames(NAMES[:5])

    def explode(p, max_retries=2, model=None):
        if p == paths[3]:
            raise RuntimeError("unexpected")
        r = vis._empty_result()
        r["description"] = "ok"
        return r

    monkeypatch.setattr(vis, "_describe_one_light", explode)
    monkeypatch.setattr(vis.config, "VISION_CONCURRENCY", 2, raising=False)
    monkeypatch.setattr(vis, "vision", FakeOllama())
    with pytest.raises(RuntimeError, match="unexpected"):
        vis.describe_frames(paths)


def test_n1_runs_the_serial_loop(vis, monkeypatch, frames):
    """Concurrency 1 must not touch a thread pool at all."""
    paths = frames(NAMES[:5])

    class NoPool:
        def __init__(self, *a, **k):
            raise AssertionError("thread pool used at concurrency 1")

    monkeypatch.setattr(vis, "ThreadPoolExecutor", NoPool)
    fake = FakeOllama(jitter=False)
    out, _ = _run(vis, monkeypatch, paths, 1, fake)
    assert [r["file"] for r in out] == paths
    # Serial call order: representative first, then the rest in frame order.
    rep = paths[len(paths) // 2]
    assert [c[0] for c in fake.calls] == [rep] + [p for p in paths if p != rep]


def test_model_threaded_through_concurrent_path(vis, monkeypatch, frames):
    paths = frames(NAMES[:3])
    seen = []

    def fake(prompt, image_b64, model=None, num_ctx=None):
        seen.append(model)
        return {"text": json.dumps({"description": "d", "tags": []})}

    monkeypatch.setattr(vis.config, "VISION_CONCURRENCY", 2, raising=False)
    monkeypatch.setattr(vis, "vision", fake)
    vis.describe_frames(paths, model="minicpm-v:latest")
    assert seen == ["minicpm-v:latest"] * 3


@pytest.mark.parametrize("raw,expected", [(0, 1), (-3, 1), (1, 1), (2, 2), (99, 8), ("x", 2), (None, 2)])
def test_concurrency_clamped(vis, monkeypatch, raw, expected):
    monkeypatch.setattr(vis.config, "VISION_CONCURRENCY", raw, raising=False)
    assert vis.vision_concurrency() == expected


def test_default_is_two():
    import config
    import os
    if "ARKIV_VISION_CONCURRENCY" in os.environ:
        pytest.skip("env override present")
    assert config.VISION_CONCURRENCY == 2
