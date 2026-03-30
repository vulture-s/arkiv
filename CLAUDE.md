# CLAUDE.md — arkiv

## What is arkiv?

A local-first media asset manager with AI-powered semantic search. Ingests video/audio files, transcribes them with Whisper, extracts keyframes, generates scene descriptions with LLaVA, and indexes everything in a vector database for natural-language search (Chinese/English/Japanese).

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Vanilla JS + Tailwind CSS (CDN), single `index.html` SPA |
| Backend | FastAPI + Uvicorn (Python 3.9+) |
| Database | SQLite (metadata) + ChromaDB (vector embeddings) |
| Embeddings | Ollama + nomic-embed-text (768-dim, cosine) |
| Transcription | mlx-whisper (macOS) / faster-whisper (GPU/CPU) |
| Vision | Ollama llava:7b |
| Media | FFmpeg 6.0+ |
| Desktop | Tauri 2.x (Rust) |
| Container | Docker + Docker Compose |

## Project Structure

```
arkiv/
├── server.py          # FastAPI routes, WebSocket ingest broadcaster, static serving
├── db.py              # SQLite schema, CRUD, migrations, pagination
├── api.py             # Service layer between UI and data stores
├── config.py          # Centralized env var configuration
├── vectordb.py        # ChromaDB integration, CJK/Latin chunking, embedding
├── ingest.py          # Media ingestion pipeline (probe → thumbnail → transcribe → vision → store)
├── transcribe.py      # Whisper wrapper, model warm-up, LLM post-correction
├── frames.py          # FFmpeg scene detection + keyframe extraction
├── vision.py          # LLaVA frame description + keyword tagging
├── embed.py           # CLI tool: build/rebuild ChromaDB index
├── health.py          # Environment validation (Python, FFmpeg, Ollama, Whisper)
├── watch.py           # File system watcher for auto-ingest
├── styles.py          # Styling/formatting utilities
├── app.py             # Legacy Streamlit UI (unused)
├── index.html         # SPA frontend (Tailwind, dark theme)
├── requirements.txt   # Python dependencies
├── Dockerfile         # Multi-stage Docker build
├── docker-compose.yml # arkiv + ollama services
├── install.sh         # One-click macOS installer
├── smoke-test.sh      # Integration test suite
├── .env.example       # Environment variable template
├── resolve_plugin/    # DaVinci Resolve integration
│   └── arkiv_resolve.py
└── src-tauri/         # Tauri native desktop app
    ├── Cargo.toml
    ├── tauri.conf.json
    └── src/main.rs
```

## Architecture

```
index.html (SPA) ↔ server.py (FastAPI) ↔ db.py (SQLite) + vectordb.py (ChromaDB)
                                │
                    ┌───────────┼───────────┐
                    │           │           │
              ingest.py    transcribe.py  vision.py
              (FFmpeg)     (Whisper)      (LLaVA)
                    │
              frames.py
              (keyframes)
```

**Layering:** `config.py` → `db.py` / `vectordb.py` → `api.py` (service) → `server.py` (routes)

## Common Commands

```bash
# Start the server
uvicorn server:app --host 0.0.0.0 --port 8501

# Health check
python health.py

# Ingest media files
python ingest.py --dir /path/to/media [--limit N] [--skip-vision]

# Build/rebuild vector index
python embed.py [--rebuild] [--search "query"]

# Auto-ingest on file changes
python watch.py /path/to/watch [--interval 10]

# Initialize database
python -c "import db; db.init_db()"

# Run smoke tests (requires running server)
bash smoke-test.sh

# Docker
docker compose up
```

## Environment Variables

All configured in `config.py`, override via env vars or `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `ARKIV_DB_PATH` | `./media.db` | SQLite database path |
| `ARKIV_CHROMA_PATH` | `./chroma_db` | ChromaDB storage path |
| `ARKIV_THUMBNAILS_DIR` | `./thumbnails` | Thumbnail output directory |
| `ARKIV_OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `ARKIV_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `ARKIV_VISION_MODEL` | `llava:7b` | Vision model for frame descriptions |
| `ARKIV_WHISPER_MODEL` | `mlx-community/whisper-large-v3-mlx` | Whisper model (macOS default) |
| `ARKIV_HOST` | `0.0.0.0` | Server bind host |
| `ARKIV_PORT` | `8501` | Server bind port |

## Code Conventions

- **Python style:** snake_case functions/vars, SCREAMING_SNAKE_CASE constants
- **Type hints:** Use `from __future__ import annotations` and modern union syntax (`str | None`)
- **Section headers:** Markdown-style `# ── Section Name ──────` separators in Python files
- **Docstrings:** Module-level docstrings with usage examples; shebang `#!/usr/bin/env python3`
- **Imports:** Grouped (stdlib → third-party → local), explicit (no wildcards)
- **Error handling:** try/except with sensible fallback defaults, especially for optional services (Whisper, Ollama)
- **Architecture:** Keep pipeline stages decoupled (ingest, transcribe, frames, vision each in own module)
- **Frontend:** No JS framework — vanilla DOM manipulation with Tailwind utilities
- **Theme:** DaVinci Resolve-inspired dark UI (surface `#1a1a1e`, accent `#3b82f6`)
- **Commit messages:** Semantic prefixes (`feat:`, `fix:`, `auto:`)

## Key Design Decisions

- **Local-first:** No cloud dependencies. All AI runs locally via Ollama and Whisper
- **CJK-aware chunking:** vectordb.py uses different chunking strategies for CJK vs Latin text
- **Whisper platform split:** mlx-whisper on macOS Apple Silicon, faster-whisper elsewhere
- **Model warm-up:** Whisper model pre-loaded at startup to avoid per-request latency
- **WebSocket broadcasting:** Ingest progress streamed to UI via WebSocket (IngestBroadcaster in server.py)
- **Schema migrations:** db.py uses try/except ALTER TABLE to add columns incrementally
- **Single-file SPA:** index.html is self-contained (~55KB) with inline JS and Tailwind CDN

## Testing

No unit test framework is set up. Validation is done via:
- `python health.py` — checks Python version, FFmpeg, Ollama connectivity, Whisper availability
- `bash smoke-test.sh` — integration tests against a running server (HTTP endpoint checks, search, static files)

## External Dependencies

Requires running services:
- **Ollama** (`ollama serve`) with models: `nomic-embed-text`, optionally `llava:7b`
- **FFmpeg 6.0+** on PATH

## Files to Never Commit

- `media.db` — SQLite database
- `chroma_db/` — ChromaDB storage
- `thumbnails/` — Generated thumbnails
- `.env` — Environment overrides (use `.env.example` as template)
