#!/usr/bin/env python3
"""
arkiv vs DaVinci Resolve — Speech-to-Text Benchmark
Usage:
    # Step 1: Run arkiv transcription with timing
    python bench_stt.py arkiv --file "H:/path/to/video.mp4"

    # Step 2: After DaVinci manual transcription, export SRT and compare
    python bench_stt.py compare --arkiv-srt output/arkiv.srt --davinci-srt output/davinci.srt --ground-truth output/ground_truth.txt

    # Step 3: Generate report
    python bench_stt.py report --results output/bench_results.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path


def parse_srt(path: str) -> list[dict]:
    """Parse SRT file into list of {index, start, end, text}."""
    blocks = Path(path).read_text(encoding="utf-8").strip().split("\n\n")
    entries = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        text = " ".join(lines[2:]).strip()
        entries.append({"text": text})
    return entries


def srt_to_text(path: str) -> str:
    """Extract plain text from SRT."""
    entries = parse_srt(path)
    return " ".join(e["text"] for e in entries)


def normalize_text(text: str) -> str:
    """Normalize for comparison: remove punctuation, whitespace, lowercase."""
    text = re.sub(r'[，。！？、：；「」『』（）\(\)\[\]\.,!?;:\"\'\-]', '', text)
    text = re.sub(r'\s+', '', text)
    return text.lower()


def cer(hypothesis: str, reference: str) -> float:
    """Character Error Rate using edit distance."""
    h = normalize_text(hypothesis)
    r = normalize_text(reference)
    if not r:
        return 0.0 if not h else 1.0
    return _edit_distance(h, r) / len(r)


def wer(hypothesis: str, reference: str) -> float:
    """Word Error Rate — for Chinese, segment by character."""
    h = list(normalize_text(hypothesis))
    r = list(normalize_text(reference))
    if not r:
        return 0.0 if not h else 1.0
    return _edit_distance_list(h, r) / len(r)


def _edit_distance(s1: str, s2: str) -> int:
    """Levenshtein distance for strings."""
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]


def _edit_distance_list(s1: list, s2: list) -> int:
    """Levenshtein distance for lists."""
    if len(s1) < len(s2):
        return _edit_distance_list(s2, s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]


def cmd_arkiv(args):
    """Run arkiv transcription with timing."""
    import subprocess
    media = args.file
    print(f"[bench] Transcribing with arkiv: {media}")

    start = time.time()
    result = subprocess.run(
        [sys.executable, "ingest.py", "--dir", str(Path(media).parent),
         "--limit", "1"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent),
        env={**__import__("os").environ, "PYTHONUTF8": "1"}
    )
    elapsed = time.time() - start

    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    # Get duration
    dur_cmd = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", media],
        capture_output=True, text=True
    )
    duration = float(dur_cmd.stdout.strip()) if dur_cmd.stdout.strip() else 0

    bench = {
        "engine": "arkiv",
        "file": str(media),
        "duration_s": round(duration, 1),
        "transcribe_s": round(elapsed, 1),
        "rtf": round(elapsed / max(duration, 1), 3),
        "speed_x": round(duration / max(elapsed, 1), 1),
        "pipeline": "faster-whisper large-v3-turbo + Silero VAD + qwen2.5:14b polish",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    out = Path(args.output) if args.output else Path(f"bench_{Path(media).stem}.json")
    out.write_text(json.dumps(bench, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[bench] Done in {elapsed:.1f}s (RTF {bench['rtf']}, {bench['speed_x']}x realtime)")
    print(f"[bench] Results saved to {out}")


def cmd_compare(args):
    """Compare arkiv vs DaVinci SRT against ground truth."""
    gt = Path(args.ground_truth).read_text(encoding="utf-8").strip()

    results = {}
    for label, srt_path in [("arkiv", args.arkiv_srt), ("davinci", args.davinci_srt)]:
        if not srt_path:
            continue
        text = srt_to_text(srt_path)
        c = cer(text, gt)
        w = wer(text, gt)
        results[label] = {
            "cer": round(c, 4),
            "wer": round(w, 4),
            "char_count": len(normalize_text(text)),
            "gt_char_count": len(normalize_text(gt)),
        }
        print(f"[{label}] CER: {c:.2%}  WER: {w:.2%}  chars: {len(normalize_text(text))}")

    out = Path(args.output) if args.output else Path("bench_compare.json")
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults saved to {out}")


def cmd_report(args):
    """Generate markdown report from bench results."""
    data = json.loads(Path(args.results).read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description="arkiv STT Benchmark")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("arkiv", help="Run arkiv transcription with timing")
    a.add_argument("--file", required=True)
    a.add_argument("--output", default=None)

    c = sub.add_parser("compare", help="Compare SRTs against ground truth")
    c.add_argument("--arkiv-srt")
    c.add_argument("--davinci-srt")
    c.add_argument("--ground-truth", required=True)
    c.add_argument("--output", default=None)

    r = sub.add_parser("report", help="Print bench results")
    r.add_argument("--results", required=True)

    args = p.parse_args()
    if args.cmd == "arkiv":
        cmd_arkiv(args)
    elif args.cmd == "compare":
        cmd_compare(args)
    elif args.cmd == "report":
        cmd_report(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
