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


DB_PATH = _validate_writable_path(
    Path(os.getenv("ARKIV_DB_PATH", str(BASE_DIR / "media.db"))), "ARKIV_DB_PATH"
)
CHROMA_PATH = _validate_writable_path(
    Path(os.getenv("ARKIV_CHROMA_PATH", str(BASE_DIR / "chroma_db"))), "ARKIV_CHROMA_PATH"
)
THUMBNAILS_DIR = _validate_writable_path(
    Path(os.getenv("ARKIV_THUMBNAILS_DIR", str(BASE_DIR / "thumbnails"))), "ARKIV_THUMBNAILS_DIR"
)
PROXIES_DIR = _validate_writable_path(
    Path(os.getenv("ARKIV_PROXIES_DIR", str(BASE_DIR / "proxies"))), "ARKIV_PROXIES_DIR"
)
PROJECT_ROOT = _validate_writable_path(
    Path(os.getenv("ARKIV_PROJECT_ROOT", str(BASE_DIR))), "ARKIV_PROJECT_ROOT"
)


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
EMBED_MODEL = os.getenv("ARKIV_EMBED_MODEL", "nomic-embed-text")
VISION_MODEL = os.getenv("ARKIV_VISION_MODEL", "qwen3-vl:8b")

EXIFTOOL_PATH = os.getenv("ARKIV_EXIFTOOL_PATH", "exiftool")

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

COLLECTION_NAME = "media_assets"
EMBED_DIM = 768
