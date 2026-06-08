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
| wall time (s) | 1172 (full ingest, 3 clips) | _not run_ |
| per-frame vision avg (s) | ~122 (1099.6s / 9 frames) | _not run_ |
| peak mem % | 94% (probe, pre-vision) | _not run_ |
| frames with NULL description | 0 / 9 | _not run_ |

> ⚠️ **Small sample (n=3 clips / 9 frames), single run, parallel=1 only.** This
> daytime session ran the cold-start anchor (B) on parallel=1; the parallel=2
> A/B leg was **not run** (it needs an Ollama restart with the env flag + a
> ≥10-clip set for a fair comparison). The parallel=1 column here is a real but
> small data point, not the full A/B. Per EC-1 (sample-size labeling): low n,
> treat as directional, not a production tuning decision.

**Recommendation**: _parallel=2 A/B still pending a fair ≥10-clip run._ On this
machine (mini, 16GB, M2 Pro) parallel=1 already pushed probe memory to 94% and
triggered backpressure (see C) — raising parallel risks more contention, not
less, so **keep the default at 1 here** until a real A/B says otherwise.

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

**Result**: ✅ **PASS** — `2026-06-09`, mini-relay (M2 Pro, 16GB), 3 NAS clips
(C4606/C4609/C4611, 2023 Tokyo, ~134MB each), latest `main`.

```
media: 3 | transcribed: 3
frames: 9 | NULL/empty description: 0
wall_seconds: 1172 | ingest_exit: 0
vision per clip: 311.6s / 400.2s / 387.8s
```

`SELECT COUNT(*) FROM frames WHERE description IS NULL OR description=''` → **0**.
Every frame got a vision description through a real cold-start (vision model was
**not** resident at vision-phase start — see C). ⚠️ **Small sample (n=3 clips /
9 frames), single run** — directional evidence the cold-start loss is fixed, not
a 427-clip-scale replication. The headline 427-clip regression itself is not
re-run here; this confirms the mechanism on a clean cold start.

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

**Result**: ✅ **PASS** — observed live during the B run (`2026-06-09`), not
staged. At vision-phase entry the probe read real memory pressure and the WAIT
path fired, then warm-up + proceed:

```
Unloaded qwen2.5:14b from VRAM
[probe] [apple] MEM 94% (16067/17180MB) | models: qwen2.5:14b | active jobs: 0
[backpressure] memory at 94% > threshold 80% — GPU busy, waiting (waited 0s/120s)
Warming up vision model (qwen3-vl:8b)... ready
[1/3] C4606.MP4 >vision [311.6s] [OK]
```

The probe→backpressure→unload→warm-up chain ran end-to-end on a genuinely
loaded GPU (the 16GB ceiling on this mini made the pressure real, not induced),
then ingest proceeded and completed with 0 NULL frames (B). This is the live
counterpart to the mock backpressure tests.

---

## Summary

| anchor | status | note |
|---|---|---|
| A — parallel 1 vs 2 A/B | ⏳ partial | parallel=1 leg run (n=3); parallel=2 A/B still pending a ≥10-clip run |
| B — cold-start elimination | ✅ pass | 0/9 NULL frames, small sample (n=3 clips) |
| C — backpressure fires live | ✅ pass | observed live, 94%>80% WAIT → warm-up → proceed |

Anchors B and C — the headline cold-start risk — are closed on real GPU
(directional, small-sample per EC-1). Anchor A's full parallel A/B is the one
remaining tuning task; the default `OLLAMA_NUM_PARALLEL=1` stands until then.

*Stub created 2026-06-03 by the overnight run. A/B/C filled 2026-06-09 from a
scoped daytime GPU run (3 NAS clips) on mini-relay; B+C pass, A partial.*
