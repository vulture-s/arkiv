"""Phase 11.5c — jobs queue tests (real SQLite via tmp_db fixture)."""
import importlib

import pytest


@pytest.fixture
def jq(tmp_db):
    # jobs.py uses db.get_conn(); tmp_db has already pointed db.DB_PATH at tmp.
    jobs = importlib.import_module("jobs")
    return importlib.reload(jobs)


# --------------------------------------------------------------------------
# enqueue + priority
# --------------------------------------------------------------------------
def test_enqueue_returns_id_and_priority(jq):
    jid = jq.enqueue("vision", target="clip.mp4")
    rows = jq.list_jobs()
    assert len(rows) == 1
    assert rows[0]["id"] == jid
    assert rows[0]["priority"] == jq.PRIORITY["vision"]
    assert rows[0]["status"] == jq.PENDING
    assert rows[0]["target"] == "clip.mp4"


def test_unknown_type_goes_to_back(jq):
    jq.enqueue("transcode")
    weird = jq.enqueue("mystery")
    # transcode (1) dequeues before unknown (99)
    nxt = jq.next_pending()
    assert nxt["type"] == "transcode"
    assert jq.priority_for("mystery") == jq._DEFAULT_PRIORITY


def test_next_pending_orders_by_priority_then_fifo(jq):
    jq.enqueue("whisper")    # priority 4
    jq.enqueue("transcode")  # priority 1  <- should come first
    jq.enqueue("embed")      # priority 2
    nxt = jq.next_pending()
    assert nxt["type"] == "transcode"


def test_next_pending_fifo_within_tier(jq):
    a = jq.enqueue("vision", target="a")
    b = jq.enqueue("vision", target="b")
    nxt = jq.next_pending()
    assert nxt["id"] == a and nxt["target"] == "a"
    assert b  # second one still pending


def test_next_pending_empty_returns_none(jq):
    assert jq.next_pending() is None


# --------------------------------------------------------------------------
# status transitions
# --------------------------------------------------------------------------
def test_running_done_lifecycle(jq):
    jid = jq.enqueue("whisper")
    jq.mark_running(jid)
    assert jq.list_jobs(status=jq.RUNNING)[0]["id"] == jid
    # running job is not pending -> not dequeued
    assert jq.next_pending() is None
    jq.mark_done(jid)
    c = jq.counts()
    assert c[jq.DONE] == 1 and c[jq.RUNNING] == 0


def test_mark_failed_records_error(jq):
    jid = jq.enqueue("vision")
    jq.mark_running(jid)
    jq.mark_failed(jid, "ollama timeout")
    row = jq.list_jobs(status=jq.FAILED)[0]
    assert row["error"] == "ollama timeout"
    assert row["finished_at"] is not None


def test_mark_failed_truncates_long_error(jq):
    jid = jq.enqueue("vision")
    jq.mark_failed(jid, "x" * 5000)
    row = jq.list_jobs(status=jq.FAILED)[0]
    assert len(row["error"]) == 2000


# --------------------------------------------------------------------------
# cancel / retry edge cases
# --------------------------------------------------------------------------
def test_cancel_pending(jq):
    jid = jq.enqueue("vision")
    assert jq.cancel(jid) is True
    assert jq.counts()[jq.CANCELLED] == 1
    # cancelled job no longer dequeues
    assert jq.next_pending() is None


def test_cancel_running(jq):
    jid = jq.enqueue("vision")
    jq.mark_running(jid)
    assert jq.cancel(jid) is True


def test_cancel_absent_returns_false(jq):
    assert jq.cancel(99999) is False


def test_cannot_cancel_done(jq):
    jid = jq.enqueue("vision")
    jq.mark_done(jid)
    assert jq.cancel(jid) is False


def test_retry_failed_requeues(jq):
    jid = jq.enqueue("vision")
    jq.mark_running(jid)
    jq.mark_failed(jid, "boom")
    assert jq.retry(jid) is True
    row = jq.list_jobs()[0]
    assert row["status"] == jq.PENDING
    assert row["error"] is None
    assert row["started_at"] is None
    # re-queued job dequeues again
    assert jq.next_pending()["id"] == jid


def test_retry_cancelled_requeues(jq):
    jid = jq.enqueue("vision")
    jq.cancel(jid)
    assert jq.retry(jid) is True


def test_cannot_retry_pending(jq):
    jid = jq.enqueue("vision")
    assert jq.retry(jid) is False


def test_cannot_retry_done(jq):
    jid = jq.enqueue("vision")
    jq.mark_done(jid)
    assert jq.retry(jid) is False


def test_retry_absent_returns_false(jq):
    assert jq.retry(99999) is False


# --------------------------------------------------------------------------
# counts / active_count
# --------------------------------------------------------------------------
def test_counts_has_all_statuses(jq):
    c = jq.counts()
    for s in (jq.PENDING, jq.RUNNING, jq.DONE, jq.FAILED, jq.CANCELLED):
        assert s in c and c[s] == 0


def test_active_count_pending_plus_running(jq):
    jq.enqueue("vision")
    r = jq.enqueue("whisper")
    jq.mark_running(r)
    d = jq.enqueue("embed")
    jq.mark_done(d)
    # 1 pending + 1 running = 2 active; done excluded
    assert jq.active_count() == 2


def test_list_jobs_limit_clamped(jq):
    for _ in range(5):
        jq.enqueue("vision")
    assert len(jq.list_jobs(limit=2)) == 2
    assert len(jq.list_jobs(limit=0)) == 1  # clamped to >=1


def test_list_jobs_orders_running_then_pending(jq):
    p = jq.enqueue("transcode")
    r = jq.enqueue("whisper")
    jq.mark_running(r)
    rows = jq.list_jobs()
    # running first regardless of priority number
    assert rows[0]["id"] == r
    assert rows[1]["id"] == p
