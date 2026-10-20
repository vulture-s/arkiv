# 11.5d — `OLLAMA_NUM_PARALLEL` 1 vs 2 A/B on Apple Silicon (M2 Max)

Date: 2026-10-17 · Host: MacBook Pro M2 Max, 32 GB unified memory, macOS 27.0.1 ·
Ollama 0.33.0 · arkiv `main` @ `9b28028` · vision model `qwen2.5vl:7b`, `num_ctx` 16384.

This is the Apple Silicon leg of the 11.5d A/B. The NVIDIA leg (RTX 4070, 2026-06-09)
is in [`docs/phase-11.5-acceptance.md`](../phase-11.5-acceptance.md) §A.

## TL;DR

- **arkiv does not send concurrent vision requests.** `vision.describe_frames()` is a
  plain `for` loop over frames, `ingest.py` processes clips one at a time, and the job
  queue (`jobs.py`) serialises jobs. `config.OLLAMA_NUM_PARALLEL` is parsed but **no code
  reads it**. It's an Ollama *server* env var, so it only has an effect when it's set on
  `ollama serve`, and then only if a client keeps more than one request in flight.
  arkiv as shipped never does.
- **Measured with the real pipeline:** parallel=2 gave no speedup. In this run it was
  **slower** (wall +35%, per-frame +35%) and held **+3.3 GB** more memory (a second KV
  slot). Part of that slowdown happened while the host was under unrelated load (see
  Caveats), so read the size of the gap as directional. The direction agrees with the
  RTX 4070 leg.
- **If arkiv did fan out** (2 client threads, same prompt and same frames): parallel=2
  reached 28.0 frames/min against 20.1 serial (**+39%**). About two-thirds of that gain
  shows up with the server still at parallel=1 (25.3/min, +26%), because client-side
  encoding and HTTP overlap the GPU. Each of these is n=1.
- **Recommendation:** keep the default at 1. Don't raise the `.env.example` value. A
  throughput win on Apple Silicon would first need client-side concurrency in
  `describe_frames`, a code change that this report doesn't make.

## Method

- **Isolation.** A dedicated server is started per leg, sharing the normal model
  directory:
  `OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=<1|2> OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_KEEP_ALIVE=30m ollama serve`
  The flash-attention and q8_0 KV settings mirror the desktop server's env. The desktop
  Ollama on `:11434` and the deployed `~/.arkiv` were left alone. Each leg's server was
  killed afterwards, and port 11435 was confirmed free. The server logs confirm that the
  runner was spawned with `-np 1` and `-np 2` respectively.
- **Clips.** 10 clips derived from the repo's own `sample/clips/` (4 × 8 s, 640×268,
  CC-licensed per `sample/LICENSES.md`). Each clip has an original copy and an hflip
  copy, plus one desaturated `caminandes` and one 2× cropped `coffee_run`. All were
  re-encoded with `-an`, so there's no audio and no transcription: **this measures
  vision only.** That gives 30 frames per run (3 per clip).
- **Throwaway library.** `ARKIV_PROJECT_ROOT=$CLAUDE_JOB_DIR/tmp/ab-lib`, deleted after
  the run.
- **Tool.** [`scripts/bench_ollama_parallel.py`](../../scripts/bench_ollama_parallel.py)
  - `ingest` mode runs `ingest.py --dir <clips> --refresh --no-embed --skip-failed`
    against the server. A `sitecustomize` shim times every image-bearing
    `/api/generate` round-trip, which gives per-frame latency.
  - `direct` mode fires `llm.vision()` with `vision.LIGHT_PROMPT` over the 30 extracted
    frame JPEGs, using N client threads.
  - Both modes sample every 0.5 s: the summed RSS of the server and its runner
    processes, and `kern.memorystatus_level` (the system-wide free-memory % that
    `memory_pressure` reports). The tool refuses to run against `:11434`.
- **Runs.** For each leg there was 1 warm-up ingest (discarded), then 2 timed ingest runs
  (median reported), then 1 direct run each at concurrency 1 and 2.

## Results — real pipeline (`ingest` mode, 10 clips / 30 frames per run)

| metric | parallel=1 | parallel=2 | Δ |
|---|---|---|---|
| wall time, median of 2 (s) | **219.1** (221.2, 217.1) | **296.4** (298.1, 294.8) | +35% |
| per-frame vision latency, median (s) | **6.36** (6.42, 6.29) | **8.59** (9.01, 8.17) | +35% |
| per-frame max (s) | 8.55 / 7.12 | 14.79 / 10.78 | |
| peak server RSS (GB) | **11.8–12.1** | **15.3–15.4** | +3.3 GB |
| min system free memory (`memorystatus_level`) | 24% / 20% | 15% / 13% | |
| frames with empty description | 0 / 30 | 0 / 30 | |
| warm-up run wall (s, discarded) | 206.7 | 229.7 | +11% |

Peak RSS covers **both** models that ingest keeps resident on that server. Each run also
loads the chat model `qwen2.5:14b` (one `/api/chat` call), and it stays loaded next to
`qwen2.5vl:7b` under `KEEP_ALIVE`. That's why ingest-mode RSS is higher than
direct-mode RSS.

## Results — hypothetical client fan-out (`direct` mode, 30 frames, `LIGHT_PROMPT`, n=1 each)

| server | client threads | wall (s) | frames/min | per-request latency median (s) | peak RSS (GB) |
|---|---|---|---|---|---|
| parallel=1 | 1 | 89.6 | 20.1 | 2.89 | 9.8 |
| parallel=1 | 2 | 71.2 | 25.3 (+26%) | 4.53 | 9.8 |
| parallel=2 | 1 | 89.8 | 20.1 | 2.93 | 9.8 |
| parallel=2 | 2 | 64.3 | **28.0 (+39%)** | 4.20 | 9.8 |

With one client thread, the server setting changes nothing (89.6 vs 89.8 s), which is
exactly the situation arkiv is in today. Direct-mode per-frame latency (~2.9 s) is lower
than ingest-mode latency (~6.4 s) for three reasons: direct mode uses only the light
prompt (ingest sends the full 12-field prompt on each clip's representative frame),
there's no 14b model resident beside it, and none of ingest's other per-clip work runs.
Compare rows within a table, not across the two tables.

## Interpretation

1. **arkiv today (serial client):** a second slot can't be used. Requests arrive one at
   a time, so parallel=2 only reserves a second KV cache. That cost ~3.3 GB at
   `num_ctx` 16384 and pushed the system to 13–15% free while the 14b chat model was
   also resident. The memory cost is real and structural. The ingest slowdown we saw is
   consistent with that extra pressure, but it was also confounded by host load (see
   Caveats).
2. **If client concurrency were added:** on M2 Max, two in-flight requests do raise
   throughput. Most of the gain comes from pipelining, so it appears even at server
   parallel=1. parallel=2 adds about another 10% on top. On the RTX 4070, where VRAM was
   already 97% full at parallel=1, parallel=2 was a loss. The answer depends on the
   hardware, which is one more reason not to change the default.

## Caveats

- **Vision only.** No audio and no whisper. Whisper isn't served by Ollama in arkiv, so
  `OLLAMA_NUM_PARALLEL` couldn't affect it anyway.
- **One host:** M2 Max, 32 GB. Small clips: 640×268, 3 frames each.
- **Small samples.** Ingest mode is 2 timed runs per leg plus a warm-up. Direct mode is
  a single run per cell. That's enough for a direction, not a precise effect size.
- **Shared, non-idle host.** Other sessions were active. The 1-minute load average
  reached **26** during the parallel=2 ingest runs (00:31–00:41). It wasn't recorded for
  the parallel=1 runs. Some of the +35% is likely contention rather than parallel=2
  itself. The warm-up comparison (+11%) and the memory numbers aren't affected by that
  and point the same way.
- The desktop Ollama (`:11434`) stayed up throughout, holding `bge-m3` (~0.7 GB).

## Reproduce

```bash
OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=2 OLLAMA_FLASH_ATTENTION=1 \
  OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve &
python scripts/bench_ollama_parallel.py ingest --ollama-url http://127.0.0.1:11435 \
  --project-root /tmp/ab-lib --clips /tmp/ab-lib/clips --label p2-r1 --out results.jsonl
python scripts/bench_ollama_parallel.py direct --concurrency 2 --ollama-url http://127.0.0.1:11435 \
  --project-root /tmp/ab-lib --frames /tmp/ab-lib/frames --label p2-c2 --out results.jsonl
```

## Raw results (JSONL)

```
{"label": "p1-warmup", "mode": "ingest", "concurrency": 1, "rc": 0, "wall_s": 206.66, "frames": 30, "frame_lat_median_s": 6.03, "frame_lat_mean_s": 6.11, "frame_lat_max_s": 7.65, "throughput_fps_per_min": 8.71, "peak_rss_mb": 12136, "min_free_pct": 22, "mem_samples": 324}
{"label": "p1-ingest-r1", "mode": "ingest", "concurrency": 1, "rc": 0, "wall_s": 221.15, "frames": 30, "frame_lat_median_s": 6.42, "frame_lat_mean_s": 6.53, "frame_lat_max_s": 8.55, "throughput_fps_per_min": 8.14, "peak_rss_mb": 11798, "min_free_pct": 24, "mem_samples": 346}
{"label": "p1-ingest-r2", "mode": "ingest", "concurrency": 1, "rc": 0, "wall_s": 217.09, "frames": 30, "frame_lat_median_s": 6.29, "frame_lat_mean_s": 6.21, "frame_lat_max_s": 7.12, "throughput_fps_per_min": 8.29, "peak_rss_mb": 12122, "min_free_pct": 20, "mem_samples": 337}
{"label": "p1-direct-c1", "mode": "direct", "concurrency": 1, "rc": 0, "wall_s": 89.57, "frames": 30, "frame_lat_median_s": 2.89, "frame_lat_mean_s": 2.98, "frame_lat_max_s": 4.56, "throughput_fps_per_min": 20.1, "peak_rss_mb": 9764, "min_free_pct": 48, "mem_samples": 137}
{"label": "p1-direct-c2", "mode": "direct", "concurrency": 2, "rc": 0, "wall_s": 71.2, "frames": 30, "frame_lat_median_s": 4.53, "frame_lat_mean_s": 4.66, "frame_lat_max_s": 6.08, "throughput_fps_per_min": 25.28, "peak_rss_mb": 9764, "min_free_pct": 47, "mem_samples": 113}
{"label": "p2-ingest-warmup", "mode": "ingest", "concurrency": 1, "rc": 0, "wall_s": 229.69, "frames": 30, "frame_lat_median_s": 6.78, "frame_lat_mean_s": 6.87, "frame_lat_max_s": 8.52, "throughput_fps_per_min": 7.84, "peak_rss_mb": 15390, "min_free_pct": 15, "mem_samples": 362}
{"label": "p2-ingest-r1", "mode": "ingest", "concurrency": 1, "rc": 0, "wall_s": 298.08, "frames": 30, "frame_lat_median_s": 9.01, "frame_lat_mean_s": 8.93, "frame_lat_max_s": 14.79, "throughput_fps_per_min": 6.04, "peak_rss_mb": 15383, "min_free_pct": 15, "mem_samples": 474}
{"label": "p2-ingest-r2", "mode": "ingest", "concurrency": 1, "rc": 0, "wall_s": 294.79, "frames": 30, "frame_lat_median_s": 8.17, "frame_lat_mean_s": 8.19, "frame_lat_max_s": 10.78, "throughput_fps_per_min": 6.11, "peak_rss_mb": 15337, "min_free_pct": 13, "mem_samples": 489}
{"label": "p2-direct-c1", "mode": "direct", "concurrency": 1, "rc": 0, "wall_s": 89.76, "frames": 30, "frame_lat_median_s": 2.93, "frame_lat_mean_s": 2.99, "frame_lat_max_s": 3.79, "throughput_fps_per_min": 20.05, "peak_rss_mb": 9817, "min_free_pct": 49, "mem_samples": 138}
{"label": "p2-direct-c2", "mode": "direct", "concurrency": 2, "rc": 0, "wall_s": 64.34, "frames": 30, "frame_lat_median_s": 4.2, "frame_lat_mean_s": 4.27, "frame_lat_max_s": 5.62, "throughput_fps_per_min": 27.98, "peak_rss_mb": 9832, "min_free_pct": 51, "mem_samples": 99}
```
