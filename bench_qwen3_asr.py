#!/usr/bin/env python3
"""
Qwen3-ASR-1.7B Benchmark — Compare with Whisper large-v3-turbo on same podcast audio.
"""
import time, json, re, sys
from pathlib import Path

AUDIO_DIR = r"C:\Users\user\.gemini\antigravity\scratch\.data\media_manager_casestudy\pc_benchmark\audio"
TEST_FILES = [
    ("ke_short", Path(AUDIO_DIR) / "ke_short.mp3", 1376),   # 23min
    ("vt_short", Path(AUDIO_DIR) / "vt_short.mp3", 1288),   # 21min
]

def count_reps(text):
    if not text: return 0
    return len(re.findall(r'(.{2,6})\1{2,}', text))

def count_chars(text):
    return len(re.sub(r'\s+', '', text)) if text else 0

def run_qwen3_asr(audio_path):
    import torch
    from qwen_asr import Qwen3ASRModel

    print("  [qwen3-asr] Loading model...", flush=True)
    t0 = time.time()
    model = Qwen3ASRModel.from_pretrained(
        "Qwen/Qwen3-ASR-1.7B",
        dtype=torch.bfloat16,
        device_map="cuda:0",
    )
    load_time = time.time() - t0
    print(f"  [qwen3-asr] Model loaded in {load_time:.1f}s", flush=True)

    print(f"  [qwen3-asr] Transcribing {audio_path.name}...", flush=True)
    t0 = time.time()
    results = model.transcribe(audio=str(audio_path))
    elapsed = time.time() - t0

    text = results[0].text if results else ""
    return text, elapsed, load_time, model

def run_whisper(audio_path):
    """Run faster-whisper for comparison."""
    from faster_whisper import WhisperModel
    import torch

    print("  [whisper] Loading model...", flush=True)
    t0 = time.time()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = WhisperModel("large-v3-turbo", device=device, compute_type="float16")
    load_time = time.time() - t0
    print(f"  [whisper] Model loaded in {load_time:.1f}s", flush=True)

    # Convert to WAV first
    import subprocess, tempfile, os
    fd, wav = tempfile.mkstemp(suffix=".wav"); os.close(fd)
    subprocess.run(["ffmpeg", "-i", str(audio_path), "-ac", "1", "-ar", "16000",
                    "-loglevel", "error", wav, "-y"], capture_output=True)

    print(f"  [whisper] Transcribing {audio_path.name}...", flush=True)
    t0 = time.time()
    segs, info = model.transcribe(wav, language="zh", condition_on_previous_text=True)
    text = " ".join(s.text for s in segs).strip()
    elapsed = time.time() - t0

    Path(wav).unlink(missing_ok=True)
    return text, elapsed, load_time

def main():
    print("=" * 60)
    print("  Qwen3-ASR vs Whisper large-v3-turbo")
    print("  GPU: RTX 4070 12GB")
    print("=" * 60)

    all_results = []

    # --- Qwen3-ASR ---
    qwen_model = None
    for label, audio, dur in TEST_FILES:
        if not audio.exists():
            print(f"[SKIP] {audio}"); continue

        if qwen_model is None:
            text, elapsed, load_time, qwen_model = run_qwen3_asr(audio)
        else:
            print(f"  [qwen3-asr] Transcribing {audio.name}...", flush=True)
            t0 = time.time()
            results = qwen_model.transcribe(audio=str(audio))
            elapsed = time.time() - t0
            text = results[0].text if results else ""
            load_time = 0

        r = {
            "label": label, "engine": "Qwen3-ASR-1.7B", "model_size": "1.7B / 4.7GB",
            "audio_dur_s": dur, "time_s": round(elapsed, 1),
            "speed_x": round(dur / max(elapsed, 1), 1),
            "char_count": count_chars(text), "reps": count_reps(text),
            "text_preview": text[:300] if text else "(empty)",
        }
        all_results.append(r)
        print(f"  → {elapsed:.1f}s, {r['char_count']} chars, {r['reps']} reps, {r['speed_x']}x", flush=True)

    # Free VRAM
    del qwen_model
    import torch; torch.cuda.empty_cache()

    # --- Whisper ---
    whisper_model_loaded = False
    for label, audio, dur in TEST_FILES:
        if not audio.exists(): continue

        if not whisper_model_loaded:
            text, elapsed, load_time = run_whisper(audio)
            whisper_model_loaded = True
        else:
            # Reuse (model stays in memory via faster-whisper singleton)
            text, elapsed, _ = run_whisper(audio)

        r = {
            "label": label, "engine": "faster-whisper large-v3-turbo", "model_size": "~3GB",
            "audio_dur_s": dur, "time_s": round(elapsed, 1),
            "speed_x": round(dur / max(elapsed, 1), 1),
            "char_count": count_chars(text), "reps": count_reps(text),
            "text_preview": text[:300] if text else "(empty)",
        }
        all_results.append(r)
        print(f"  → {elapsed:.1f}s, {r['char_count']} chars, {r['reps']} reps, {r['speed_x']}x", flush=True)

    # Summary
    print(f"\n{'='*60}")
    print(f"  {'Engine':<35} {'Label':<12} {'Time':>6} {'Speed':>6} {'Chars':>7} {'Reps':>5}")
    print(f"  {'-'*35} {'-'*12} {'-'*6} {'-'*6} {'-'*7} {'-'*5}")
    for r in all_results:
        print(f"  {r['engine']:<35} {r['label']:<12} {r['time_s']:>5.1f}s {r['speed_x']:>5.1f}x {r['char_count']:>7} {r['reps']:>5}")

    # Save
    out = Path(__file__).parent / "bench_qwen3_asr_results.json"
    out.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[done] {out}")

if __name__ == "__main__":
    main()
