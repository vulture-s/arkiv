#!/usr/bin/env python3
"""
Anti-Hallucination Guard A/B Test — Podcast Edition
Runs the same podcast audio through 4 configurations and compares output.

Configs:
  A) raw            — No Guard, No LLM, No VAD
  B) guard_only     — Guard ON, No LLM, No VAD
  C) llm_only       — No Guard, LLM ON, No VAD
  D) full           — Guard + LLM (current default, no VAD)
  E) vad_raw        — VAD ON, No Guard, No LLM
  F) vad_full       — VAD + Guard + LLM (full pipeline)
"""
import json, os, sys, time, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import transcribe as T

AUDIO_DIR = r"C:\Users\user\.gemini\antigravity\scratch\.data\media_manager_casestudy\pc_benchmark\audio"

TEST_FILES = [
    os.path.join(AUDIO_DIR, "ke_short.mp3"),   # 健達 SP.04 ~23min
    os.path.join(AUDIO_DIR, "vt_short.mp3"),    # vulture EP3 ~21min
    os.path.join(AUDIO_DIR, "ke_medium.mp3"),   # 健達 S3E4 ~59min
]

CONFIGS = [
    {"name": "A_raw",        "vad": False, "guard": False, "llm": False},
    {"name": "B_guard_only", "vad": False, "guard": True,  "llm": False},
    {"name": "C_llm_only",   "vad": False, "guard": False, "llm": True},
    {"name": "D_full",       "vad": False, "guard": True,  "llm": True},
    {"name": "E_vad_raw",    "vad": True,  "guard": False, "llm": False},
    {"name": "F_vad_full",   "vad": True,  "guard": True,  "llm": True},
]


def count_repetitions(text):
    if not text: return 0
    return len(re.findall(r'(.{2,6})\1{2,}', text))

def count_chars(text):
    return len(re.sub(r'\s+', '', text)) if text else 0


def patched_postprocess(guard, llm):
    def _pp(text, lang, segments, language):
        if not segments:
            return text, lang

        if guard:
            avg_ns = sum(s.get("no_speech_prob", 0) for s in segments) / len(segments)
            if avg_ns > T.NO_SPEECH_THRESHOLD:
                return "", lang

            good, filtered = [], 0
            for s in segments:
                st = s.get("text", "").strip()
                if not st: continue
                if s.get("no_speech_prob", 0) > 0.8: filtered += 1; continue
                if s.get("avg_logprob", 0) < -1.5: filtered += 1; continue
                if s.get("compression_ratio", 1) > 3.0: filtered += 1; continue
                good.append(st)
            if not good:
                return "", lang
            text = " ".join(good).strip()
            if T._is_repetitive(text): return "", lang
            if T._has_char_loops(text): text = T._remove_char_loops(text)
        else:
            text = " ".join(s.get("text", "").strip() for s in segments if s.get("text", "")).strip()

        if llm and len(text) > 10:
            text = T._llm_polish(text, language)

        return text, lang
    return _pp


def run_test(media, cfg):
    name = cfg["name"]
    print(f"  [{name}] ...", end="", flush=True)

    T.VAD_ENABLED = cfg["vad"]
    orig = T._postprocess
    T._postprocess = patched_postprocess(cfg["guard"], cfg["llm"])

    t0 = time.time()
    text, lang = T.transcribe(media)
    dt = time.time() - t0

    T._postprocess = orig

    r = {
        "config": name, "vad": cfg["vad"], "guard": cfg["guard"], "llm": cfg["llm"],
        "time_s": round(dt, 1), "char_count": count_chars(text),
        "repetitions": count_repetitions(text),
        "text_preview": (text or "")[:300], "full_text": text or "",
    }
    print(f" {dt:.1f}s, {r['char_count']} chars, {r['repetitions']} reps", flush=True)
    return r


def main():
    print("=" * 60)
    print("  Guard A/B Test — Podcast")
    print("=" * 60)

    print("\n[warm-up] Whisper...", flush=True)
    T.warm_up()
    print("[warm-up] Ollama...", flush=True)
    T.warm_up_ollama()

    all_results = {}

    for media in TEST_FILES:
        if not Path(media).exists():
            print(f"\n[SKIP] {media}"); continue

        fname = Path(media).name
        print(f"\n{'='*60}\n  {fname}\n{'='*60}")

        results = [run_test(media, c) for c in CONFIGS]
        all_results[fname] = results

        print(f"\n  {'Config':<16} {'Time':>6} {'Chars':>7} {'Reps':>5}")
        print(f"  {'-'*16} {'-'*6} {'-'*7} {'-'*5}")
        for r in results:
            print(f"  {r['config']:<16} {r['time_s']:>5.1f}s {r['char_count']:>7} {r['repetitions']:>5}")

    # Save
    out = Path(__file__).parent / "bench_guard_ab_results.json"
    save = {f: [{k: v for k, v in r.items() if k != "full_text"} for r in rs] for f, rs in all_results.items()}
    out.write_text(json.dumps(save, indent=2, ensure_ascii=False), encoding="utf-8")

    txt = Path(__file__).parent / "bench_guard_ab_texts.json"
    txt.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[done] {out}\n[done] {txt}")


if __name__ == "__main__":
    main()
