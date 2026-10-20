"""路由層：兩道截止線怎麼被算出來、以及它們如何回報。

原本 `/api/ingest` 是一個寫死的 `timeout=1800`，而 `/api/ingest/ws`
（UI 實際走的那條）**完全沒有 timeout** —— 卡住的子行程不會給 EOF，
讀取迴圈就永遠等下去，單飛槽位也一直被占著。
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proctree  # noqa: E402


def test_scan_reports_an_estimate_and_both_limits(fastapi_client, monkeypatch, tmp_path):
    monkeypatch.setenv("ARKIV_INGEST_ROOTS", str(tmp_path))
    (tmp_path / "a.mp4").write_bytes(b"\x00" * 1024)
    (tmp_path / "b.mp4").write_bytes(b"\x00" * 1024)
    r = fastapi_client.post("/api/ingest/scan", json={"path": str(tmp_path)})
    assert r.status_code == 200, r.text
    est = r.json().get("estimate")
    assert est, "掃描要能回答『這會跑多久』——否則使用者只能用猜的設參數"
    for k in ("seconds", "budget_s", "stall_s", "probed", "of"):
        assert k in est, k
    # 預算必須嚴格大於估值，否則估值本身就變成死線
    assert est["budget_s"] > est["seconds"]
    assert est["of"] == 2


def test_estimate_is_reported_even_when_nothing_can_be_probed(fastapi_client, monkeypatch, tmp_path):
    """探不到時序估不可以整個消失 —— 那會讓 UI 退回沒有資訊的狀態。"""
    monkeypatch.setenv("ARKIV_INGEST_ROOTS", str(tmp_path))
    (tmp_path / "broken.mp4").write_bytes(b"not really a video")
    r = fastapi_client.post("/api/ingest/scan", json={"path": str(tmp_path)})
    est = r.json()["estimate"]
    assert est["probed"] == 0
    assert est["seconds"] > 0, "探不到就回 0 秒，等於宣稱『瞬間完成』"


def test_timeout_detail_names_which_limit_fired():
    """兩種停止要用不同的話講 —— 使用者的下一步不一樣。"""
    from routers.ingest import _timeout_detail
    stall = _timeout_detail(proctree.TreeTimeout(
        ["x"], 600, "stall", elapsed=900, idle=700))
    budget = _timeout_detail(proctree.TreeTimeout(
        ["x"], 3600, "budget", elapsed=3700, idle=3))
    assert "停滯" in stall and "卡住" in stall
    assert "超過預估" in budget
    assert stall != budget
    # 兩者都必須說「已完成的不會丟」，否則使用者會以為要整批重來
    for msg in (stall, budget):
        assert "接續" in msg


def test_limits_fall_back_to_floors_on_an_unreadable_folder(tmp_path):
    """估算失敗不可以連累它只是在建議的那個匯入。"""
    import config
    from routers.ingest import _ingest_limits
    stall, budget = _ingest_limits(tmp_path / "does-not-exist")
    assert stall >= config.INGEST_STALL_FLOOR_SECONDS
    assert budget >= config.INGEST_BUDGET_FLOOR_SECONDS


def test_no_fixed_1800_second_deadline_remains():
    """回歸鎖：那個寫死的 30 分鐘是本次要移除的東西。

    它同時錯兩個方向 —— 200 支要 94 分鐘（撐不到第 60 支），
    而對「已經卡死」又太久。
    """
    src = Path(__file__).resolve().parents[1] / "routers" / "ingest.py"
    text = src.read_text(encoding="utf-8")
    offending = [ln for ln in text.splitlines()
                 if "timeout=1800" in ln.replace(" ", "")]
    assert not offending, offending
