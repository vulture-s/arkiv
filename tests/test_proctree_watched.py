"""兩道獨立的截止線 —— 而它們必須說得出是哪一道觸發的。

固定 30 分鐘死線同時錯兩個方向：對 200 支的匯入太小（實測要 94 分鐘，
撐不到第 60 支），對「已經卡死的 ffmpeg」又太大。拆成
「靜默多久」與「總共多久」之後，兩者才各自能設對。

⚠️ 本檔刻意用秒級門檻跑真的子行程 —— 這條路徑的失效模式（緩衝、
執行緒沒收、孫行程沒被殺）在 mock 裡全部看不見。
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proctree  # noqa: E402


def _py(code):
    return [sys.executable, "-c", code]


def test_normal_run_returns_output_and_code():
    r = proctree.run_tree_watched(
        _py("import sys; print('hello'); sys.exit(3)"),
        stall_timeout=10, total_timeout=10)
    assert r.returncode == 3
    assert "hello" in r.stdout


def test_silent_child_is_killed_as_stall():
    """沒有輸出 = 停滯。這是 watchdog 的主要用途。"""
    t0 = time.time()
    with pytest.raises(proctree.TreeTimeout) as ei:
        proctree.run_tree_watched(
            _py("import time; time.sleep(60)"),
            stall_timeout=1.5, total_timeout=30)
    assert ei.value.kind == "stall"
    assert ei.value.idle >= 1.5
    assert time.time() - t0 < 15, "應該在 stall_timeout 後就殺，不是等到 total"


def test_chatty_but_overlong_child_is_killed_as_budget():
    """一直在講話但超過總預算 —— 這是「估錯了」，不是「卡住了」。

    配對驗：跟上一條的差別只有「有沒有輸出」，而兩者必須回不同的 kind。
    """
    with pytest.raises(proctree.TreeTimeout) as ei:
        proctree.run_tree_watched(
            _py("import time,sys\n"
                "for i in range(200):\n"
                "    print(i, flush=True); time.sleep(0.2)"),
            stall_timeout=5, total_timeout=1.5)
    assert ei.value.kind == "budget"
    assert ei.value.elapsed >= 1.5


def test_output_captured_up_to_the_kill():
    """殺掉時已經產出的東西不可以丟 —— 那是使用者唯一的線索。"""
    with pytest.raises(proctree.TreeTimeout) as ei:
        proctree.run_tree_watched(
            _py("import time,sys\nprint('MARKER', flush=True)\ntime.sleep(60)"),
            stall_timeout=1.5, total_timeout=30)
    assert "MARKER" in (ei.value.output or "")


def test_on_line_sees_lines_as_they_arrive():
    seen = []
    proctree.run_tree_watched(
        _py("import sys\nfor i in range(3): print('L%d' % i, flush=True)"),
        stall_timeout=10, total_timeout=10, on_line=seen.append)
    assert [x.strip() for x in seen] == ["L0", "L1", "L2"]


def test_a_broken_on_line_cannot_kill_the_run():
    """負向：消費端壞掉不可以連累匯入本身。"""
    def boom(_line):
        raise ValueError("consumer exploded")
    r = proctree.run_tree_watched(
        _py("print('still fine')"), stall_timeout=10, total_timeout=10, on_line=boom)
    assert r.returncode == 0
    assert "still fine" in r.stdout


@pytest.mark.skipif(os.name != "posix", reason="POSIX 的行程群組語意")
def test_stall_kill_takes_the_grandchild_too(tmp_path):
    """整棵樹要一起死 —— 只殺直接子行程會留下 ffmpeg/whisper 孤兒。

    孫行程把自己的 pid 寫進檔案，然後長睡；殺完之後那個 pid 必須不存在。
    """
    pidfile = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "gc = subprocess.Popen([sys.executable, '-c',\n"
        "  \"import os,time,sys; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(120)\",\n"
        "  %r])\n"
        "time.sleep(120)\n" % str(pidfile)
    )
    with pytest.raises(proctree.TreeTimeout):
        proctree.run_tree_watched(_py(code), stall_timeout=2.5, total_timeout=30)
    deadline = time.time() + 5
    pid = None
    while time.time() < deadline and pid is None:
        if pidfile.exists() and pidfile.read_text().strip():
            pid = int(pidfile.read_text().strip())
        else:
            time.sleep(0.2)
    assert pid, "孫行程沒來得及登記 pid，這條測不到東西"
    time.sleep(0.5)
    with pytest.raises(OSError):
        os.kill(pid, 0)   # 還活著的話這裡不會丟例外 → 測試失敗
