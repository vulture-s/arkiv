"""First-run setup checks (roadmap 9.8a) — what this machine has, what arkiv still needs.

Pure-ish probes behind /api/setup/*: hardware (CPU / RAM / GPU), the device the
transcriber will actually use, whether the Whisper model is already on disk, and
whether Ollama is up with the three models arkiv uses. The wizard shows these before
the first ingest so the 1–9 GB of model downloads happen when the user chooses —
not silently in the middle of their first import.

Every probe degrades to "unknown" instead of raising: a first-run screen that 500s
because `nvidia-smi` is missing would be the worst possible first impression.
Python 3.9-safe, no new dependencies (psutil and huggingface_hub are already
installed; both are imported lazily and optional here).
"""
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import config


# ── completion flag (per user, not per library) ────────────────────────────────
# Per library would re-run the wizard every time the desktop app switches library
# (#561), and the things it checks — hardware, models — belong to the machine.
def _state_path() -> Path:
    return Path(os.getenv("ARKIV_SETUP_STATE", str(Path.home() / ".arkiv-setup.json"))).expanduser()


def is_completed() -> bool:
    try:
        return bool(json.loads(_state_path().read_text(encoding="utf-8")).get("completed"))
    except (OSError, ValueError):
        return False


def mark_completed(skipped: bool = False) -> Dict[str, Any]:
    data = {"completed": True, "skipped": bool(skipped), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(str(tmp), str(p))
    return data


def reset() -> None:
    try:
        _state_path().unlink()
    except FileNotFoundError:
        pass


# ── hardware ───────────────────────────────────────────────────────────────────
def _run(cmd: List[str], timeout: float = 3.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _ram_gb() -> Optional[float]:
    try:
        import psutil

        return round(psutil.virtual_memory().total / 1024 ** 3, 1)
    except Exception:  # noqa: BLE001 — optional probe
        return None


def _cpu_name() -> str:
    system = platform.system()
    if system == "Darwin":
        name = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if name:
            return name
    if system == "Windows":
        name = _run(["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"])
        if name:
            return name
    return platform.processor() or platform.machine() or "unknown"


def _nvidia() -> Optional[Dict[str, Any]]:
    if not shutil.which("nvidia-smi"):
        return None
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if not out:
        return None
    first = out.splitlines()[0].split(",")
    try:
        vram = round(int(first[1].strip()) / 1024, 1)
    except (IndexError, ValueError):
        vram = None
    return {"name": first[0].strip(), "vram_gb": vram}


def hardware() -> Dict[str, Any]:
    apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"
    nv = None if apple_silicon else _nvidia()
    ram = _ram_gb()
    if apple_silicon:
        gpu = {"name": "Apple GPU（與 CPU 共用記憶體）", "vram_gb": ram}
    elif nv:
        gpu = nv
    else:
        gpu = None
    return {
        "os": "{0} {1}".format(platform.system(), platform.release()),
        "arch": platform.machine(),
        "cpu": _cpu_name(),
        "cores": os.cpu_count(),
        "ram_gb": ram,
        "gpu": gpu,
        "apple_silicon": apple_silicon,
    }


def transcribe_device(hw: Dict[str, Any]) -> Dict[str, str]:
    """The device transcription will really run on, and why.

    Mirrors transcribe.py rather than re-deciding: Apple Silicon → mlx-whisper
    (Metal); elsewhere faster-whisper on ARKIV_WHISPER_DEVICE (default cuda). A
    machine with no NVIDIA GPU left on the cuda default fails at the first ingest,
    so that combination is reported as a problem here, before it happens.
    """
    if hw.get("apple_silicon"):
        return {"device": "mlx", "label": "Apple Silicon（Metal）", "ok": "true", "note": ""}
    dev = os.getenv("ARKIV_WHISPER_DEVICE", "cuda").strip().lower()
    if dev == "cuda" and not (hw.get("gpu") or {}).get("name"):
        return {"device": "cuda", "label": "NVIDIA CUDA", "ok": "false",
                "note": "沒偵測到 NVIDIA 顯示卡：請把 ARKIV_WHISPER_DEVICE 設成 cpu（會慢很多）"}
    if dev == "cpu":
        return {"device": "cpu", "label": "CPU", "ok": "true", "note": "轉錄會比 GPU 慢數倍"}
    return {"device": dev, "label": "NVIDIA CUDA" if dev == "cuda" else dev, "ok": "true", "note": ""}


# ── models ─────────────────────────────────────────────────────────────────────
_FW_REPOS = {  # faster-whisper short names → HF repo (fallback when faster_whisper isn't importable)
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
    "medium": "Systran/faster-whisper-medium",
    "small": "Systran/faster-whisper-small",
}


def whisper_repo(name: Optional[str] = None) -> Optional[str]:
    """HF repo id for the configured Whisper model; None when it is a local path."""
    name = name or config.WHISPER_MODEL
    if Path(name).expanduser().is_dir():
        return None
    if "/" in name:
        return name
    try:
        from faster_whisper.utils import _MODELS  # type: ignore

        return _MODELS.get(name) or _FW_REPOS.get(name)
    except Exception:  # noqa: BLE001
        return _FW_REPOS.get(name)


def hf_cache_dir() -> Path:
    if os.getenv("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    if os.getenv("HF_HOME"):
        return Path(os.environ["HF_HOME"]).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _repo_dir(repo: str) -> Path:
    return hf_cache_dir() / ("models--" + repo.replace("/", "--"))


def dir_bytes(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(str(p)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def whisper_status() -> Dict[str, Any]:
    name = config.WHISPER_MODEL
    repo = whisper_repo(name)
    if repo is None:
        return {"kind": "whisper", "name": name, "present": True, "where": "local"}
    snaps = _repo_dir(repo) / "snapshots"
    present = snaps.is_dir() and any(any(s.iterdir()) for s in snaps.iterdir() if s.is_dir())
    return {"kind": "whisper", "name": name, "repo": repo, "present": bool(present),
            "size_hint": "約 1.6 GB"}


OLLAMA_ROLES = (
    ("embed", "語意搜尋", "OLLAMA_EMBED_MODEL", "約 1.2 GB"),
    ("vision", "畫面描述", "OLLAMA_VISION_MODEL", "約 6 GB"),
    ("chat", "對話", "OLLAMA_CHAT_MODEL", "約 9 GB"),
)


def _model_present(want: str, have: List[str]) -> bool:
    if ":" in want:
        return want in have or (want.endswith(":latest") and want[:-7] in have)
    return any(h == want or h.startswith(want + ":") for h in have)


def ollama_status() -> Dict[str, Any]:
    try:
        import requests

        r = requests.get("{0}/api/tags".format(config.OLLAMA_URL), timeout=3)
        r.raise_for_status()
        have = [m.get("name", "") for m in r.json().get("models", [])]
        up = True
    except Exception:  # noqa: BLE001
        have, up = [], False
    models = []
    for role, label, attr, hint in OLLAMA_ROLES:
        want = getattr(config, attr)
        models.append({"kind": "ollama", "role": role, "label": label, "name": want,
                       "present": up and _model_present(want, have), "size_hint": hint})
    return {"running": up, "installed": bool(shutil.which("ollama")) or up, "models": models}


def status() -> Dict[str, Any]:
    hw = hardware()
    return {
        "completed": is_completed(),
        "hardware": hw,
        "transcribe": transcribe_device(hw),
        "ffmpeg": shutil.which(config.FFMPEG_PATH) is not None,
        "whisper": whisper_status(),
        "ollama": ollama_status(),
    }
