"""Round-1 audit of the ingest budget/watchdog work (#436/#437/#438).

Each test pins one finding that was verified against the real code. They share
a shape: the failure is silent — the run is killed, or reported as halted, or
never starts — and nothing in the output says which of the two deadlines, or
which process, or which env var was responsible.
"""
import os
import importlib
import pytest

import ingest_budget
import proctree


# ── ①② 預算與靜默門檻互相矛盾 ────────────────────────────────
@pytest.mark.parametrize("dur", [1800.0, 3600.0, 7200.0])
def test_budget_is_never_shorter_than_the_silence_it_allows(dur):
    """stall_seconds 說「這麼久的沉默是合法的」，budget 就不能比它短。

    estimate_seconds 只模型化 vision 的每幀成本，轉錄完全沒進去；stall_seconds
    卻明白知道轉錄跟片長成正比。兩者矛盾時，長素材會在還健康地轉錄時被判
    budget 超時，而 stall 那條路徑變成不可達的 dead code。
    """
    est = ingest_budget.estimate_seconds([dur], n_files=1)
    stall = ingest_budget.stall_seconds(dur)
    budget = ingest_budget.budget_seconds(est, max_duration_s=dur)
    assert budget >= stall, (
        "budget {0:.0f}s < stall {1:.0f}s — 長素材必然在健康轉錄中被殺，"
        "而 stall watchdog 永遠碰不到".format(budget, stall))


def test_budget_without_duration_keeps_old_behaviour():
    est = ingest_budget.estimate_seconds([10.0] * 5, n_files=5)
    assert ingest_budget.budget_seconds(est) >= 0


# ── ⑬ 一個打錯的環境變數不該讓整個 API 起不來 ──────────────────
@pytest.mark.parametrize("bad", ["30m", "", "abc", "1,800"])
def test_malformed_tuning_env_does_not_break_import(monkeypatch, bad):
    monkeypatch.setenv("ARKIV_INGEST_BUDGET_FLOOR", bad)
    import config
    importlib.reload(config)          # 不該拋
    assert config.INGEST_BUDGET_FLOOR_SECONDS > 0
    monkeypatch.delenv("ARKIV_INGEST_BUDGET_FLOOR")
    importlib.reload(config)


def test_budget_factor_below_one_is_clamped(monkeypatch):
    """係數小於 1 會讓「安全邊際」比它要保護的估值還小。"""
    monkeypatch.setenv("ARKIV_INGEST_BUDGET_FACTOR", "0.5")
    import config
    importlib.reload(config)
    assert config.INGEST_BUDGET_FACTOR >= 1.0
    monkeypatch.delenv("ARKIV_INGEST_BUDGET_FACTOR")
    importlib.reload(config)


# ── ⑤ 三份 tree-kill 不可以各修各的 ──────────────────────────
def test_ingest_kill_delegates_to_proctree(monkeypatch):
    """proctree._kill_tree 的 docstring 說它被抽出來就是為了避免兩邊漂移，
    而 routers/ingest.py 曾經是第三份手寫複本、非 POSIX 分支沒有 taskkill。"""
    from routers import ingest as ring
    called = {}
    monkeypatch.setattr(proctree, "_kill_tree", lambda p: called.setdefault("hit", True))

    class _P:
        pid = 424242
        def kill(self): called["fallback"] = True
    ring._kill_ingest_tree(_P())
    assert called.get("hit"), "應該委派給 proctree._kill_tree"


# ── ⑦ pump thread 死掉比掉一行字嚴重得多 ──────────────────────
def test_watched_run_decodes_leniently():
    """一個非 UTF-8 byte 曾讓 pump thread 靜默結束 → 沒人排空管線 →
    子程序卡在寫入 → watchdog 殺掉一個健康的程序並回報「卡住」。"""
    import inspect
    src = inspect.getsource(proctree.run_tree_watched)
    assert '"errors"' in src or "errors=" in src, \
        "run_tree_watched 的 Popen 必須寬容解碼"


def test_pump_failure_is_recorded_not_swallowed():
    import inspect
    src = inspect.getsource(proctree.run_tree_watched)
    assert "pump_error" in src, "pump 的例外要留下痕跡，不能 except: pass"
