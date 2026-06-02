# Phase 11.5 — Live-GPU acceptance (deferred from overnight)

The overnight run (2026-06-03, on `mini-relay`) shipped + verified the
mock-testable core of Phase 11.5: `resource_probe.py`, the SQLite job queue,
the probe-driven vision warm-up/backpressure wiring, and `arkiv status`. 49 new
tests green; full suite 296 passed / 3 skipped; probe verified live against the
mini's real Ollama.

Two acceptance anchors genuinely need a **real GPU under real ingest load** and
were intentionally left for a daytime session on the machine with the media +
hardware (mini for Apple-Silicon, PC for NVIDIA). They cannot be honestly closed
by mocks.

---

## A. 11.5d — throughput A/B (`OLLAMA_NUM_PARALLEL` 1 vs 2)

**Goal**: decide the production default for `OLLAMA_NUM_PARALLEL`.

**Method**
1. Pick 10 representative clips (mix of short/long, with audio).
2. Baseline: `OLLAMA_NUM_PARALLEL=1 python ingest.py --dir <10-clip-dir> --refresh`
   while sampling `python ingest.py --status --json` every ~30s into a log.
3. Repeat with `OLLAMA_NUM_PARALLEL=2` (restart Ollama so the env takes effect).
4. Record per run: wall time, per-frame vision latency (from ingest stdout
   `[Ns]` markers), peak `system_mem_pct` (Apple) / `gpu_mem_pct` (NVIDIA) from
   the status samples.

**Pass condition**: a filled comparison table + a one-line recommendation. If
parallel=2 doesn't beat parallel=1 on wall time without pushing peak memory past
the threshold, keep the default at 1.

| metric | parallel=1 | parallel=2 |
|---|---|---|
| wall time (s) | _TODO_ | _TODO_ |
| per-frame vision avg (s) | _TODO_ | _TODO_ |
| peak mem % | _TODO_ | _TODO_ |
| frames with NULL description | _TODO_ | _TODO_ |

**Recommendation**: _TODO after run_

---

## B. Cold-start elimination (the headline risk)

**Goal**: prove backpressure + warm-up removes the cold-start frame loss the
427-clip run hit (20 frames blank).

**Method**
1. Run a vision-bearing ingest of a non-trivial batch (≥ a few dozen clips) on
   the GPU box: `python ingest.py --dir <batch> --refresh`.
2. After it completes:
   `sqlite3 .arkiv/project.db "SELECT COUNT(*) FROM frames WHERE description IS NULL OR description=''"`

**Pass condition**: count == 0 (no frame left undescribed by a cold-start
timeout).

**Result**: _TODO_

---

## C. Backpressure actually fires under contention (live)

**Goal**: confirm the WAIT path triggers on a real busy GPU, not just in tests.

**Method**
1. Load the machine: start a memory-hungry model (e.g. keep `qwen2.5:14b`
   resident) or run another ingest so memory pressure > threshold.
2. Start a vision ingest and watch stdout for
   `[backpressure] memory at NN% > threshold 80% — GPU busy, waiting`.
3. Free the load → confirm it proceeds and warms up.

**Pass condition**: the backpressure log line appears, then ingest proceeds once
pressure drops (or after `ARKIV_BACKPRESSURE_MAX_WAIT`).

**Result**: _TODO_

---

*Stub created 2026-06-03 by the overnight run. Fill in A/B/C on the next
daytime GPU session, then flip the Phase 11.5 roadmap rows from ⏳ to ✅.*
