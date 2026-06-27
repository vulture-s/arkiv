#!/usr/bin/env python3
"""arkiv CLI (Phase 11.5e) — operator visibility into the resource-aware pipeline.

Two read/light-write subcommands over the building blocks that already ship:

    python arkiv_cli.py status [--json]
        resource_probe snapshot (GPU/mem + loaded models) + queue depth +
        the backpressure decision (PROCEED/WAIT) the ingest pipeline would make.

    python arkiv_cli.py queue list [--status S] [--limit N] [--json]
    python arkiv_cli.py queue cancel <id>
    python arkiv_cli.py queue retry  <id>
        inspect / cancel / retry SQLite-backed ingest jobs (jobs.py).

Pure glue: resource_probe.probe/decide/summary_line + jobs.counts/list_jobs/
cancel/retry do the work; this just gives them a terminal surface.
"""
from __future__ import annotations

import argparse
import json
import sys

import db
import jobs
import resource_probe as rp


def cmd_status(args) -> int:
    db.init_db()
    active = jobs.active_count()
    result = rp.probe(active_jobs=active)
    decision, reason = rp.decide(result)
    counts = jobs.counts()
    if args.json:
        print(json.dumps(
            {"resource": result, "decision": decision, "reason": reason, "queue": counts},
            ensure_ascii=False, indent=2,
        ))
        return 0
    print(rp.summary_line(result))
    print("  decision: {0} — {1}".format(decision, reason))
    print("  queue: pending {pending} · running {running} · done {done} · "
          "failed {failed} · cancelled {cancelled}".format(**counts))
    return 0


def _print_job_row(j: dict) -> None:
    print("#{id:>4}  {status:<9} {type:<9} pri={priority}  {target}{err}".format(
        id=j["id"], status=j["status"], type=j["type"], priority=j["priority"],
        target=(j.get("target") or ""),
        err=("  ! " + j["error"]) if j.get("error") else "",
    ))


def cmd_queue(args) -> int:
    db.init_db()
    if args.qcmd == "list":
        rows = jobs.list_jobs(status=args.status, limit=args.limit)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if not rows:
            print("(no jobs)")
            return 0
        for j in rows:
            _print_job_row(j)
        return 0
    if args.qcmd == "cancel":
        ok = jobs.cancel(args.id)
        print("cancelled #{0}".format(args.id) if ok
              else "#{0} not cancellable (absent or already terminal)".format(args.id))
        return 0 if ok else 1
    if args.qcmd == "retry":
        ok = jobs.retry(args.id)
        print("re-queued #{0}".format(args.id) if ok
              else "#{0} not retryable (absent or not failed/cancelled)".format(args.id))
        return 0 if ok else 1
    return 2  # unreachable: subparser is required


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arkiv", description="arkiv status + job queue CLI (Phase 11.5e)")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("status", help="resource probe + queue depth + backpressure decision")
    ps.add_argument("--json", action="store_true", help="machine-readable output")
    ps.set_defaults(func=cmd_status)

    pq = sub.add_parser("queue", help="inspect / cancel / retry queued jobs")
    pq.set_defaults(func=cmd_queue)
    qsub = pq.add_subparsers(dest="qcmd", required=True)
    ql = qsub.add_parser("list", help="list jobs (newest-active first)")
    ql.add_argument("--status", help="filter: pending|running|done|failed|cancelled")
    ql.add_argument("--limit", type=int, default=50)
    ql.add_argument("--json", action="store_true")
    qc = qsub.add_parser("cancel", help="cancel a pending/running job")
    qc.add_argument("id", type=int)
    qr = qsub.add_parser("retry", help="re-queue a failed/cancelled job")
    qr.add_argument("id", type=int)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
