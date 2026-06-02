"""resource_probe — Phase 11.5 resource-awareness layer.

Reports GPU / system-memory / loaded-model / active-job state so the ingest
pipeline can warm models up before a phase and back off when the machine is
already saturated (the 427-clip stress test lost 20 frames to a cold-start
timeout and hit a 28 GB unified-memory crunch).

HARD RULE — this module is a *sensor*, never a *gate*. Every external call
(Ollama HTTP, nvidia-smi, psutil) is wrapped: any failure degrades the
corresponding field to ``None`` and is recorded in ``errors``; it must never
raise into the ingest path. Set ``ARKIV_PROBE_DISABLE=true`` to make probe() a
fully-degraded no-op (CI / GPU-less hosts).

Backends:
- nvidia (PC RTX): nvidia-smi gives real, separate VRAM used/total.
- apple (M-series): unified memory — GPU and system share one pool, so the
  backpressure signal is system memory %. Loaded-model VRAM (from Ollama
  /api/ps ``size_vram``) is reported as ``ollama_vram_mb`` for visibility.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import urllib.request
from typing import Dict, List, Optional, Tuple

import config

# psutil is a soft dependency: present in requirements.txt (mac) but probe must
# import-time-survive its absence and just degrade the memory fields.
try:
    import psutil as _psutil
except Exception:  # noqa: BLE001 — any import failure → degrade, never crash
    _psutil = None


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def probe_disabled() -> bool:
    return _truthy_env("ARKIV_PROBE_DISABLE")


def _degraded_result(reason: str) -> Dict:
    return {
        "degraded": True,
        "errors": [reason],
        "backend": "unknown",
        "gpu_mem_used_mb": None,
        "gpu_mem_total_mb": None,
        "gpu_mem_pct": None,
        "ollama_vram_mb": None,
        "models_loaded": [],
        "models_known": False,
        "system_mem_used_mb": None,
        "system_mem_total_mb": None,
        "system_mem_pct": None,
        "active_jobs": None,
    }


def _detect_backend() -> str:
    if shutil.which("nvidia-smi"):
        return "nvidia"
    if platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64"):
        return "apple"
    return "unknown"


def _probe_ollama(url: str, timeout: float = 3.0) -> Tuple[List[str], Optional[float]]:
    """Return (loaded model names, summed VRAM MB) from Ollama /api/ps.

    Raises on any failure — caller catches and degrades.
    """
    req = urllib.request.Request(url.rstrip("/") + "/api/ps")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - configured URL
        raw = resp.read().decode("utf-8", "replace")
    data = json.loads(raw)
    models = data.get("models") or []
    names = [m.get("name") or m.get("model") for m in models if (m.get("name") or m.get("model"))]
    vram_bytes = sum((m.get("size_vram") or 0) for m in models)
    vram_mb = round(vram_bytes / 1_000_000, 1) if vram_bytes else 0.0
    return names, vram_mb


def _probe_nvidia(timeout: float = 3.0) -> Tuple[float, float]:
    """Return (used_mb, total_mb) from nvidia-smi. Raises on failure."""
    out = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    ).stdout.strip()
    # First GPU line: "1234, 8192"
    first = out.splitlines()[0]
    used_str, total_str = (p.strip() for p in first.split(","))
    return float(used_str), float(total_str)


def _probe_memory() -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (used_mb, total_mb, pct 0-1) via psutil, or (None, None, None)."""
    if _psutil is None:
        return None, None, None
    vm = _psutil.virtual_memory()
    total_mb = round(vm.total / 1_000_000, 1)
    used_mb = round((vm.total - vm.available) / 1_000_000, 1)
    pct = round(vm.percent / 100.0, 4)
    return used_mb, total_mb, pct


def probe(active_jobs: Optional[int] = None) -> Dict:
    """Snapshot current resource state. Never raises.

    ``active_jobs`` is injected by the caller (queue layer) to avoid a hard
    import cycle with db; left None when the caller has no queue context.
    """
    if probe_disabled():
        r = _degraded_result("ARKIV_PROBE_DISABLE set — probe is a no-op")
        r["active_jobs"] = active_jobs
        return r

    errors: List[str] = []
    try:
        backend = _detect_backend()
    except Exception as e:  # noqa: BLE001 — backend detection must never escape
        backend = "unknown"
        errors.append("backend detection failed: {0}".format(e))

    # --- loaded models + ollama VRAM (both backends) ---
    models_loaded: List[str] = []
    models_known = False
    ollama_vram_mb: Optional[float] = None
    try:
        models_loaded, ollama_vram_mb = _probe_ollama(config.OLLAMA_URL)
        models_known = True
    except Exception as e:  # noqa: BLE001
        errors.append("ollama /api/ps unavailable: {0}".format(e))

    # --- GPU memory (nvidia only; apple has no discrete VRAM) ---
    gpu_used_mb: Optional[float] = None
    gpu_total_mb: Optional[float] = None
    gpu_pct: Optional[float] = None
    if backend == "nvidia":
        try:
            gpu_used_mb, gpu_total_mb = _probe_nvidia()
            if gpu_total_mb:
                gpu_pct = round(gpu_used_mb / gpu_total_mb, 4)
        except Exception as e:  # noqa: BLE001
            errors.append("nvidia-smi unavailable: {0}".format(e))

    # --- system memory (psutil) ---
    try:
        sys_used_mb, sys_total_mb, sys_pct = _probe_memory()
    except Exception as e:  # noqa: BLE001 — psutil reading must never escape
        sys_used_mb = sys_total_mb = sys_pct = None
        errors.append("psutil read failed: {0}".format(e))
    if sys_pct is None and not any("psutil" in m for m in errors):
        errors.append("psutil unavailable — system memory unknown")

    degraded = bool(errors)
    return {
        "degraded": degraded,
        "errors": errors,
        "backend": backend,
        "gpu_mem_used_mb": gpu_used_mb,
        "gpu_mem_total_mb": gpu_total_mb,
        "gpu_mem_pct": gpu_pct,
        "ollama_vram_mb": ollama_vram_mb,
        "models_loaded": models_loaded,
        "models_known": models_known,
        "system_mem_used_mb": sys_used_mb,
        "system_mem_total_mb": sys_total_mb,
        "system_mem_pct": sys_pct,
        "active_jobs": active_jobs,
    }


def pressure_metric(result: Dict) -> Optional[float]:
    """The 0-1 number backpressure decisions key on.

    nvidia: discrete VRAM %. apple/unknown: unified-memory % (GPU shares it).
    Returns None when no signal is available (caller must then PROCEED).
    """
    if result.get("backend") == "nvidia" and result.get("gpu_mem_pct") is not None:
        return result["gpu_mem_pct"]
    return result.get("system_mem_pct")


def is_model_loaded(result: Dict, model: str) -> bool:
    """True only when we positively know the model is resident in Ollama.

    When model state is unknown (Ollama unreachable) returns False so the caller
    warms up defensively rather than skipping a needed load.
    """
    if not result.get("models_known"):
        return False
    loaded = result.get("models_loaded") or []
    # Ollama reports e.g. "qwen3-vl:8b"; tolerate bare-name configs ("qwen3-vl").
    for name in loaded:
        if name == model or name.split(":", 1)[0] == model.split(":", 1)[0]:
            return True
    return False


def decide(result: Dict, threshold: Optional[float] = None) -> Tuple[str, str]:
    """Return ('PROCEED'|'WAIT', human reason).

    RED LINE: degraded / unknown pressure → PROCEED. Probe never blocks ingest
    on missing signal.
    """
    if threshold is None:
        threshold = config.GPU_MEM_THRESHOLD
    # RED LINE: if ANY source failed, the picture is incomplete/untrustworthy —
    # never WAIT on a partial reading (blocking is the dangerous action). This
    # also avoids treating system RAM as a VRAM proxy when nvidia-smi failed.
    if result.get("degraded"):
        return "PROCEED", "probe degraded — proceeding (sensor, not gate)"
    p = pressure_metric(result)
    if p is None:
        return "PROCEED", "no memory-pressure signal — proceeding"
    if p > threshold:
        return "WAIT", "memory at {0:.0%} > threshold {1:.0%} — GPU busy, waiting".format(p, threshold)
    return "PROCEED", "memory at {0:.0%} <= threshold {1:.0%}".format(p, threshold)


def summary_line(result: Dict) -> str:
    """One-line human-readable status (for `arkiv status`)."""
    backend = result.get("backend", "unknown")
    if result.get("backend") == "nvidia" and result.get("gpu_mem_pct") is not None:
        mem = "GPU {0:.0%} ({1:.0f}/{2:.0f}MB)".format(
            result["gpu_mem_pct"], result["gpu_mem_used_mb"], result["gpu_mem_total_mb"]
        )
    elif result.get("system_mem_pct") is not None:
        mem = "MEM {0:.0%} ({1:.0f}/{2:.0f}MB)".format(
            result["system_mem_pct"], result["system_mem_used_mb"], result["system_mem_total_mb"]
        )
    else:
        mem = "MEM unknown"
    models = ",".join(result.get("models_loaded") or []) or "none"
    jobs = result.get("active_jobs")
    jobs_str = "?" if jobs is None else str(jobs)
    flag = " [degraded]" if result.get("degraded") else ""
    return "[{0}] {1} | models: {2} | active jobs: {3}{4}".format(backend, mem, models, jobs_str, flag)
