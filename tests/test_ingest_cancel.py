"""匯入可以被使用者停止（/api/ingest/cancel）。

原本沒有任何停止的方法：一批大的匯入會占住單飛槽位直到跑完，
期間切換素材庫、開新的匯入都會被拒絕。
"""
import asyncio
import sys

import pytest

import routers.ingest as ri


def test_cancel_with_nothing_running_is_409(fastapi_client, monkeypatch):
    monkeypatch.setattr(ri, "_active_ws_run", None)
    r = fastapi_client.post("/api/ingest/cancel")
    assert r.status_code == 409, r.text


def test_cancel_stops_the_run_and_complete_says_cancelled(monkeypatch, tmp_path):
    """真的起一個不會自己結束的子行程，停掉它，並確認 `complete` 仍然送出、
    且帶 halted="cancelled" —— 否則畫面會把被停掉的批次當成跑完。"""
    sent = []

    async def fake_broadcast(msg):
        sent.append(msg)

    monkeypatch.setattr(ri.ingest_ws, "broadcast", fake_broadcast)
    monkeypatch.setattr(ri, "_ingest_limits", lambda *a, **k: (300, 300))
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*cmd, **kw):
        # Stands in for ingest.py: says it started, then never finishes.
        return await real_exec(
            sys.executable, "-c",
            "import sys,time; print('Found 3 media files. Processing 3', flush=True); time.sleep(120)",
            **kw,
        )

    monkeypatch.setattr(ri.asyncio, "create_subprocess_exec", fake_exec)

    async def scenario():
        task = asyncio.create_task(ri._run_ingest_with_ws(tmp_path, 0))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if ri._active_ws_run and any(m.get("type") == "start" and m.get("total") == 3 for m in sent):
                break
        assert ri._active_ws_run is not None, "the run must be registered as cancellable"
        out = await ri.cancel_ingest_ws(_tok={})
        assert out["ok"] is True
        await asyncio.wait_for(task, timeout=10)

    asyncio.run(scenario())
    types = [m.get("type") for m in sent]
    assert "halted" in types
    complete = [m for m in sent if m.get("type") == "complete"]
    assert complete and complete[-1]["halted"] == "cancelled"
    assert ri._active_ws_run is None, "a finished run must not stay cancellable"
