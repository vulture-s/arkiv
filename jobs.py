"""jobs — Phase 11.5c SQLite-backed ingest job queue.

A deliberately small queue: no Redis/Celery (roadmap 11.5c). State lives in the
``jobs`` table created by ``db.init_db()``. Used by the scheduler to serialise
GPU-heavy work and by ``arkiv queue`` for visibility/cancel/retry.

Priority is derived from job type; ``next_pending`` dequeues the lowest priority
number first, FIFO within a tier. The roadmap orders them
``transcode < embed < vision < whisper`` — read left-to-right as precedence, so
transcode (cheap, unblocks proxy playback) is picked before the heavy GPU jobs.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import db

# type -> priority (lower dequeued first). Unknown types fall to the back.
PRIORITY = {
    "transcode": 1,
    "embed": 2,
    "vision": 3,
    "whisper": 4,
}
_DEFAULT_PRIORITY = 99

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

_TERMINAL = (DONE, FAILED, CANCELLED)


def priority_for(job_type: str) -> int:
    return PRIORITY.get(job_type, _DEFAULT_PRIORITY)


def enqueue(job_type: str, target: Optional[str] = None) -> int:
    """Add a pending job; returns its id."""
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (type, target, priority, status) VALUES (?, ?, ?, ?)",
            (job_type, target, priority_for(job_type), PENDING),
        )
        return int(cur.lastrowid)


def next_pending() -> Optional[Dict]:
    """Highest-precedence pending job (lowest priority number, then oldest)."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status=? ORDER BY priority ASC, created_at ASC, id ASC LIMIT 1",
            (PENDING,),
        ).fetchone()
        return dict(row) if row else None


def claim_next() -> Optional[Dict]:
    """Atomically dequeue the next pending job → running.

    Concurrency-safe: the UPDATE is guarded on `status='pending'`, so if a second
    worker claimed the same row first, this one's UPDATE matches 0 rows and we
    return None (caller retries). Prefer this over next_pending()+mark_running()
    when more than one consumer can run.
    """
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status=? ORDER BY priority ASC, created_at ASC, id ASC LIMIT 1",
            (PENDING,),
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE jobs SET status=?, started_at=datetime('now') WHERE id=? AND status=?",
            (RUNNING, row["id"], PENDING),
        )
        if cur.rowcount != 1:
            return None  # lost the race to another claimer
        return dict(row, status=RUNNING)


def mark_running(job_id: int) -> bool:
    """pending → running. Returns False if the job wasn't pending (already
    claimed / cancelled / terminal) — never revives a finished job."""
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status=?, started_at=datetime('now') WHERE id=? AND status=?",
            (RUNNING, job_id, PENDING),
        )
        return cur.rowcount == 1


def mark_done(job_id: int) -> bool:
    """pending/running → done. Won't overwrite a cancelled/terminal job."""
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status=?, finished_at=datetime('now'), error=NULL "
            "WHERE id=? AND status IN (?, ?)",
            (DONE, job_id, PENDING, RUNNING),
        )
        return cur.rowcount == 1


def mark_failed(job_id: int, error: str = "") -> bool:
    """pending/running → failed. Won't overwrite a cancelled/terminal job."""
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status=?, finished_at=datetime('now'), error=? "
            "WHERE id=? AND status IN (?, ?)",
            (FAILED, (error or "")[:2000], job_id, PENDING, RUNNING),
        )
        return cur.rowcount == 1


def cancel(job_id: int) -> bool:
    """Cancel a pending/running job. Returns False if absent or terminal."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["status"] in _TERMINAL:
            return False
        conn.execute(
            "UPDATE jobs SET status=?, finished_at=datetime('now') WHERE id=?",
            (CANCELLED, job_id),
        )
        return True


def retry(job_id: int) -> bool:
    """Re-queue a failed/cancelled job back to pending. Returns False otherwise."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["status"] not in (FAILED, CANCELLED):
            return False
        conn.execute(
            "UPDATE jobs SET status=?, started_at=NULL, finished_at=NULL, error=NULL WHERE id=?",
            (PENDING, job_id),
        )
        return True


def counts() -> Dict[str, int]:
    """status -> count, with every known status present (0 if none)."""
    base = {PENDING: 0, RUNNING: 0, DONE: 0, FAILED: 0, CANCELLED: 0}
    with db.get_conn() as conn:
        for row in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
            base[row["status"]] = row["n"]
    return base


def active_count() -> int:
    """Pending + running — the number `resource_probe.probe` reports as load."""
    c = counts()
    return c[PENDING] + c[RUNNING]


def list_jobs(status: Optional[str] = None, limit: int = 50) -> List[Dict]:
    limit = max(1, min(int(limit), 500))
    with db.get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY priority ASC, created_at ASC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY "
                "CASE status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, "
                "priority ASC, created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
