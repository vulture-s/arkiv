"""前端 build 有沒有跟上原始碼 —— 而這個缺口在 fleet 已有的兩道守衛之間。

2026-09-08 實犯：部署在 `~/.arkiv` 的 `frontend/dist` 是 **7 月 16 日**建的，
於是 :8501 有兩個月 serve 的是七月的前端 —— v1.2.0 與 v1.3.0 的 UI 改動
從來沒出現在畫面上。`dist/` 是 gitignored，所以 repo、安裝器、升級路徑
沒有任何一環會發現它落後了。

既有的兩道守衛各自防的是：
    DEPLOY gate      「commit 了不等於上線」
    CLAUDE.md #8     「pull 了不等於在跑」
缺的是第三面：「**跑起來了不等於畫面是新的**」。
"""
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from health import spa_freshness  # noqa: E402


def _repo(tmp_path, src_mtime_offset=0):
    """建一個最小的 git repo，frontend/src 有一顆 commit。"""
    r = tmp_path / "repo"
    (r / "frontend" / "src").mkdir(parents=True)
    (r / "frontend" / "src" / "App.svelte").write_text("x", encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    import os
    env = dict(os.environ, **env)
    subprocess.run(["git", "init", "-q"], cwd=r, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=r, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "src"], cwd=r, check=True, env=env)
    return r


def _build(repo, mtime):
    d = repo / "frontend" / "dist"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "index.html"
    f.write_text("<html>", encoding="utf-8")
    import os
    os.utime(f, (mtime, mtime))
    return f


def test_dist_newer_than_last_src_commit_is_ok(tmp_path):
    r = _repo(tmp_path)
    _build(r, time.time() + 60)
    status, _ = spa_freshness(r)
    assert status == "ok"


def test_dist_older_than_last_src_commit_is_stale(tmp_path):
    """🔴 本檔的核心：這正是 2026-09-08 那個兩個月沒人發現的狀態。"""
    r = _repo(tmp_path)
    _build(r, time.time() - 86400 * 60)      # 60 天前建的
    status, detail = spa_freshness(r)
    assert status == "stale"
    assert "npm run build" in detail, detail
    assert "days stale" in detail


def test_missing_dist_is_not_ok(tmp_path):
    """沒 build 過不可以被讀成通過 —— UI 會退回舊頁或 404。"""
    r = _repo(tmp_path)
    status, detail = spa_freshness(r)
    assert status == "missing"
    assert "npm run build" in detail


def test_no_git_history_is_unmeasurable_not_ok(tmp_path):
    """🔴 量不到必須是自己一種狀態，不可以折進『新鮮』。

    打包安裝沒有 git 歷史 ⇒ 無法為原始碼定日期。把它當成 ok，
    等於讓「我不知道」長得跟「沒問題」一樣 —— 那正是本 repo 反覆踩的形狀。
    """
    r = tmp_path / "nogit"
    (r / "frontend" / "src").mkdir(parents=True)
    _build(r, time.time())
    status, detail = spa_freshness(r)
    assert status == "unmeasurable"
    assert "git" in detail.lower()


def test_packaged_install_without_src_is_unmeasurable(tmp_path):
    r = tmp_path / "packaged"
    (r / "frontend").mkdir(parents=True)
    status, _ = spa_freshness(r)
    assert status == "unmeasurable"


def test_fresh_clone_does_not_report_stale(tmp_path):
    """負向：整棵樹同一個 mtime（fresh clone 的樣子）不可以被判 stale。

    這是刻意不拿 src 的 mtime 去比的理由 —— 那樣每台新機器都會誤報，
    而一個常誤報的守衛會被忽略，等於沒有。
    """
    r = _repo(tmp_path)
    now = time.time()
    import os
    for p in (r / "frontend" / "src").rglob("*"):
        os.utime(p, (now, now))
    _build(r, now)
    assert spa_freshness(r)[0] == "ok"
