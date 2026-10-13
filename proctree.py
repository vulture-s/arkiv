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
import time
from typing import Mapping, Optional, Sequence


def _kill_tree(proc) -> None:
    """Kill the child AND its descendants. Extracted so the timeout path and the
    watchdog path cannot drift apart — the Windows branch in particular is easy to
    fix on one side only."""
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    else:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        proc.kill()


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
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = ("" if text else b""), ("" if text else b"")
        raise subprocess.TimeoutExpired(list(cmd), timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, err)


class TreeTimeout(subprocess.TimeoutExpired):
    """A `run_tree_watched` kill, carrying WHICH limit fired.

    Subclasses TimeoutExpired so existing `except subprocess.TimeoutExpired`
    handlers keep working; `kind` is what lets a caller tell the user the two
    apart, and they need different words:

      "stall"  — the child produced no output for `stall_timeout` seconds. It is
                 probably wedged (a hung ffmpeg, an ollama that stopped answering).
      "budget" — the child is still talking, just far past the estimated total. It
                 may well finish; the cap exists so a wrong estimate cannot run
                 forever.
    """

    def __init__(self, cmd, timeout, kind, output=None, stderr=None,
                 elapsed=None, idle=None):
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.kind = kind
        self.elapsed = elapsed
        self.idle = idle


def run_tree_watched(
    cmd: Sequence[str],
    stall_timeout: float,
    total_timeout: float,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    on_line=None,
) -> "subprocess.CompletedProcess":
    """`run_tree` with two independent deadlines instead of one wall-clock cap.

    Why two: a single fixed timeout has to be either too small for a big library
    (30 minutes could not finish a 200-clip ingest — measured 94 minutes) or too
    large to catch anything wedged. Splitting them lets each be right:

      stall_timeout  bounds how long the child may stay SILENT.
      total_timeout  bounds the whole run, as a backstop for a bad estimate.

    stdout and stderr are merged so that any byte counts as liveness — ingest.py
    flushes a marker at every stage (`probe`/`transcribe`/`thumbnail`/`frames`/
    `vision`), so silence really does mean "not progressing" rather than "buffered".

    `on_line(str)` is called for each line as it arrives (used to broadcast live
    progress); exceptions from it are swallowed so a bad consumer cannot kill the
    ingest.
    """
    import threading

    popen_kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,   # merged: any output is liveness
        "text": True,
        "bufsize": 1,
        # run_tree takes encoding/errors; this one did not, so a single
        # non-UTF-8 byte raised inside the pump thread. Not decoding strictly is
        # the right default here because the output is a liveness signal, not
        # data — losing a character beats losing the reader.
        "errors": "replace",
    }
    if cwd is not None:
        popen_kwargs["cwd"] = cwd
    if env is not None:
        popen_kwargs["env"] = env
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(list(cmd), **popen_kwargs)
    chunks = []
    state = {"last": time.monotonic()}

    def _pump():
        try:
            for line in proc.stdout:
                state["last"] = time.monotonic()
                chunks.append(line)
                if on_line is not None:
                    try:
                        on_line(line)
                    except Exception:
                        pass
        except Exception as e:
            # A dead pump is worse than a lost line: nobody drains the pipe, the
            # child blocks on write once the buffer fills, state["last"] stops
            # advancing, and the watchdog kills a perfectly healthy run while
            # reporting it as stalled. UnicodeDecodeError is the realistic cause
            # (ffmpeg/whisper emitting one non-UTF-8 byte, a cp950 progress bar
            # on the Windows box) — the Popen below now decodes with
            # errors="replace" so it cannot happen, and this records anything
            # else instead of vanishing.
            state["pump_error"] = "{0}: {1}".format(type(e).__name__, e)

    pump = threading.Thread(target=_pump, daemon=True)
    pump.start()

    started = time.monotonic()
    kind = None
    while True:
        if proc.poll() is not None:
            break
        now = time.monotonic()
        if now - state["last"] > stall_timeout:
            kind = "stall"
            break
        if now - started > total_timeout:
            kind = "budget"
            break
        time.sleep(0.5)

    if kind is None:
        pump.join(timeout=10)
        try:
            proc.stdout.close()
        except Exception:
            pass
        # stderr is merged into stdout above, so this field can never carry the
        # child's error output — and /api/ingest presents it verbatim as the
        # diagnostic field. A permanently-empty diagnostic is worse than none:
        # it reads as "the child said nothing". The one thing that genuinely has
        # no other channel is a pump crash (round 1 stopped swallowing it, but
        # nobody read what it recorded), so that is what goes here.
        return subprocess.CompletedProcess(
            list(cmd), proc.returncode, "".join(chunks), state.get("pump_error", ""))

    _kill_tree(proc)
    pump.join(timeout=10)
    try:
        proc.stdout.close()
    except Exception:
        pass
    now = time.monotonic()
    raise TreeTimeout(
        list(cmd),
        stall_timeout if kind == "stall" else total_timeout,
        kind,
        output="".join(chunks),
        stderr=state.get("pump_error", ""),
        elapsed=now - started,
        idle=now - state["last"],
    )
