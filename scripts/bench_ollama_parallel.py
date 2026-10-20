#!/usr/bin/env python3
"""Phase 11.5d bench: OLLAMA_NUM_PARALLEL A/B on a *dedicated* Ollama server.

This script never starts, stops or reconfigures an Ollama server. Start one
yourself on a spare port (so the desktop/production Ollama is untouched), e.g.

    OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=2 ollama serve

then point this script at it with --ollama-url. Two modes:

  ingest  — runs the real pipeline (`ingest.py --dir <clips> --refresh`) against
            the server and records wall time, per-frame vision latency (each
            image-bearing /api/generate round-trip, timed by a sitecustomize
            shim injected into the ingest subprocess) and memory peaks.
            This answers "does arkiv as shipped get faster with parallel=2?".

  direct  — fires the same vision request arkiv sends (llm.vision, same prompt,
            same num_ctx) over a list of frame JPEGs with N client threads.
            This answers "*if* arkiv sent concurrent requests, would the server
            setting pay off?" (arkiv's ingest loop is serial; see the report).

Memory: samples every --sample-interval seconds the summed RSS of the server's
descendant processes (the `ollama runner` holding the model) and
`sysctl kern.memorystatus_level` (system-wide free-memory %, the same figure
`memory_pressure` prints). Reports peak RSS and minimum free %.

Output: one JSON object per run on stdout (and --out, appended as JSONL).
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parent.parent


def _server_pid(port: int) -> int:
    # lsof, not psutil.net_connections (needs root on macOS).
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                         capture_output=True, text=True, encoding="utf-8").stdout.split()
    if not out:
        raise SystemExit(f"no process listening on :{port}")
    return int(out[0])


class MemSampler(threading.Thread):
    def __init__(self, server_pid: int, interval: float):
        super().__init__(daemon=True)
        self.server = psutil.Process(server_pid)
        self.interval = interval
        self.stop_evt = threading.Event()
        self.peak_rss = 0
        self.min_free_pct = 100
        self.samples = 0

    def run(self):
        while not self.stop_evt.is_set():
            rss = 0
            try:
                procs = [self.server] + self.server.children(recursive=True)
            except psutil.Error:
                procs = []
            for p in procs:
                try:
                    rss += p.memory_info().rss
                except psutil.Error:
                    pass
            self.peak_rss = max(self.peak_rss, rss)
            try:
                lvl = int(subprocess.check_output(
                    ["sysctl", "-n", "kern.memorystatus_level"], text=True, encoding="utf-8").strip())
                self.min_free_pct = min(self.min_free_pct, lvl)
            except Exception:
                pass
            self.samples += 1
            self.stop_evt.wait(self.interval)

    def result(self):
        return {"peak_rss_mb": round(self.peak_rss / 2**20), "min_free_pct": self.min_free_pct,
                "mem_samples": self.samples}


# Injected into the ingest subprocess: wraps llm._ollama_post so every vision
# round-trip is timed, written one-per-line to $BENCH_LAT_FILE.
_SITE = r'''
import os, time, llm
_orig = llm._ollama_post
def _timed(path, payload, *a, **k):
    t = time.time()
    r = _orig(path, payload, *a, **k)
    if path == "/api/generate" and payload.get("images"):
        with open(os.environ["BENCH_LAT_FILE"], "a") as f:
            f.write("%.3f\n" % (time.time() - t))
    return r
llm._ollama_post = _timed
'''


def run_ingest(args, sampler):
    lat_file = Path(args.project_root) / f"lat-{os.getpid()}-{time.time():.0f}.txt"
    shim = Path(args.project_root) / "_bench_shim"
    shim.mkdir(parents=True, exist_ok=True)
    tmp = shim / "sitecustomize.py.tmp"
    tmp.write_text(_SITE, encoding="utf-8")
    os.replace(str(tmp), str(shim / "sitecustomize.py"))  # repo rule: no in-place writes
    env = dict(os.environ,
               ARKIV_OLLAMA_URL=args.ollama_url,
               ARKIV_PROJECT_ROOT=args.project_root,
               BENCH_LAT_FILE=str(lat_file),
               PYTHONPATH=f"{shim}{os.pathsep}{REPO}")
    cmd = [args.python, str(REPO / "ingest.py"), "--dir", args.clips, "--refresh",
           "--no-embed", "--skip-failed"]
    t0 = time.time()
    proc = subprocess.run(cmd, env=env, cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    wall = time.time() - t0
    lats = [float(x) for x in lat_file.read_text().split()] if lat_file.exists() else []
    lat_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-2000:])
    return wall, lats, proc.returncode


def run_direct(args):
    sys.path.insert(0, str(REPO))
    os.environ["ARKIV_OLLAMA_URL"] = args.ollama_url
    os.environ["ARKIV_PROJECT_ROOT"] = args.project_root
    import base64
    import llm
    import vision
    import config
    frames = sorted(Path(args.frames).glob("*.jpg"))[: args.max_frames or None]
    prompt = vision.LIGHT_PROMPT  # same prompt ingest uses for non-representative frames

    def one(p):
        b64 = base64.b64encode(p.read_bytes()).decode()
        t = time.time()
        llm.vision(prompt=prompt, image_b64=b64, model=config.VISION_MODEL,
                   num_ctx=config.OLLAMA_VISION_NUM_CTX)
        return time.time() - t

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        lats = list(ex.map(one, frames))
    return time.time() - t0, lats, 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["ingest", "direct"])
    ap.add_argument("--ollama-url", required=True, help="dedicated server, e.g. http://127.0.0.1:11435")
    ap.add_argument("--project-root", required=True, help="throwaway ARKIV_PROJECT_ROOT")
    ap.add_argument("--clips", help="ingest mode: clip directory")
    ap.add_argument("--frames", help="direct mode: directory of frame JPEGs")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=1, help="direct mode: client threads")
    ap.add_argument("--label", default="")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--sample-interval", type=float, default=0.5)
    ap.add_argument("--out", help="append result JSON line here")
    args = ap.parse_args()

    port = int(args.ollama_url.rsplit(":", 1)[1].split("/")[0])
    if port == 11434:
        raise SystemExit("refusing to bench the default :11434 server — use a dedicated one")
    sampler = MemSampler(_server_pid(port), args.sample_interval)
    sampler.start()
    if args.mode == "ingest":
        wall, lats, rc = run_ingest(args, sampler)
    else:
        wall, lats, rc = run_direct(args)
    sampler.stop_evt.set()
    sampler.join()
    res = {"label": args.label, "mode": args.mode, "concurrency": args.concurrency if args.mode == "direct" else 1,
           "rc": rc, "wall_s": round(wall, 2), "frames": len(lats),
           "frame_lat_median_s": round(statistics.median(lats), 2) if lats else None,
           "frame_lat_mean_s": round(statistics.mean(lats), 2) if lats else None,
           "frame_lat_max_s": round(max(lats), 2) if lats else None,
           "throughput_fps_per_min": round(len(lats) / wall * 60, 2) if wall else None,
           **sampler.result()}
    line = json.dumps(res, ensure_ascii=False)
    print(line)
    if args.out:
        with open(args.out, "a") as f:
            f.write(line + "\n")


if __name__ == "__main__":
    main()
