"""Server-spawned workers must not outlive the server (audit 2026-10-09 N1).

The desktop shell stops the backend with Child::kill() — SIGKILL — and every
ingest worker runs in its own session (start_new_session, so a timeout can
killpg the tree). Together that meant quitting the app mid-ingest left
ingest.py + whisper + ffmpeg running, re-parented to launchd, still writing the
DB; reopening the app found an empty in-memory single-flight slot and happily
started a second whisper.

`orphan_guard` makes a worker notice that the process which spawned it is gone
and take its own process group down with it.
"""
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a reaped-but-not-yet-collected zombie still answers kill(0); ask ps
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, encoding="utf-8").stdout.strip()
    return bool(out) and not out.startswith("Z")


@pytest.mark.skipif(os.name != "posix", reason="POSIX re-parenting semantics")
def test_worker_and_its_grandchild_die_when_server_is_sigkilled(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text(textwrap.dedent("""
        import subprocess, sys, time
        sys.path.insert(0, {root!r})
        import orphan_guard
        assert orphan_guard.install(poll_s=0.1)
        g = subprocess.Popen(["sleep", "30"])   # stands in for ffmpeg / whisper
        print(g.pid, flush=True)
        time.sleep(30)
    """).format(root=str(ROOT)))
    server = tmp_path / "server.py"
    server.write_text(textwrap.dedent("""
        import subprocess, sys, time
        sys.path.insert(0, {root!r})
        import proctree
        # the real spawn helper — same session/env handling as /api/ingest
        proctree.run_tree([sys.executable, {worker!r}], timeout=30)
    """).format(root=str(ROOT), worker=str(worker)))

    srv = subprocess.Popen([sys.executable, str(server)], stdout=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        # the worker's stdout is captured by run_tree, so find it via ps instead
        deadline = time.time() + 10
        worker_pid = gpid = None
        while time.time() < deadline and gpid is None:
            ps = subprocess.run(["ps", "-ax", "-o", "pid=,ppid=,command="], capture_output=True, text=True, encoding="utf-8").stdout
            for line in ps.splitlines():
                parts = line.split(None, 2)
                if len(parts) == 3 and parts[1] == str(srv.pid) and str(worker) in parts[2]:
                    worker_pid = int(parts[0])
            if worker_pid:
                for line in ps.splitlines():
                    parts = line.split(None, 2)
                    if len(parts) == 3 and parts[1] == str(worker_pid) and parts[2].startswith("sleep 30"):
                        gpid = int(parts[0])
            time.sleep(0.1)
        assert worker_pid and gpid, "worker/grandchild never started"

        os.kill(srv.pid, signal.SIGKILL)  # what Tauri's child.kill() does
        srv.wait(timeout=5)

        deadline = time.time() + 5
        while time.time() < deadline and (_alive(worker_pid) or _alive(gpid)):
            time.sleep(0.1)
        assert not _alive(worker_pid), "ingest worker outlived the server"
        assert not _alive(gpid), "worker's ffmpeg/whisper grandchild outlived the server"
    finally:
        for pid in (srv.pid,):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        subprocess.run(["pkill", "-f", str(worker)], capture_output=True)


def test_spawn_helpers_tell_the_child_who_its_server_is():
    import proctree

    code = "import os; print(os.environ.get('ARKIV_PARENT_PID'))"
    r = proctree.run_tree([sys.executable, "-c", code], timeout=30)
    assert r.stdout.strip() == str(os.getpid())
    r = proctree.run_tree_watched([sys.executable, "-c", code], stall_timeout=30, total_timeout=30)
    assert r.stdout.strip() == str(os.getpid())
    # an explicit env is extended, not replaced
    r = proctree.run_tree([sys.executable, "-c", code + "; print(os.environ.get('X_KEEP'))"],
                          timeout=30, env={**os.environ, "X_KEEP": "1"})
    assert r.stdout.split() == [str(os.getpid()), "1"]


def test_install_is_inert_without_a_server(monkeypatch):
    import orphan_guard

    monkeypatch.delenv("ARKIV_PARENT_PID", raising=False)
    assert orphan_guard.install(poll_s=0.01) is False  # plain CLI / nohup runs untouched
    monkeypatch.setenv("ARKIV_PARENT_PID", "not-a-pid")
    assert orphan_guard.install(poll_s=0.01) is False
    # spawned through an intermediary: don't guess, don't arm
    monkeypatch.setenv("ARKIV_PARENT_PID", str(os.getppid() + 999999))
    assert orphan_guard.install(poll_s=0.01) is False


@pytest.mark.parametrize("module,argv", [
    ("ingest", ["ingest.py", "--help"]),
    ("offload", None),
])
def test_worker_entry_points_arm_the_guard_first(monkeypatch, module, argv):
    import importlib
    import orphan_guard

    calls = []
    monkeypatch.setattr(orphan_guard, "install", lambda *a, **k: calls.append(1) or False)
    mod = importlib.import_module(module)
    if argv is not None:
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit):
            mod.main()
    else:
        with pytest.raises(SystemExit):
            mod.main(["--help"])
    assert calls == [1]
