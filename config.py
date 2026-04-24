"""
arkiv — Configuration
All hardcoded values centralized here. Override via environment variables.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DB_PATH = Path(os.getenv("ARKIV_DB_PATH", str(BASE_DIR / "media.db")))
CHROMA_PATH = Path(os.getenv("ARKIV_CHROMA_PATH", str(BASE_DIR / "chroma_db")))
THUMBNAILS_DIR = Path(os.getenv("ARKIV_THUMBNAILS_DIR", str(BASE_DIR / "thumbnails")))
PROXIES_DIR = Path(os.getenv("ARKIV_PROXIES_DIR", str(BASE_DIR / "proxies")))
PROJECT_ROOT = Path(os.getenv("ARKIV_PROJECT_ROOT", str(BASE_DIR)))


def proxy_path_for(media_id: int, abs_source_path: str) -> Path:
    # media_id alone is not enough — a proxies/ dir copied between
    # installations would serve another user's content for the same id.
    # Scoping by a hash of the absolute source path makes collisions
    # across machines impossible.
    digest = hashlib.sha1(str(abs_source_path).encode("utf-8")).hexdigest()[:10]
    return PROXIES_DIR / f"{media_id}_{digest}.mp4"

# ── Ollama ───────────────────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("ARKIV_OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("ARKIV_EMBED_MODEL", "nomic-embed-text")
VISION_MODEL = os.getenv("ARKIV_VISION_MODEL", "qwen3-vl:8b")

# ── ExifTool ─────────────────────────────────────────────────────────────────
EXIFTOOL_PATH = os.getenv("ARKIV_EXIFTOOL_PATH", "exiftool")

# ── Whisper ──────────────────────────────────────────────────────────────────
import platform as _plat
_IS_MLX = _plat.system() == "Darwin" and _plat.machine() == "arm64"
_DEFAULT_WHISPER = "mlx-community/whisper-large-v3-turbo" if _IS_MLX else "large-v3-turbo"
WHISPER_MODEL = os.getenv("ARKIV_WHISPER_MODEL", _DEFAULT_WHISPER)

# ── Transcription ────────────────────────────────────────────────────────────
# Custom vocabulary: comma-separated terms for Whisper initial_prompt
# e.g. "Furutech,Alpha Design Labs,byebyenoise!"
CUSTOM_VOCABULARY = os.getenv("ARKIV_CUSTOM_VOCABULARY", "")
# Filter dictionary: comma-separated words to remove from transcript
# e.g. "嗯,啊,呃,那個"
FILTER_WORDS = os.getenv("ARKIV_FILTER_WORDS", "")

# ── Server ───────────────────────────────────────────────────────────────────
HOST = os.getenv("ARKIV_HOST", "0.0.0.0")
PORT = int(os.getenv("ARKIV_PORT", "8501"))

# ── ChromaDB ─────────────────────────────────────────────────────────────────
COLLECTION_NAME = "media_assets"
EMBED_DIM = 768
