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

**Result**: ✅ **PASS** — `2026-06-09`, PC (RTX 4070, 12 GB), 10 iphone clips
(C462–C471, 15–230 s, 52 frames total), latest `main`. Both legs were a full
cold-start ingest of the **same** 10 clips through a dedicated `ollama serve`
(par=1 on :11435, par=2 on :11436; NUM_PARALLEL set at serve start, models
unloaded between legs so each is a fair cold run). Desktop ollama (:11434)
untouched. ffmpeg/ffprobe resolved via the Gyan build (headless WinError 448 fix).

| metric | parallel=1 | parallel=2 |
|---|---|---|
| wall time (s) | **3905** | **4101**  (+5%, slower) |
| per-frame vision avg (s) | ~34 (1618 s / ~47 frames) | 35.9 (1867 s / 52 frames) |
| peak GPU VRAM | 11866 MiB (96.6%) | 11870 MiB (96.6%) |
| frames with NULL description | **0 / 52** | **0 / 52** |

> ⚠️ **Small sample (n=10 clips / 52 frames), single run per leg.** A real
> two-legged A/B (unlike the 2026-06-03 stub), but one run each — directional
> per EC-1, not a multi-run benchmark. The parallel=1 vision-total counts 9 of
> 10 clips (one clip's `>vision` stdout line wasn't captured by the parser);
> wall time is the full, authoritative figure for both legs.

**Recommendation**: **keep `OLLAMA_NUM_PARALLEL=1`.** On the RTX 4070, parallel=1
already pins VRAM at ~97%; parallel=2 hits the *same* ceiling but runs **5%
slower** wall-clock and worse per-frame — a second slot just adds scheduling
contention on a GPU that's already memory-bound for a num_ctx=16384 vision model.
parallel=2 brings no throughput win and no cold-start regression (NULL=0 in both),
so the default stands.

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

**Result**: ✅ **PASS** — confirmed on two machines, `2026-06-09`:
- **PC (RTX 4070, NVIDIA)** — 10 iphone clips / **52 frames**, both A/B legs
  (par=1 and par=2): **0 NULL** each. This is the stronger sample (n=10).
- **mini-relay (M2 Pro, 16GB, Apple)** — 3 NAS clips (C4606/C4609/C4611, 2023
  Tokyo, ~134MB each): 0 NULL. The original directional run.

```
PC   : media 10 | frames 52 | NULL 0  (×2 legs)   | NVIDIA cold start
mini : media 3  | frames 9  | NULL 0  | wall 1172s | Apple cold start
       vision per clip (mini): 311.6s / 400.2s / 387.8s
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
| A — parallel 1 vs 2 A/B | ✅ pass | full A/B on RTX 4070 (n=10); par=2 is 5% slower at the same 97% VRAM ceiling → keep `OLLAMA_NUM_PARALLEL=1` |
| B — cold-start elimination | ✅ pass | 0 NULL frames — NVIDIA n=10 (×2 legs) + Apple n=3 |
| C — backpressure fires live | ✅ pass | observed live, 94%>80% WAIT → warm-up → proceed |

All three anchors closed on real GPU (directional, small-sample per EC-1). The
production default `OLLAMA_NUM_PARALLEL=1` is now backed by a real A/B, not just
the contention argument.

*Stub created 2026-06-03 by the overnight run. B+C filled 2026-06-09 from a
scoped GPU run (3 NAS clips) on mini-relay. Anchor A closed 2026-06-09 by a full
parallel=1-vs-2 A/B (10 iphone clips / 52 frames) on PC (RTX 4070); B also
re-confirmed there at n=10.*
