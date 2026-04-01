#!/usr/bin/env python3
"""
arkiv — Environment Health Check
Run: python health.py [--platform pc|docker]

Auto-detects platform if not specified:
  pc      — Windows/macOS native (expects GPU, whisper backend)
  docker  — Linux container (CPU whisper, no GPU required)
"""
from __future__ import annotations

import os
import platform
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
        print(f"  \033[32m[PASS]\033[0m {name} {detail}")
    elif required:
        FAIL += 1
        print(f"  \033[31m[FAIL]\033[0m {name} {detail}")
    else:
        SKIP += 1
        print(f"  \033[33m[SKIP]\033[0m {name} {detail} (optional)")


def detect_platform() -> str:
    """Auto-detect: 'docker' if inside container, else 'pc'."""
    if os.path.exists("/.dockerenv") or os.environ.get("ARKIV_DOCKER"):
        return "docker"
    return "pc"


def detect_os() -> str:
    """Return 'windows', 'macos', or 'linux'."""
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    return s  # 'windows' or 'linux'


def main():
    # Parse --platform arg
    plat = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--platform" and i < len(sys.argv) - 1:
            plat = sys.argv[i + 1]
        elif arg.startswith("--platform="):
            plat = arg.split("=", 1)[1]
    if plat not in ("pc", "docker", None):
        print(f"Unknown platform: {plat}. Use 'pc' or 'docker'.")
        return 1
    if plat is None:
        plat = detect_platform()

    os_name = detect_os()

    print(f"\n═══ arkiv Health Check ({plat} / {os_name}) ═══\n")

    # ── Python ──────────────────────────────────────────────────────────
    print("-- Python --")
    v = sys.version_info
    check("Python >= 3.9", v >= (3, 9), f"({v.major}.{v.minor}.{v.micro})")

    # ── FFmpeg ──────────────────────────────────────────────────────────
    print("\n-- FFmpeg --")
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    check("ffmpeg", ffmpeg is not None, f"({ffmpeg})" if ffmpeg else "(not found)")
    check("ffprobe", ffprobe is not None, f"({ffprobe})" if ffprobe else "(not found)")

    # ── Ollama ──────────────────────────────────────────────────────────
    print("\n-- Ollama --")
    ollama = shutil.which("ollama")
    if plat == "docker":
        # Docker connects to ollama service, binary not needed locally
        check("ollama binary", ollama is not None, "(optional in Docker)", required=False)
    else:
        check("ollama binary", ollama is not None, f"({ollama})" if ollama else "(not found)")

    try:
        import requests
        import config
        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        check("ollama server", True, f"({len(models)} models)")

        has_embed = any("nomic-embed" in m for m in models)
        check("nomic-embed-text", has_embed, "" if has_embed else "(run: ollama pull nomic-embed-text)")

        has_vision = any("llava" in m for m in models)
        check("llava:7b", has_vision, "" if has_vision else "(ollama pull llava:7b)", required=False)
    except Exception:
        check("ollama server", False, "(not running)")

    # ── ExifTool ────────────────────────────────────────────────────────
    print("\n-- ExifTool --")
    exiftool = shutil.which("exiftool")
    if plat == "docker":
        check("exiftool", exiftool is not None, "(optional in Docker)", required=False)
    else:
        check("exiftool", exiftool is not None,
              f"({exiftool})" if exiftool else "(not found — install for rich metadata extraction)",
              required=False)

    # ── Whisper ─────────────────────────────────────────────────────────
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

    if plat == "docker":
        # Docker only uses faster-whisper (CPU)
        check("faster-whisper", has_faster, "" if has_faster else "(required in Docker)")
    elif os_name == "macos":
        # macOS: prefer mlx-whisper, faster-whisper as fallback
        check("mlx-whisper (Apple Silicon)", has_mlx, "" if has_mlx else "(pip install mlx-whisper)")
        check("faster-whisper (fallback)", has_faster, required=False)
        check("any whisper backend", has_mlx or has_faster, "(need at least one)")
    else:
        # Windows/Linux PC: faster-whisper required
        check("faster-whisper (CUDA/CPU)", has_faster, "" if has_faster else "(pip install faster-whisper torch)")
        check("any whisper backend", has_faster, "(need at least one)")

    # ── GPU ─────────────────────────────────────────────────────────────
    print("\n-- GPU --")
    if plat == "docker":
        check("GPU", True, "(not required in Docker — CPU mode)", required=False)
    elif os_name == "macos":
        try:
            import mlx.core  # noqa: F401
            check("Apple Silicon (MLX)", True)
        except ImportError:
            check("Apple Silicon (MLX)", False, "(pip install mlx)")
    else:
        # Windows/Linux PC: NVIDIA
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

    # ── Disk Space ──────────────────────────────────────────────────────
    print("\n-- Disk Space --")
    try:
        usage = shutil.disk_usage(Path(__file__).parent)
        free_gb = usage.free / (1024**3)
        check("disk free >= 2 GB", free_gb >= 2.0, f"({free_gb:.1f} GB free)")
    except Exception as e:
        check("disk space", False, f"({e})")

    # ── Database ────────────────────────────────────────────────────────
    print("\n-- Database --")
    try:
        import config
        check("config.py", True)

        db_exists = config.DB_PATH.exists()
        check("media.db", db_exists, f"({config.DB_PATH})")

        if db_exists:
            import db
            stats = db.get_stats()
            total = stats["total"]
            transcribed = stats["with_transcript"]
            if plat == "docker" and total == 0:
                check("media records", True, "(empty — ingest media after setup)", required=False)
            else:
                check("media records", total > 0, f"({total} files, {transcribed} transcribed)")

        chroma_exists = config.CHROMA_PATH.exists()
        check("chroma_db", chroma_exists, f"({config.CHROMA_PATH})", required=False)
    except Exception as e:
        check("database", False, f"({e})")

    # ── Server packages ─────────────────────────────────────────────────
    print("\n-- Server --")
    try:
        import fastapi  # noqa: F401
        check("fastapi", True, f"({fastapi.__version__})")
    except Exception:
        check("fastapi", False, "(pip install fastapi)")

    try:
        import uvicorn  # noqa: F401
        check("uvicorn", True)
    except ImportError:
        check("uvicorn", False, "(pip install uvicorn)")

    # ── Summary ─────────────────────────────────────────────────────────
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
