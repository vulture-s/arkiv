"""Phase 11.5e — arkiv_cli status + queue subcommands."""
import importlib
import json

import pytest


@pytest.fixture
def cli(tmp_db, monkeypatch):
    # Probe never raises, but a real probe hits localhost ollama/nvidia with a
    # timeout — disable it so status tests are fast + deterministic (it returns a
    # degraded no-op result, which is enough to exercise the CLI formatting).
    monkeypatch.setenv("ARKIV_PROBE_DISABLE", "1")
    import db, jobs, resource_probe  # noqa: F401 — ensure rebind to tmp_db
    importlib.reload(resource_probe)
    return importlib.reload(importlib.import_module("arkiv_cli"))


def test_status_json_has_resource_queue_decision(cli, capsys):
    import jobs
    jobs.enqueue("vision", "/tmp/a.mp4")
    rc = cli.main(["status", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "resource" in out and "queue" in out
    assert out["decision"] in ("PROCEED", "WAIT")
    assert out["queue"]["pending"] == 1
    # active_jobs is injected from the queue (pending+running)
    assert out["resource"]["active_jobs"] == 1


def test_status_human_readable(cli, capsys):
    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "decision:" in out
    assert "queue:" in out


def test_queue_list_json(cli, capsys):
    import jobs
    jobs.enqueue("transcode", "/tmp/a.mov")
    jobs.enqueue("whisper", "/tmp/b.wav")
    rc = cli.main(["queue", "list", "--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["type"] for r in rows} == {"transcode", "whisper"}


def test_queue_list_status_filter(cli, capsys):
    import jobs
    jid = jobs.enqueue("vision")
    jobs.mark_failed(jid, "boom")
    jobs.enqueue("embed")  # pending
    rc = cli.main(["queue", "list", "--status", "failed", "--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1 and rows[0]["type"] == "vision"


def test_queue_cancel(cli, capsys):
    import jobs
    jid = jobs.enqueue("vision")
    rc = cli.main(["queue", "cancel", str(jid)])
    assert rc == 0
    assert "cancelled" in capsys.readouterr().out
    assert jobs.counts()["cancelled"] == 1


def test_queue_cancel_absent_returns_1(cli, capsys):
    rc = cli.main(["queue", "cancel", "999"])
    assert rc == 1
    assert "not cancellable" in capsys.readouterr().out


def test_queue_retry_failed(cli, capsys):
    import jobs
    jid = jobs.enqueue("whisper")
    jobs.mark_failed(jid, "boom")
    rc = cli.main(["queue", "retry", str(jid)])
    assert rc == 0
    assert "re-queued" in capsys.readouterr().out
    assert jobs.counts()["pending"] == 1
    assert jobs.counts()["failed"] == 0


def test_queue_retry_pending_returns_1(cli, capsys):
    import jobs
    jid = jobs.enqueue("whisper")  # pending, not failed/cancelled
    rc = cli.main(["queue", "retry", str(jid)])
    assert rc == 1
    assert "not retryable" in capsys.readouterr().out
