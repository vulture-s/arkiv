"""Progress reporting: visible middle, unchanged contract.

`retry-vision` and `retranscribe` are blocking POSTs that run for minutes with
nothing to show. The obvious fix — thread a `progress_cb` down into
`vision.describe_frames` and `transcribe.transcribe` — changes both signatures,
every call site, and every test that calls them. A context-bound sink changes none
of that: the worker reports, and whether anyone listens is not its problem.

What these tests defend:

* a worker with no listener behaves exactly as before (CLI, ingest.py, tests);
* two concurrent jobs do not see each other's progress;
* a broken sink cannot break the work it describes;
* a job that finishes between two polls is still reportable;
* the POST's request/response shape is untouched.
"""
from __future__ import annotations

import importlib
import threading

import pytest

import progress
import state


@pytest.fixture(autouse=True)
def _fresh_registries():
    """`state.vision_jobs` is process-global and `routers.media` imported it BY NAME,
    so rebinding `state.vision_jobs` would not reach the route. Clear it in place
    instead — otherwise one test's finished record makes another's "idle" assertion
    depend on file order."""
    for reg in (state.vision_jobs, state.transcribe_jobs):
        with reg._lock:
            reg._jobs.clear()
            reg._done_order.clear()
    yield


# ── the sink ─────────────────────────────────────────────────────────────────

def test_reporting_with_no_listener_is_a_silent_no_op():
    """The common case by far: CLI runs, ingest.py, every existing test."""
    assert progress.report(stage="x", done=1) is None


def test_a_bound_sink_receives_the_fields():
    seen = []
    with progress.capture(seen.append):
        progress.report(stage="frames", done=3, total=10)

    assert seen == [{"stage": "frames", "done": 3, "total": 10}]


def test_the_sink_is_unbound_again_after_the_block():
    seen = []
    with progress.capture(seen.append):
        progress.report(a=1)
    progress.report(a=2)

    assert seen == [{"a": 1}]


def test_a_broken_sink_cannot_break_the_work():
    """A progress bar must never take down a transcode."""
    def explode(_ev):
        raise RuntimeError("the UI went away")

    with progress.capture(explode):
        progress.report(stage="frames")  # must not raise


def test_the_block_s_own_exception_still_propagates():
    """Swallowing the worker's exception would be catastrophic and is easy to do
    by accident when `__exit__` returns something truthy."""
    with pytest.raises(ValueError):
        with progress.capture(lambda _ev: None):
            raise ValueError("real failure")


def test_two_threads_do_not_see_each_other_s_sink():
    """Two clips can be worked on at once. A module-global sink would cross-wire
    their progress; contextvars is what makes id-threading unnecessary."""
    a, b = [], []
    barrier = threading.Barrier(2)

    def work(sink, tag):
        with progress.capture(sink.append):
            barrier.wait(timeout=5)  # (1) both sinks bound before either reports
            progress.report(tag=tag)
            barrier.wait(timeout=5)  # (2) neither unbinds before both have reported
    # Both barriers matter. Without (2) a module-global sink would still pass by
    # luck: whichever thread finishes first restores the other's sink on the way
    # out, so the slower thread reports into the right list anyway.

    t1 = threading.Thread(target=work, args=(a, "A"))
    t2 = threading.Thread(target=work, args=(b, "B"))
    t1.start(); t2.start(); t1.join(5); t2.join(5)

    assert a == [{"tag": "A"}] and b == [{"tag": "B"}]


# ── the registry ─────────────────────────────────────────────────────────────

def test_a_second_start_on_the_same_id_is_refused():
    reg = state.JobRegistry()
    assert reg.start(7) is True
    assert reg.start(7) is False


def test_different_ids_run_side_by_side():
    """Per id, not global. The process-wide OOM guard is the ingest slot."""
    reg = state.JobRegistry()
    assert reg.start(1) is True and reg.start(2) is True


def test_a_finished_id_can_start_again():
    reg = state.JobRegistry()
    reg.start(7); reg.finish(7)
    assert reg.start(7) is True


def test_get_returns_a_copy_not_the_live_record():
    """The worker mutates the record while the poller reads it. Handing out the
    live dict lets a caller observe it half-updated."""
    reg = state.JobRegistry()
    reg.start(1, done=0)
    snap = reg.get(1)
    reg.update(1, done=5)

    assert snap["done"] == 0
    assert reg.get(1)["done"] == 5


def test_a_finished_job_is_still_reportable():
    """If terminal records vanished, a job that finished between two polls would
    leave the UI's last observation at 'running' forever."""
    reg = state.JobRegistry()
    reg.start(1, total=10)
    reg.finish(1, patched=10)

    assert reg.get(1) == {"state": "done", "total": 10, "patched": 10}


def test_finished_records_are_evicted_oldest_first():
    reg = state.JobRegistry(max_done=2)
    for i in range(4):
        reg.start(i); reg.finish(i)

    assert reg.get(0) is None and reg.get(1) is None
    assert reg.get(2) is not None and reg.get(3) is not None


def test_an_unknown_id_is_none_not_an_error():
    assert state.JobRegistry().get(999) is None


def test_a_late_update_on_a_finished_job_is_ignored():
    """Otherwise a straggler report resurrects a completed record as 'running'."""
    reg = state.JobRegistry()
    reg.start(1); reg.finish(1, patched=3)
    reg.update(1, done=99)

    assert reg.get(1)["state"] == "done"
    assert "done" not in reg.get(1)


# ── end to end through the route ─────────────────────────────────────────────

def _seed_with_empty_frames(db, sample_record, tmp_path, n=4):
    db.upsert(sample_record(path=str(tmp_path / "v.mp4"), filename="v.mp4"))
    with db.get_conn() as conn:
        for i in range(n):
            thumb = tmp_path / "f{0}.jpg".format(i)
            thumb.write_bytes(b"\xff\xd8\xff")
            conn.execute(
                "INSERT INTO frames (media_id, frame_index, timestamp_s, thumbnail_path, description)"
                " VALUES (?,?,?,?,?)", (1, i, float(i), str(thumb), ""))
    return 1


def test_status_reports_idle_for_a_clip_that_never_ran(fastapi_client, server_module):
    r = fastapi_client.get("/api/media/1/retry-vision/status")
    assert r.status_code == 200
    assert r.json() == {"state": "idle"}


def test_the_route_reports_frame_progress_and_then_finishes(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    db = importlib.import_module("db")
    mid = _seed_with_empty_frames(db, sample_record, tmp_path, n=4)

    seen = []
    import vision as vis

    def fake_describe(paths, model=None):
        # Report the way the real loop does, and snapshot what a poller would see.
        import progress as pr
        out = []
        for i, p in enumerate(paths):
            pr.report(stage="frames", done=i, total=len(paths))
            seen.append(fastapi_client.get(
                "/api/media/%d/retry-vision/status" % mid).json())
            out.append({"description": "描述 {0}".format(i), "tags": ["a"], "file": p})
        return out

    monkeypatch.setattr(vis, "describe_frames", fake_describe)

    r = fastapi_client.post("/api/media/%d/retry-vision" % mid)

    assert r.status_code == 200
    # Contract unchanged: same keys the UI already reads.
    assert set(r.json()) >= {"ok", "patched", "still_empty", "total_frames"}
    # Mid-flight the poller saw real, advancing numbers.
    running = [s for s in seen if s.get("state") == "running"]
    assert running, seen
    assert [s.get("done") for s in running] == [0, 1, 2, 3]
    assert all(s.get("total") == 4 for s in running)
    # And afterwards the record is terminal, not stuck at 'running'.
    after = fastapi_client.get("/api/media/%d/retry-vision/status" % mid).json()
    assert after["state"] == "done" and after["patched"] == 4


def test_a_second_retry_on_the_same_clip_is_refused_while_one_runs(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    db = importlib.import_module("db")
    mid = _seed_with_empty_frames(db, sample_record, tmp_path, n=2)
    import vision as vis

    inner = {}

    def fake_describe(paths, model=None):
        # `reentered` stops the nested POST from calling this again: without the
        # 409 guard the second run would recurse forever, and a test that hangs
        # proves nothing about WHY it failed.
        if not inner.get("reentered"):
            inner["reentered"] = True
            inner["status"] = fastapi_client.post(
                "/api/media/%d/retry-vision" % mid).status_code
        return [{"description": "d", "tags": [], "file": p} for p in paths]

    monkeypatch.setattr(vis, "describe_frames", fake_describe)
    fastapi_client.post("/api/media/%d/retry-vision" % mid)

    assert inner["status"] == 409


def test_a_crashed_run_does_not_wedge_the_clip_forever(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    """A claim left behind by a crash would make this clip un-retryable until the
    server restarts."""
    db = importlib.import_module("db")
    mid = _seed_with_empty_frames(db, sample_record, tmp_path, n=2)
    import vision as vis
    monkeypatch.setattr(vis, "describe_frames",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ollama died")))

    with pytest.raises(RuntimeError):
        fastapi_client.post("/api/media/%d/retry-vision" % mid)

    assert state.vision_jobs.get(mid)["state"] == "error"
    assert state.vision_jobs.start(mid) is True
    state.vision_jobs.finish(mid)


def test_the_real_vision_loop_reports_each_frame(monkeypatch):
    """Pins the `report()` calls inside `describe_frames` itself. The route-level
    test above drives a fake `describe_frames`, so it cannot see these — remove the
    reports from the real loop and it stays green."""
    import vision as vis

    monkeypatch.setattr(vis, "_describe_one",
                        lambda p, model=None: {"description": "rep", "tags": []})
    monkeypatch.setattr(vis, "_describe_one_light",
                        lambda p, model=None: {"description": "light", "tags": []})
    monkeypatch.setattr(vis, "_is_usable_frame", lambda p: True)

    seen = []
    with progress.capture(seen.append):
        vis.describe_frames(["a.jpg", "b.jpg", "c.jpg"])

    # The representative frame runs BEFORE the loop and is the slowest call, so it
    # gets its own report — otherwise the UI sits at nothing through it.
    assert seen[0] == {"stage": "representative", "done": 0, "total": 3}
    frames = [e for e in seen if e.get("stage") == "frames"]
    assert [e["done"] for e in frames] == [0, 1, 2, 3]
    assert all(e["total"] == 3 for e in frames)


# ── transcribe stages ────────────────────────────────────────────────────────
# Stages, not a percentage. Whisper decoding is one opaque call per file, so
# "decoding" for four minutes is the truth and a bar creeping to 90% is not.

def _seed_playable(db, sample_record, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"\x00")
    db.upsert(sample_record(path=str(media), filename="clip.mp4", ext=".mp4", has_audio=1))
    return 1


def test_transcribe_reports_each_stage_it_passes_through(monkeypatch, tmp_path):
    """Pins the reports inside `transcribe.transcribe` itself, with the backend and
    the wav extraction stubbed — the stage names are the contract the UI reads."""
    import transcribe as tr

    monkeypatch.setattr(tr, "_USE_MLX", False)
    monkeypatch.setattr(tr, "_non_mac_backend", lambda: "faster-whisper")
    monkeypatch.setattr(tr, "_to_wav", lambda p: str(tmp_path / "a.wav"))
    monkeypatch.setattr(tr, "_vad_filter", lambda w: (w, None))
    monkeypatch.setattr(tr, "_transcribe_faster_whisper",
                        lambda w, lang: ("句子", "zh", [], []))

    seen = []
    with progress.capture(seen.append):
        tr.transcribe("/clip.mp4", language="zh")

    stages = [e["stage"] for e in seen if "stage" in e]
    assert stages[:3] == ["extracting", "vad", "decoding"]


def test_the_status_endpoint_shows_the_stage_mid_run(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    db = importlib.import_module("db")
    mid = _seed_playable(db, sample_record, tmp_path)
    import transcribe as tr

    seen = []

    def fake_transcribe(path, language=None):
        for stage in ("extracting", "decoding", "polishing"):
            progress.report(stage=stage)
            seen.append(fastapi_client.get(
                "/api/media/%d/retranscribe/status" % mid).json())
        return "新的逐字稿", "zh", [], []

    monkeypatch.setattr(tr, "transcribe", fake_transcribe)

    r = fastapi_client.post("/api/media/%d/retranscribe" % mid, json={"language": "zh"})

    assert r.status_code == 200, r.text
    assert [s.get("stage") for s in seen] == ["extracting", "decoding", "polishing"]
    assert all(s.get("state") == "running" for s in seen)
    # No percentage is claimed anywhere — the UI has stage names to show, not a bar.
    assert not any("percent" in s for s in seen)
    after = fastapi_client.get("/api/media/%d/retranscribe/status" % mid).json()
    assert after["state"] == "done" and after["stage"] == "done"


def test_a_second_retranscribe_is_refused_after_the_slot_is_already_free(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    """The window the per-id claim actually covers, and the only one it covers.

    While whisper runs, the process-wide ingest slot already refuses everything, so
    the claim adds nothing there. But the slot is released the moment decoding ends
    — deliberately, because the DB writes that follow hold no model — and during
    those writes a second POST would sail past the slot and start overwriting the
    same media row. The claim is what stops that, and this drives the request from
    inside the write phase so the window is hit deterministically."""
    db = importlib.import_module("db")
    mid = _seed_playable(db, sample_record, tmp_path)
    import transcribe as tr

    monkeypatch.setattr(tr, "transcribe", lambda *a, **k: ("文字", "zh", [], []))

    inner = {}
    real_upsert = db.upsert_transcript

    def spy_upsert(*a, **k):
        if not inner.get("reentered"):
            inner["reentered"] = True
            import server
            # The slot really is free at this point — that is the premise.
            assert server._acquire_ingest_slot() is True
            server._release_ingest_slot()
            inner["status"] = fastapi_client.post(
                "/api/media/%d/retranscribe" % mid, json={"language": "zh"}).status_code
        return real_upsert(*a, **k)

    monkeypatch.setattr(db, "upsert_transcript", spy_upsert)
    fastapi_client.post("/api/media/%d/retranscribe" % mid, json={"language": "zh"})

    assert inner["status"] == 409


def test_losing_the_ingest_slot_does_not_leave_a_stale_claim(
    fastapi_client, server_module, sample_record, tmp_path
):
    """The per-id claim is taken BEFORE the process-wide slot. If the slot is busy
    the request 409s — and must hand the claim back, or this clip is wedged until
    a restart even though it never ran."""
    import server
    db = importlib.import_module("db")
    mid = _seed_playable(db, sample_record, tmp_path)

    assert server._acquire_ingest_slot() is True
    try:
        r = fastapi_client.post("/api/media/%d/retranscribe" % mid, json={"language": "zh"})
        assert r.status_code == 409
    finally:
        server._release_ingest_slot()

    assert state.transcribe_jobs.get(mid)["state"] != "running"
    assert state.transcribe_jobs.start(mid) is True
    state.transcribe_jobs.finish(mid)


def test_a_failed_retranscribe_clears_its_claim(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    db = importlib.import_module("db")
    mid = _seed_playable(db, sample_record, tmp_path)
    import transcribe as tr
    monkeypatch.setattr(tr, "transcribe",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    r = fastapi_client.post("/api/media/%d/retranscribe" % mid, json={"language": "zh"})

    assert r.status_code == 500
    assert state.transcribe_jobs.get(mid)["state"] == "error"
    assert state.transcribe_jobs.start(mid) is True
    state.transcribe_jobs.finish(mid)


def test_a_refused_empty_result_also_clears_its_claim(
    fastapi_client, server_module, sample_record, tmp_path, monkeypatch
):
    """The 422 path (transcribe returned nothing for a clip that already has a
    transcript) is a third exit from the handler and leaks just as easily."""
    db = importlib.import_module("db")
    mid = _seed_playable(db, sample_record, tmp_path)
    import transcribe as tr
    monkeypatch.setattr(tr, "transcribe", lambda *a, **k: ("", "zh", [], []))

    r = fastapi_client.post("/api/media/%d/retranscribe" % mid, json={"language": "zh"})

    assert r.status_code == 422
    assert state.transcribe_jobs.start(mid) is True
    state.transcribe_jobs.finish(mid)
