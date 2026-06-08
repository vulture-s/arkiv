from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).parent

# Codex Round-2 audit (J3): without bounds, ARKIV_PROXIES_DIR=/etc would have
# arkiv write generated proxy mp4 files into /etc on every HEVC ingest. Same
# risk for THUMBNAILS_DIR / CHROMA_PATH / DB_PATH (any operator-tunable
# writable path). Hard-fail when env override resolves under a system root.
_SYSTEM_DIR_DENYLIST = (
    "/etc", "/usr", "/bin", "/sbin", "/sys", "/proc", "/dev",
    "/var/log", "/var/run", "/Library", "/System",
    "/private/etc", "/private/var/log", "/private/var/run",
)


def _validate_writable_path(path: Path, env_var: str) -> Path:
    canonical = path.expanduser().resolve()
    for prefix in _SYSTEM_DIR_DENYLIST:
        try:
            canonical.relative_to(prefix)
        except ValueError:
            continue
        raise ValueError(
            f"{env_var} 不可指向系統目錄：{canonical}（matched {prefix}; denylist={_SYSTEM_DIR_DENYLIST}）"
        )
    return path


# Codex Round-2 audit (J5): a malicious ARKIV_OLLAMA_URL like
# http://169.254.169.254/latest/meta-data/ would have arkiv server proxy cloud
# metadata queries on every embed / vision call (SSRF). Restrict to http/https
# + reject link-local. Tailscale 100.x is intentionally allowed (Ollama on NAS).
def _validate_http_url(url: str, env_var: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{env_var} 必須是 http/https scheme：{url!r}")
    host = parsed.hostname or ""
    if host.startswith("169.254.") or host == "0.0.0.0":
        raise ValueError(
            f"{env_var} 不可指向 link-local / cloud metadata 位址：{url!r}"
        )
    return url


# Phase 8.0c: PROJECT_ROOT = single source of truth. 4 storage paths
# default to PROJECT_ROOT/.arkiv/<xxx>; explicit ARKIV_<X>_PATH env still
# overrides per-path. Before 8.0c, defaults were BASE_DIR/xxx so "moving
# PROJECT_ROOT" left thumbnails/proxies stranded at install location —
# the 2026-05-15 thumbnails→NAS symlink workaround was a band-aid for
# exactly this gap (server.py:70 also hardcoded ROOT, fixed in 2026-05-21).
PROJECT_ROOT = _validate_writable_path(
    Path(os.getenv("ARKIV_PROJECT_ROOT", str(BASE_DIR))), "ARKIV_PROJECT_ROOT"
)
_ARKIV_DIR = PROJECT_ROOT / ".arkiv"
_ASCMHL_DIR = PROJECT_ROOT / "ascmhl"

DB_PATH = _validate_writable_path(
    Path(os.getenv("ARKIV_DB_PATH", str(_ARKIV_DIR / "project.db"))), "ARKIV_DB_PATH"
)
CHROMA_PATH = _validate_writable_path(
    Path(os.getenv("ARKIV_CHROMA_PATH", str(_ARKIV_DIR / "chroma_db"))), "ARKIV_CHROMA_PATH"
)
THUMBNAILS_DIR = _validate_writable_path(
    Path(os.getenv("ARKIV_THUMBNAILS_DIR", str(_ARKIV_DIR / "thumbnails"))), "ARKIV_THUMBNAILS_DIR"
)
PROXIES_DIR = _validate_writable_path(
    Path(os.getenv("ARKIV_PROXIES_DIR", str(_ARKIV_DIR / "proxies"))), "ARKIV_PROXIES_DIR"
)

# Auth bootstrap (auth-tokens-1b handover).
ARKIV_ADMIN_BOOTSTRAP_TOKEN = os.getenv("ARKIV_ADMIN_BOOTSTRAP_TOKEN", "").strip()
# Phase 16.1: when set, access tokens are stored as HMAC-SHA256(key, token)
# instead of bare SHA-256. Dual-read keeps existing sha256 tokens valid and
# upgrades them on next use. Keep this key safe — losing it invalidates every
# HMAC token (re-mint required), the same failure mode as losing the DB.
ARKIV_TOKEN_HMAC_KEY = os.getenv("ARKIV_TOKEN_HMAC_KEY", "").strip()


def proxy_path_for(media_id: int, abs_source_path: str) -> Path:
    # media_id alone is not enough — a proxies/ dir copied between
    # installations would serve another user's content for the same id.
    # Scoping by a hash of the absolute source path makes collisions
    # across machines impossible.
    digest = hashlib.sha1(str(abs_source_path).encode("utf-8")).hexdigest()[:10]
    return PROXIES_DIR / f"{media_id}_{digest}.mp4"

# ── Ollama ───────────────────────────────────────────────────────────────────
OLLAMA_URL = _validate_http_url(
    os.getenv("ARKIV_OLLAMA_URL", "http://localhost:11434"), "ARKIV_OLLAMA_URL"
)
ARKIV_CHAT_MODEL = os.getenv("ARKIV_CHAT_MODEL", "qwen2.5:14b")
# Intent classification reuses the chat model by default so a single
# `ollama pull` covers chat end-to-end. Override with a smaller/faster model
# (e.g. qwen2.5:7b-instruct) only if it is actually installed on the host.
ARKIV_INTENT_MODEL = os.getenv("ARKIV_INTENT_MODEL", ARKIV_CHAT_MODEL)
OLLAMA_CHAT_MODEL = os.getenv(
    "ARKIV_OLLAMA_CHAT_MODEL",
    ARKIV_CHAT_MODEL,
)
OLLAMA_EMBED_MODEL = os.getenv(
    "ARKIV_OLLAMA_EMBED_MODEL",
    os.getenv("ARKIV_EMBED_MODEL", "bge-m3"),
)
OLLAMA_VISION_MODEL = os.getenv(
    "ARKIV_OLLAMA_VISION_MODEL",
    os.getenv("ARKIV_VISION_MODEL", "qwen3-vl:8b"),
)
EMBED_MODEL = OLLAMA_EMBED_MODEL
VISION_MODEL = OLLAMA_VISION_MODEL

# Cap the vision model's context window so it fits in GPU VRAM. qwen3-vl's
# default context balloons the model to ~28 GB, which on a 12 GB GPU (e.g.
# RTX 4070) offloads ~75% to CPU and drops vision to minutes/frame. 16384 is
# large enough to hold a 720p frame's image tokens + prompt + response without
# truncating perception, while keeping the model fully resident on GPU
# (~9.4 GB) → seconds/frame. Raise for bigger frames; lower if VRAM-starved.
def _read_vision_num_ctx() -> int:
    """Parse ARKIV_OLLAMA_VISION_NUM_CTX, falling back to the default on a
    missing / non-integer / non-positive value so a malformed env var can't
    crash import or silently load the vision model with an unusable context."""
    raw = os.getenv("ARKIV_OLLAMA_VISION_NUM_CTX", "16384")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return 16384
    return val if val > 0 else 16384


OLLAMA_VISION_NUM_CTX = _read_vision_num_ctx()

def _detect_exiftool() -> str:
    """Resolve ExifTool binary path via fallback chain.

    Priority: ARKIV_EXIFTOOL_PATH env > shutil.which('exiftool') > common
    per-platform install paths. Returns "exiftool" literal as last resort
    so subprocess fails loudly (WinError 2 / FileNotFoundError).

    Background: winget/scoop/chocolatey on Windows don't add ExifTool to
    PATH by default. Before this, fresh PC install would silent-skip every
    exiftool_extract() call.
    """
    import shutil as _shutil

    env_val = os.getenv("ARKIV_EXIFTOOL_PATH")
    if env_val:
        return env_val

    which = _shutil.which("exiftool")
    if which:
        return which

    candidates = [
        # Windows
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ExifTool" / "exiftool.exe",
        Path("C:/Program Files/exiftool/exiftool.exe"),
        Path("C:/ProgramData/chocolatey/bin/exiftool.exe"),
        Path(os.environ.get("USERPROFILE", "")) / "scoop" / "shims" / "exiftool.exe",
        Path("C:/Strawberry/perl/bin/exiftool.bat"),  # Strawberry Perl install
        # macOS
        Path("/opt/homebrew/bin/exiftool"),
        Path("/usr/local/bin/exiftool"),  # Intel brew / Linux manual / Arch / NixOS
        # Linux
        Path("/usr/bin/exiftool"),  # apt default
        Path.home() / ".local" / "bin" / "exiftool",  # pipx / user install
    ]
    for c in candidates:
        try:
            if c.exists():
                return str(c)
        except OSError:
            continue

    return "exiftool"


EXIFTOOL_PATH = _detect_exiftool()

import platform as _plat

_IS_MLX = _plat.system() == "Darwin" and _plat.machine() == "arm64"
_DEFAULT_WHISPER = "mlx-community/whisper-large-v3-turbo" if _IS_MLX else "large-v3-turbo"
WHISPER_MODEL = os.getenv("ARKIV_WHISPER_MODEL", _DEFAULT_WHISPER)

WHISPER_GUARD_DEFAULT_MODE = 4
WHISPER_GUARD_LAYERS = {
    0: {
        "name": "0 baseline",
        "model": "medium",
        "beam_size": 1,
        "language_hint": None,
        "vad_enabled": True,
        "condition_on_previous_text": True,
        "compression_ratio_threshold": None,
        "logprob_threshold": None,
        "llm_polish": False,
        "llm_model": None,
        "mlx_whisper": {
            "path_or_hf_repo": "mlx-community/whisper-medium",
            "beam_size": 1,
            "condition_on_previous_text": True,
            "compression_ratio_threshold": None,
            "logprob_threshold": None,
        },
        "whisperx": {
            "batch_size": 16,
            "beam_size": 1,
            "condition_on_previous_text": True,
            "compression_ratio_threshold": None,
            "log_prob_threshold": None,
        },
    },
    1: {
        "name": "1 +large-v3-turbo",
        "model": "large-v3-turbo",
        "beam_size": 1,
        "language_hint": None,
        "vad_enabled": True,
        "condition_on_previous_text": True,
        "compression_ratio_threshold": None,
        "logprob_threshold": None,
        "llm_polish": False,
        "llm_model": None,
        "mlx_whisper": {
            "path_or_hf_repo": "mlx-community/whisper-large-v3-turbo",
            "beam_size": 1,
            "condition_on_previous_text": True,
            "compression_ratio_threshold": None,
            "logprob_threshold": None,
        },
        "whisperx": {
            "batch_size": 16,
            "beam_size": 1,
            "condition_on_previous_text": True,
            "compression_ratio_threshold": None,
            "log_prob_threshold": None,
        },
    },
    2: {
        "name": "2 +zh hint",
        "model": "large-v3-turbo",
        "beam_size": 5,
        "language_hint": "zh",
        "vad_enabled": True,
        "condition_on_previous_text": True,
        "compression_ratio_threshold": None,
        "logprob_threshold": None,
        "llm_polish": False,
        "llm_model": None,
        "mlx_whisper": {
            "path_or_hf_repo": "mlx-community/whisper-large-v3-turbo",
            "beam_size": 5,
            "condition_on_previous_text": True,
            "compression_ratio_threshold": None,
            "logprob_threshold": None,
        },
        "whisperx": {
            "batch_size": 16,
            "beam_size": 5,
            "condition_on_previous_text": True,
            "compression_ratio_threshold": None,
            "log_prob_threshold": None,
        },
    },
    3: {
        "name": "3 +anti-hallucination",
        "model": "large-v3-turbo",
        "beam_size": 5,
        "language_hint": "zh",
        "vad_enabled": True,
        "condition_on_previous_text": False,
        "compression_ratio_threshold": 2.4,
        "logprob_threshold": -1.0,
        "llm_polish": False,
        "llm_model": None,
        "mlx_whisper": {
            "path_or_hf_repo": "mlx-community/whisper-large-v3-turbo",
            "beam_size": 5,
            "condition_on_previous_text": False,
            "compression_ratio_threshold": 2.4,
            "logprob_threshold": -1.0,
        },
        "whisperx": {
            "batch_size": 16,
            "beam_size": 5,
            "condition_on_previous_text": False,
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
        },
    },
    4: {
        "name": "4 +LLM polish",
        "model": "large-v3-turbo",
        "beam_size": 5,
        "language_hint": "zh",
        "vad_enabled": True,
        "condition_on_previous_text": False,
        "compression_ratio_threshold": 2.4,
        "logprob_threshold": -1.0,
        "llm_polish": True,
        "llm_model": "qwen2.5:14b",
        "mlx_whisper": {
            "path_or_hf_repo": "mlx-community/whisper-large-v3-turbo",
            "beam_size": 5,
            "condition_on_previous_text": False,
            "compression_ratio_threshold": 2.4,
            "logprob_threshold": -1.0,
        },
        "whisperx": {
            "batch_size": 16,
            "beam_size": 5,
            "condition_on_previous_text": False,
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
        },
    },
}

CUSTOM_VOCABULARY = os.getenv("ARKIV_CUSTOM_VOCABULARY", "")
FILTER_WORDS = os.getenv("ARKIV_FILTER_WORDS", "")

HOST = os.getenv("ARKIV_HOST", "0.0.0.0")
PORT = int(os.getenv("ARKIV_PORT", "8501"))

# --- Phase 11.5 resource-aware pipeline ---
# Backpressure trigger: ingest waits when memory pressure exceeds this fraction
# (GPU VRAM % on nvidia, unified-memory % on Apple Silicon). Clamped to (0, 1].
try:
    GPU_MEM_THRESHOLD = float(os.getenv("ARKIV_GPU_MEM_THRESHOLD", "0.8"))
except ValueError:
    GPU_MEM_THRESHOLD = 0.8
if not (0.0 < GPU_MEM_THRESHOLD <= 1.0):
    GPU_MEM_THRESHOLD = 0.8
# A/B-tuned in Phase 11.5d (live GPU). Default 1 = no Ollama-side parallelism.
try:
    OLLAMA_NUM_PARALLEL = max(1, int(os.getenv("OLLAMA_NUM_PARALLEL", "1")))
except ValueError:
    OLLAMA_NUM_PARALLEL = 1

def discover_projects():
    from projects import discover_projects as _discover_projects

    return _discover_projects()


COLLECTION_NAME = "media_assets"
EMBED_DIM = 1024  # bge-m3 default; informational only — ChromaDB infers dim from first vector
