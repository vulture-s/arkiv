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
from enum import Enum
from pathlib import Path

PASS = 0
FAIL = 0
SKIP = 0


class HealthStatus(str, Enum):
    OK = "ok"
    PATH_NOT_FOUND = "path_not_found"
    DB_MISSING = "db_missing"
    CHROMA_MISSING = "chroma_missing"
    NAS_UNMOUNTED = "nas_unmounted"
    TIMEOUT = "timeout"


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


def _project_path(project):
    if isinstance(project, dict):
        raw = project.get("path", "")
    else:
        raw = getattr(project, "path", "")
    return Path(str(raw)).expanduser()


def _check_mount(path):
    project_path = Path(path).expanduser()
    project_posix = project_path.as_posix()
    if not project_posix.startswith("/Volumes/"):
        return True
    try:
        mount_root_name = project_posix.split("/")[2]
        mount_root = Path("/Volumes") / mount_root_name
        return mount_root.exists()
    except Exception:
        return True


def project_health(project):
    project_path = _project_path(project)
    if not project_path.exists():
        return HealthStatus.PATH_NOT_FOUND

    db_path = project_path / ".arkiv" / "project.db"
    if not db_path.exists():
        return HealthStatus.DB_MISSING

    chroma_path = project_path / ".arkiv" / "chroma_db"
    if not chroma_path.is_dir():
        return HealthStatus.CHROMA_MISSING

    if not _check_mount(project_path):
        return HealthStatus.NAS_UNMOUNTED

    return HealthStatus.OK


def preflight_paths():
    """Phase 8.0e + 8.0f: pre-flight check for ingest/server startup.

    Returns (ok: bool, errors: list[str]). Verifies the 4 storage paths
    are writable and (if PROJECT_ROOT lives on a NAS mount) that the
    mount is alive. Catches the 2026-05-25 overnight failure mode where
    a dangling thumbnails symlink let mkdir(exist_ok=True) raise
    FileExistsError on every video, causing 222/222 ingest fails while
    main() still returned exit 0.
    """
    import config
    import sqlite3

    errors = []
    project_root = config.PROJECT_ROOT.expanduser().resolve(strict=False)

    if not _check_mount(project_root):
        errors.append(f"PROJECT_ROOT NAS mount unavailable: {project_root}")
        return (False, errors)

    # 8.0f: NAS mount precondition. macOS auto-unmounts SMB shares; cron
    # / overnight jobs hit dangling /Volumes/<share> with no warning.
    if "/Volumes/" in str(project_root):
        try:
            mount_root = Path("/Volumes") / project_root.relative_to("/Volumes").parts[0]
            if not mount_root.exists():
                errors.append(
                    f"PROJECT_ROOT 位於 NAS mount {mount_root} 但未掛載；"
                    f"預期路徑 {project_root}（Finder ⌘K 連線或 mount -t smbfs）"
                )
                return (False, errors)  # downstream checks meaningless
        except ValueError:
            pass

    # 8.0e: per-path writable + symlink-target check
    paths_to_check = [
        ("DB_PATH parent", config.DB_PATH.parent),
        ("THUMBNAILS_DIR", config.THUMBNAILS_DIR),
        ("PROXIES_DIR", config.PROXIES_DIR),
        ("CHROMA_PATH", config.CHROMA_PATH),
    ]
    for name, p in paths_to_check:
        # Dangling symlink: entry exists but target missing. mkdir(exist_ok=True)
        # does NOT cure this — it still raises FileExistsError.
        if p.is_symlink():
            target = p.resolve(strict=False)
            if not target.exists():
                errors.append(
                    f"{name} 是 dangling symlink: {p} -> {target}（target 不存在）"
                )
                continue
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".preflight_probe"
            probe.write_text("ok")
            probe.unlink()
        except OSError as e:
            errors.append(f"{name} 不可寫: {p} ({e.__class__.__name__}: {e})")

    # Sample DB resolve: stale PROJECT_ROOT after media move shows up here
    if config.DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(config.DB_PATH))
            row = conn.execute("SELECT path FROM media LIMIT 1").fetchone()
            conn.close()
            if row and row[0]:
                rel = row[0]
                resolved = project_root / rel if not Path(rel).is_absolute() else Path(rel)
                if not resolved.exists():
                    errors.append(
                        f"DB sample row resolve fail: {rel} → {resolved} 不存在；"
                        f"PROJECT_ROOT 可能 stale（設錯或素材已搬）"
                    )
        except sqlite3.Error:
            pass  # init_db 未跑 / schema 不存在 — preflight 不負責建表

    return (len(errors) == 0, errors)


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

        vision_model = config.VISION_MODEL.split(":")[0]
        has_vision = any(vision_model in m for m in models)
        check(config.VISION_MODEL, has_vision, "" if has_vision else f"(ollama pull {config.VISION_MODEL})", required=False)
    except Exception:
        check("ollama server", False, "(not running)")

    # ── ExifTool ────────────────────────────────────────────────────────
    print("\n-- ExifTool --")
    import config as _config
    exiftool_resolved = _config.EXIFTOOL_PATH
    # config.EXIFTOOL_PATH may be absolute path (auto-detected) or literal "exiftool" (PATH lookup)
    if os.sep in exiftool_resolved or "/" in exiftool_resolved:
        exiftool_ok = Path(exiftool_resolved).exists()
    else:
        exiftool_ok = shutil.which(exiftool_resolved) is not None
    detail = (
        f"({exiftool_resolved})" if exiftool_ok
        else f"(not found — set ARKIV_EXIFTOOL_PATH or add to PATH; resolved: {exiftool_resolved!r})"
    )
    if plat == "docker":
        check("exiftool", exiftool_ok, "(optional in Docker)", required=False)
    else:
        check("exiftool", exiftool_ok, detail, required=False)

    # ── Whisper ─────────────────────────────────────────────────────────
    print("\n-- Whisper --")
    has_mlx = False
    has_faster = False
    has_whisperx = False
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
    try:
        import whisperx  # noqa: F401
        has_whisperx = True
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
        # Windows/Linux PC: whisperx preferred (wraps faster-whisper + alignment)
        check("whisperx (CUDA alignment)", has_whisperx, "" if has_whisperx else "(pip install whisperx)")
        check("faster-whisper (dependency)", has_faster, "" if has_faster else "(installed via whisperx)")
        check("any whisper backend", has_whisperx or has_faster, "(need at least one)")

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
        check("media.db", db_exists,
              f"({config.DB_PATH})" if db_exists else "(will be created on first run)",
              required=False)

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
