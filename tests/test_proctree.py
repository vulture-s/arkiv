"""proctree.run_tree — process-tree-safe subprocess.run (fable-audit round-5 #2/#12).

The load-bearing property: on timeout the WHOLE tree dies, not just the direct
child. Pre-fix, /api/ingest's plain subprocess.run(timeout=) killed ingest.py but
orphaned its ffmpeg/whisper grandchildren, which kept writing after the slot was
released.
"""
import os
import subprocess
import sys
import time

import pytest

import proctree


def test_run_tree_basic_capture_and_returncode(tmp_path):
    r = proctree.run_tree([sys.executable, "-c", "print('hi'); import sys; sys.exit(3)"], timeout=30)
    assert r.returncode == 3
    assert "hi" in r.stdout


def test_run_tree_honours_cwd_and_env(tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    env = dict(os.environ)
    env["ARKIV_PROCTREE_TEST"] = "sentinel-value"
    r = proctree.run_tree(
        [sys.executable, "-c",
         "import os; print(os.path.exists('marker.txt')); print(os.environ.get('ARKIV_PROCTREE_TEST'))"],
        timeout=30, cwd=str(tmp_path), env=env,
    )
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert lines[0] == "True"                 # ran in cwd
    assert lines[1] == "sentinel-value"       # env passed through


def test_watch_reexports_proctree_run_tree():
    import watch
    assert watch.run_tree is proctree.run_tree


@pytest.mark.skipif(os.name != "posix", reason="POSIX session/killpg tree-kill assertion")
def test_run_tree_kills_grandchild_on_timeout():
    # parent spawns a long-sleeping grandchild, prints its pid, then sleeps too.
    parent = (
        "import subprocess,sys,time;"
        "g=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "print(g.pid,flush=True);"
        "time.sleep(60)"
    )
    with pytest.raises(subprocess.TimeoutExpired) as ei:
        proctree.run_tree([sys.executable, "-c", parent], timeout=1.0)
    out = ei.value.output or ""
    gpid = int(out.strip().splitlines()[0])

    # the grandchild must be gone — killpg took the whole session, not just ingest.py
    dead = False
    for _ in range(50):
        try:
            os.kill(gpid, 0)
        except ProcessLookupError:
            dead = True
            break
        time.sleep(0.1)
    assert dead, "grandchild {0} survived the timeout kill".format(gpid)
