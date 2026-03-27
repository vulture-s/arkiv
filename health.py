#!/usr/bin/env python3
"""
arkiv — Environment Health Check
Run: python health.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PASS = 0
FAIL = 0
SKIP = 0


def check(name: str, ok: bool, detail: str = "", required: bool = True):
    global PASS, FAIL, SKIP
    if ok:
        PASS += 1
        print(f"  \033[32m✓\033[0m {name} {detail}")
    elif required:
        FAIL += 1
        print(f"  \033[31m✗\033[0m {name} {detail}")
    else:
        SKIP += 1
        print(f"  \033[33m⏭\033[0m {name} {detail} (optional)")


def main():
    print("\n═══ arkiv Health Check ═══\n")

    # ── Python ───────────────────────────────────────────────────────────
    print("-- Python --")
    v = sys.version_info
    check("Python >= 3.9", v >= (3, 9), f"({v.major}.{v.minor}.{v.micro})")

    # ── FFmpeg ───────────────────────────────────────────────────────────
    print("\n-- FFmpeg --")
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    check("ffmpeg", ffmpeg is not None, f"({ffmpeg})" if ffmpeg else "(not found)")
    check("ffprobe", ffprobe is not None, f"({ffprobe})" if ffprobe else "(not found)")

    # ── Ollama ───────────────────────────────────────────────────────────
    print("\n-- Ollama --")
    ollama = shutil.which("ollama")
    check("ollama binary", ollama is not None, f"({ollama})" if ollama else "(not found)", required=False)

    ollama_ok = False
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        ollama_ok = True
        check("ollama server", True, f"({len(models)} models)")

        has_embed = any("nomic-embed" in m for m in models)
        check("nomic-embed-text", has_embed, "" if has_embed else "(run: ollama pull nomic-embed-text)")

        has_vision = any("llava" in m for m in models)
        check("llava:7b", has_vision, "" if has_vision else "(optional: ollama pull llava:7b)", required=False)
    except Exception:
        check("ollama server", False, "(not running)", required=False)

    # ── Whisper ──────────────────────────────────────────────────────────
    print("\n-- Whisper --")
    has_mlx = False
    has_faster = False
    try:
        import mlx_whisper  # noqa: F401
        has_mlx = True
    except ImportError:
        pass
    try:
        from faster_whisper import WhisperModel  # noqa: F401
        has_faster = True
    except ImportError:
        pass
    check("mlx-whisper (Apple Silicon)", has_mlx, required=False)
    check("faster-whisper (CUDA/CPU)", has_faster, required=False)
    check("any whisper backend", has_mlx or has_faster, "(need at least one)")

    # ── GPU ──────────────────────────────────────────────────────────────
    print("\n-- GPU --")
    # Apple Silicon
    try:
        import mlx.core  # noqa: F401
        check("Apple Silicon (MLX)", True)
    except ImportError:
        check("Apple Silicon (MLX)", False, required=False)

    # NVIDIA
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            gpu = result.stdout.strip().split("\n")[0]
            check("NVIDIA GPU", True, f"({gpu})")
        else:
            check("NVIDIA GPU", False, required=False)
    except Exception:
        check("NVIDIA GPU", False, required=False)

    # ── Database ─────────────────────────────────────────────────────────
    print("\n-- Database --")
    try:
        import config
        check("config.py", True)

        db_exists = config.DB_PATH.exists()
        check("media.db", db_exists, f"({config.DB_PATH})")

        if db_exists:
            import db
            stats = db.get_stats()
            check("media records", stats["total"] > 0, f"({stats['total']} files, {stats['with_transcript']} transcribed)")

        chroma_exists = config.CHROMA_PATH.exists()
        check("chroma_db", chroma_exists, f"({config.CHROMA_PATH})", required=False)
    except Exception as e:
        check("database", False, f"({e})")

    # ── FastAPI ──────────────────────────────────────────────────────────
    print("\n-- Server --")
    try:
        import fastapi  # noqa: F401
        check("fastapi", True, f"({fastapi.__version__})")
    except ImportError:
        check("fastapi", False)

    try:
        import uvicorn  # noqa: F401
        check("uvicorn", True)
    except ImportError:
        check("uvicorn", False)

    # ── Summary ──────────────────────────────────────────────────────────
    total = PASS + FAIL + SKIP
    print(f"\n═══ Result: {PASS}/{total} PASS, {FAIL} FAIL, {SKIP} SKIP ═══")
    if FAIL == 0:
        print("\033[32m    Ready to run! → uvicorn server:app --host 0.0.0.0 --port 8501\033[0m")
    else:
        print("\033[31m    Fix the failures above before running.\033[0m")
    print()
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
