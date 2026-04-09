#!/usr/bin/env python3
"""Qwen3-ASR vs Whisper — aligned with Mac M2 Max benchmark (10.5s + 414s)"""
import time, json, re, sys, subprocess, tempfile, os
from pathlib import Path

TEST_FILES = [
    ("short_10s", Path(r"C:\Users\user\.arkiv\test_short_10s.wav"), 10.5),
    ("long_414s", Path(r"C:\Users\user\.arkiv\test_long_414s.wav"), 414),
]

def count_reps(t): return len(re.findall(r'(.{2,6})\1{2,}', t)) if t else 0
def count_chars(t): return len(re.sub(r'\s+', '', t)) if t else 0

def main():
    import torch
    print("=" * 60)
    print("  Aligned Benchmark: 10.5s + 414s (RTX 4070)")
    print("=" * 60)

    results = []

    # --- Qwen3-ASR ---
    from qwen_asr import Qwen3ASRModel
    print("\n[qwen3-asr] Loading...", flush=True)
    t0 = time.time()
    qm = Qwen3ASRModel.from_pretrained("Qwen/Qwen3-ASR-1.7B", dtype=torch.bfloat16, device_map="cuda:0")
    print(f"[qwen3-asr] Loaded in {time.time()-t0:.1f}s", flush=True)

    for label, audio, dur in TEST_FILES:
        print(f"  [{label}] qwen3-asr ...", end="", flush=True)
        t0 = time.time()
        res = qm.transcribe(audio=str(audio))
        dt = time.time() - t0
        text = res[0].text if res else ""
        r = {"label": label, "engine": "Qwen3-ASR-1.7B", "dur": dur,
             "time_s": round(dt, 1), "chars": count_chars(text), "reps": count_reps(text),
             "preview": text[:200]}
        results.append(r)
        print(f" {dt:.1f}s, {r['chars']} chars, {r['reps']} reps", flush=True)

    del qm; torch.cuda.empty_cache()

    # --- Whisper ---
    from faster_whisper import WhisperModel
    print("\n[whisper] Loading...", flush=True)
    wm = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")

    for label, audio, dur in TEST_FILES:
        # wav already 16kHz mono
        print(f"  [{label}] whisper ...", end="", flush=True)
        t0 = time.time()
        segs, info = wm.transcribe(str(audio), language="zh", condition_on_previous_text=True)
        text = " ".join(s.text for s in segs).strip()
        dt = time.time() - t0
        r = {"label": label, "engine": "faster-whisper turbo", "dur": dur,
             "time_s": round(dt, 1), "chars": count_chars(text), "reps": count_reps(text),
             "preview": text[:200]}
        results.append(r)
        print(f" {dt:.1f}s, {r['chars']} chars, {r['reps']} reps", flush=True)

    # --- Whisper large-v3 (original) ---
    print("\n[whisper v3] Loading...", flush=True)
    del wm; torch.cuda.empty_cache()
    wm3 = WhisperModel("large-v3", device="cuda", compute_type="float16")

    for label, audio, dur in TEST_FILES:
        print(f"  [{label}] whisper v3 ...", end="", flush=True)
        t0 = time.time()
        segs, info = wm3.transcribe(str(audio), language="zh", condition_on_previous_text=True)
        text = " ".join(s.text for s in segs).strip()
        dt = time.time() - t0
        r = {"label": label, "engine": "faster-whisper v3", "dur": dur,
             "time_s": round(dt, 1), "chars": count_chars(text), "reps": count_reps(text),
             "preview": text[:200]}
        results.append(r)
        print(f" {dt:.1f}s, {r['chars']} chars, {r['reps']} reps", flush=True)

    # Summary
    print(f"\n{'='*60}")
    print(f"  {'Engine':<28} {'Label':<12} {'Dur':>5} {'Time':>7} {'Chars':>6} {'Reps':>5}")
    print(f"  {'-'*28} {'-'*12} {'-'*5} {'-'*7} {'-'*6} {'-'*5}")
    for r in results:
        print(f"  {r['engine']:<28} {r['label']:<12} {r['dur']:>5.1f} {r['time_s']:>6.1f}s {r['chars']:>6} {r['reps']:>5}")

    out = Path(__file__).parent / "bench_qwen3_asr_aligned_results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[done] {out}")

if __name__ == "__main__":
    main()
