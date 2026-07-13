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
