"""Process-tree-safe subprocess dispatch.

`run_tree` is `subprocess.run` that kills the WHOLE process tree on timeout, not
just the direct child. Extracted from watch.py (fable-audit round-5 #2) so the
long-lived watcher AND the /api/ingest + /api/media/{id}/reingest HTTP routes share
one implementation: those routes used plain `subprocess.run(timeout=)`, which on
timeout kills only `ingest.py` and orphans its ffmpeg/whisper grandchildren — they
keep running (and, mid-encode, keep writing) with the H3 ingest slot already
released (audit H8, deferred #12).

POSIX: the child gets its own session (`start_new_session`) and the timeout path
`killpg`s the group. Windows: a new process group + `taskkill /T /F`.
"""
from __future__ import annotations

import os
import signal
import subprocess
from typing import Mapping, Optional, Sequence


def run_tree(
    cmd: Sequence[str],
    timeout: float,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    text: bool = True,
    encoding: Optional[str] = None,
    errors: Optional[str] = None,
) -> "subprocess.CompletedProcess":
    """subprocess.run that kills the whole process tree on timeout.

    Mirrors subprocess.run's capture semantics (stdout/stderr piped) and honours
    cwd / env / text / encoding / errors so it is a drop-in for the existing HTTP
    call sites (codex footgun: preserve cwd, env and text/encoding behaviour, plus
    the Windows tree-kill). On TimeoutExpired the entire group is killed and the
    exception is re-raised with whatever output was captured.
    """
    popen_kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": text,
    }
    if cwd is not None:
        popen_kwargs["cwd"] = cwd
    if env is not None:
        popen_kwargs["env"] = env
    if encoding is not None:
        popen_kwargs["encoding"] = encoding
    if errors is not None:
        popen_kwargs["errors"] = errors
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(list(cmd), **popen_kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = ("" if text else b""), ("" if text else b"")
        raise subprocess.TimeoutExpired(list(cmd), timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, err)
