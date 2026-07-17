"""Shared process-wide runtime state for the API server.

Extracted from server.py (fable-audit 2026-07-12) as the APIRouter-split
foundation: when server.py's ~80 routes are peeled into per-concern routers, each
router must import ONE instance of the ingest single-flight guard and the WS
broadcaster from here — forking them per router would silently break the H3 guard
(the double-whisper-OOM protection). server.py re-exports these names, so existing
call sites and tests that reference `server._acquire_ingest_slot` / `server.ingest_ws`
keep working unchanged.

Scope note: the embed-rebuild / retranscribe / vision-fallback guards deliberately
stay in server.py for now. Their flags are rebound via `global` inside route
handlers (and a test writes the raw flag), so moving them needs a mutable-container
refactor rather than a plain import — tracked as a follow-up.
"""
import threading as _threading
from typing import Set

from fastapi import WebSocket

import config


# ── Named single-flight guards ────────────────────────────────────────────────
# R5-22 (#52): embed-rebuild / retranscribe used bare module-global bools
# (`_embed_rebuild_active` etc.) rebound via `global` inside route handlers. Once
# the APIRouter split moves those routes into their own modules, `from server
# import _embed_rebuild_active` would import a FROZEN COPY of the bool — the
# importing router's guard reads False forever and the single-flight is silently
# dead (embed has no ingest-slot backstop). Wrapping the flag in an OBJECT means a
# router imports ONE live instance; acquire()/release() mutate it in place, and
# the progress dict is mutated in place too, so a poller holding the same dict
# sees updates. This is the mutable-container refactor state.py's header deferred.
class SingleFlight:
    """A named, thread-safe single-flight slot with an in-place progress dict."""

    def __init__(self, name):
        self.name = name
        self._lock = _threading.Lock()
        self._active = False
        self.progress = {}

    def acquire(self):
        """Reserve the slot. True if reserved, False if already held."""
        with self._lock:
            if self._active:
                return False
            self._active = True
            return True

    def release(self):
        with self._lock:
            self._active = False

    @property
    def active(self):
        with self._lock:
            return self._active

    def reset_progress(self, **fields):
        """Replace the progress dict CONTENTS in place (never rebind), so a
        reference already handed to a poller keeps pointing at live data."""
        self.progress.clear()
        self.progress.update(fields)


# audit M8: single-flight for the embed rebuild — double-clicking 「重建向量索引」
# used to launch N concurrent drop+rebuild subprocesses over the same Chroma
# collection. Shared by /api/embed/rebuild and the recorrect rebuild chain.
embed_rebuild = SingleFlight("embed_rebuild")

# Phase 9.6d: project-wide batch retranscribe (single-flight + progress poll).
# Seed the poll shape so GET /api/retranscribe-all/status returns the full dict
# even before the first run (parity with the old module-global default).
retranscribe = SingleFlight("retranscribe")
retranscribe.reset_progress(
    total=0, done=0, failed=0, current=None, running=False, backup=None,
)

# R5-22 (#59): /api/proxy/build had NO single-flight (unlike its embed/retranscribe
# siblings) — a double-click launched parallel full-library ffmpeg loops and
# mid-build playback streamed truncated proxies.
proxy_build = SingleFlight("proxy_build")

# First-run: /api/sample/seed loads the bundled CC-BY sample clips (single-flight +
# progress poll). Seed the poll shape so GET /api/sample/seed/status returns the
# full dict even before the first run.
sample_seed = SingleFlight("sample_seed")
sample_seed.reset_progress(running=False, ok=False, returncode=None, message="", clips=0)


def _rebuild_embeddings():
    """Background task: full embedding rebuild via subprocess. Runs embed.py in a
    child process to isolate its sys.exit() guard and use sys.executable per the
    platform Python-concurrency rule (not in-process — sys.exit would kill server).

    R5-25 (#51): moved here from server.py (swapping ROOT→config.BASE_DIR) so both
    /api/embed/rebuild (server) and the /api/recorrect rebuild chain
    (routers/recorrect.py) share ONE worker + the ONE embed_rebuild guard above."""
    import subprocess
    import sys
    try:
        subprocess.run([sys.executable, str(config.BASE_DIR / "embed.py"), "--rebuild"], check=False)
    except Exception as e:
        print(f"[embed] rebuild failed: {e}")
    finally:
        embed_rebuild.release()  # audit M8: always free the single-flight slot

# ── Ingest single-flight guard ────────────────────────────────────────────────
# One guard for ALL ingest entry points (REST /api/ingest, reingest, WS, bin-copy,
# retranscribe-all) so two full pipelines can't run at once → DB-lock contention +
# double whisper + OOM on a 16GB box (audit H3). threading.Lock because REST runs in
# the threadpool while the WS variant runs on the event loop. The flag + its
# mutators live together here so the `global` rebinding stays intra-module and
# server.py can import the FUNCTIONS without a stale value-copy of the flag.
_ingest_lock = _threading.Lock()
_ingest_active = False


def _acquire_ingest_slot() -> bool:
    global _ingest_active
    with _ingest_lock:
        if _ingest_active:
            return False
        _ingest_active = True
        return True


def _release_ingest_slot() -> None:
    global _ingest_active
    with _ingest_lock:
        _ingest_active = False


# ── WebSocket connection manager ──────────────────────────────────────────────
_MAX_WS_CONNECTIONS = 32  # cap concurrent progress listeners (DoS guard)


class IngestBroadcaster:
    """Manages WebSocket connections for ingest progress updates."""
    def __init__(self):
        self.connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> bool:
        if len(self.connections) >= _MAX_WS_CONNECTIONS:
            await ws.close(code=1013)  # try again later
            return False
        await ws.accept()
        # audit L6: re-check after the await — concurrent handshakes can all pass
        # the pre-accept check before any of them is added to the set (TOCTOU).
        if len(self.connections) >= _MAX_WS_CONNECTIONS:
            await ws.close(code=1013)
            return False
        self.connections.add(ws)
        return True

    def disconnect(self, ws: WebSocket):
        self.connections.discard(ws)

    async def broadcast(self, data: dict):
        dead = set()
        # snapshot: send_json awaits, so a concurrent connect/disconnect mutating
        # the live set mid-iteration would raise "Set changed size" (audit H9).
        for ws in list(self.connections):
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self.connections -= dead


ingest_ws = IngestBroadcaster()
