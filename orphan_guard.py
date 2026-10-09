"""Let a server-spawned worker die with the server that spawned it (POSIX).

Audit 2026-10-09 N1. The desktop shell stops the backend with `Child::kill()`
(SIGKILL — no handler runs), and every ingest worker is started in its OWN
session (`start_new_session`, so a timeout can `killpg` the whole tree). The two
together meant: quit the app mid-ingest and ingest.py + whisper + ffmpeg keep
running, re-parented to launchd, still writing project.db. The next launch's
single-flight slot is in-memory and empty, so a second whisper can start next to
the orphan — the 16 GB OOM the H3 slot exists to prevent.

A SIGKILLed parent cannot clean up, so the child has to notice. The spawn
helpers export ARKIV_PARENT_PID; `install()` arms a daemon thread that polls
`getppid()` and, the moment the server is gone, SIGKILLs the worker's own
process group (its session: ffmpeg, whisper subprocesses, itself). Ingest
writes go through SQLite transactions and offload through .partial + rename +
an atomically replaced state file, so a hard stop loses at most the clip in
flight, which the next run redoes.

Inert unless ARKIV_PARENT_PID is set AND equals our actual parent at start-up:
a CLI run, a `nohup` run, or a worker launched through some intermediary is
never armed — guessing wrong here would kill legitimate detached work.
Windows does not re-parent (getppid keeps returning the dead pid), so this is
POSIX-only; there the Job-object route would be the equivalent.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from typing import Mapping, Optional

ENV = "ARKIV_PARENT_PID"


def child_env(env: Optional[Mapping[str, str]] = None) -> dict:
    """Environment for a worker this process spawns: `env` (default: ours)
    plus our pid as ARKIV_PARENT_PID."""
    out = dict(os.environ if env is None else env)
    out[ENV] = str(os.getpid())
    return out


def _go_down() -> None:
    try:
        if os.getpgid(0) == os.getpid():
            # We lead our own session/group (start_new_session) — take the
            # whole tree, ourselves included.
            os.killpg(os.getpid(), signal.SIGKILL)
    except OSError:
        pass
    # Not a group leader (or killpg failed): never signal a group we do not
    # own — it could be the terminal's job or the app's. Just ourselves.
    os._exit(1)


def install(poll_s: float = 1.0) -> bool:
    """Arm the guard if we were spawned directly by an arkiv server. Returns
    whether it was armed."""
    if os.name != "posix":
        return False
    raw = os.environ.get(ENV, "")
    try:
        expected = int(raw)
    except ValueError:
        return False
    if expected <= 1 or os.getppid() != expected:
        return False

    def _watch() -> None:
        while True:
            time.sleep(poll_s)
            if os.getppid() != expected:
                _go_down()
                return

    threading.Thread(target=_watch, name="arkiv-orphan-guard", daemon=True).start()
    return True
