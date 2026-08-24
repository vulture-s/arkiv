"""progress.py — a write-only progress sink, bound per context.

The problem this solves: `retry-vision` and `retranscribe` are blocking POSTs that
can run for minutes, and the UI has nothing to show while they do. The obvious fix
is to thread a `progress_cb` argument down through `vision.describe_frames` and
`transcribe.transcribe` — which changes both signatures, every existing call site,
and every test that calls them.

A context-bound sink avoids all of that. The worker calls `report(...)`; whether
anyone is listening is not its problem. Nothing about the call site changes except
that one line exists.

Three properties that make this safe to sprinkle into hot paths:

* **Write-only.** `report()` returns None and has no reader side. A worker can
  never read progress back and branch on it, so progress cannot change behaviour.
* **Default no-op.** With no sink bound — CLI, tests, ingest.py — `report()` costs
  one ContextVar lookup and returns.
* **Per context.** `contextvars` means two jobs running at once each see their own
  sink, with no id threading and no cross-talk. FastAPI's sync endpoints run in a
  threadpool that copies the caller's context, so a sink bound inside the endpoint
  is visible to everything it calls.

A failing sink must never break the work it describes, so `report()` swallows
exceptions from it. That is the one place in this codebase where a bare catch is
the correct answer: the alternative is a progress bar taking down a transcode.
"""
from __future__ import annotations

import contextvars
from typing import Any, Callable, Dict, Optional

Sink = Callable[[Dict[str, Any]], None]

_sink: contextvars.ContextVar[Optional[Sink]] = contextvars.ContextVar(
    "arkiv_progress_sink", default=None
)


def report(**fields: Any) -> None:
    """Report progress to whoever is listening in this context. Usually nobody."""
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(dict(fields))
    except Exception:
        pass  # progress must never break the work it is describing


class capture:
    """Bind a sink for the duration of a block.

        with progress.capture(lambda ev: registry.update(job_id, **ev)):
            vision.describe_frames(paths)
    """

    def __init__(self, sink: Sink):
        self._sink = sink
        self._token = None

    def __enter__(self) -> "capture":
        self._token = _sink.set(self._sink)
        return self

    def __exit__(self, *exc_info) -> bool:
        _sink.reset(self._token)
        return False  # never swallow the block's own exception
