"""
arkiv — Configuration
All hardcoded values centralized here. Override via environment variables.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DB_PATH = Path(os.getenv("ARKIV_DB_PATH", str(BASE_DIR / "media.db")))
CHROMA_PATH = Path(os.getenv("ARKIV_CHROMA_PATH", str(BASE_DIR / "chroma_db")))
THUMBNAILS_DIR = Path(os.getenv("ARKIV_THUMBNAILS_DIR", str(BASE_DIR / "thumbnails")))

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

# ── Server ───────────────────────────────────────────────────────────────────
HOST = os.getenv("ARKIV_HOST", "0.0.0.0")
PORT = int(os.getenv("ARKIV_PORT", "8501"))

# ── ChromaDB ─────────────────────────────────────────────────────────────────
COLLECTION_NAME = "media_assets"
EMBED_DIM = 768
