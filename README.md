# arkiv

**Local-first media asset manager with semantic search.**

Search, browse, rate, and tag your video/audio assets using AI-powered transcription and vector search. DaVinci Resolve-inspired dark UI.

---

## Architecture

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│  index.html │◄──►│  server.py   │◄──►│   db.py      │
│  (Tailwind) │    │  (FastAPI)   │    │  (SQLite)    │
└─────────────┘    └──────┬───────┘    └─────────────┘
                          │
                   ┌──────┴───────┐
                   │  vectordb.py │◄──► ChromaDB
                   │  (Ollama)    │     (nomic-embed-text)
                   └──────────────┘
                          │
            ┌─────────────┼─────────────┐
            │             │             │
      ┌─────┴─────┐ ┌────┴────┐ ┌──────┴──────┐
      │ ingest.py │ │frames.py│ │transcribe.py│
      │ (FFmpeg)  │ │(scenes) │ │  (Whisper)  │
      └───────────┘ └─────────┘ └─────────────┘
```

## Screenshots

<!-- TODO: Add screenshots -->
| Grid View | Inspector | Analytics |
|-----------|-----------|-----------|
| ![grid](docs/screenshots/grid.png) | ![inspector](docs/screenshots/inspector.png) | ![analytics](docs/screenshots/analytics.png) |

## Features

- **Semantic search** — query in natural language (Chinese/English/Japanese)
- **AI transcription** — Whisper large-v3 (Apple Silicon MLX / NVIDIA CUDA / CPU)
- **Frame analysis** — llava:7b scene descriptions
- **Rating system** — GOOD / NG / Review with notes
- **Tag system** — auto (AI) + manual tags with autocomplete
- **DaVinci Resolve UI** — dark theme, 3-panel layout, filmstrip, waveform
- **Export** — SRT, VTT, TXT, EDL subtitle/edit formats
- **Tauri native app** — desktop app with native file/folder dialogs
- **DaVinci Resolve plugin** — search and import directly from Resolve

## Quick Start

### Prerequisites
- Python 3.9+
- FFmpeg 6.0+
- Ollama with `nomic-embed-text` model

### Install

```bash
git clone https://github.com/yourname/arkiv.git
cd arkiv
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Install Whisper backend (pick one):
pip install mlx-whisper          # macOS Apple Silicon
pip install faster-whisper torch  # NVIDIA GPU
pip install faster-whisper        # CPU fallback

# Pull Ollama models
ollama pull nomic-embed-text
ollama pull llava:7b  # optional, for frame descriptions

# Check environment
python health.py
```

### Ingest Media

```bash
# Ingest a directory of video/audio files
python ingest.py --dir /path/to/media

# Build vector search index
python embed.py
```

### Run

```bash
uvicorn server:app --host 0.0.0.0 --port 8501
# Open http://localhost:8501
```

### Docker

```bash
docker compose up -d
# Open http://localhost:8501
```

## Configuration

Copy `.env.example` to `.env` and customize:

| Variable | Default | Description |
|----------|---------|-------------|
| `ARKIV_DB_PATH` | `./media.db` | SQLite database path |
| `ARKIV_CHROMA_PATH` | `./chroma_db` | ChromaDB vector store |
| `ARKIV_THUMBNAILS_DIR` | `./thumbnails` | Thumbnail output dir |
| `ARKIV_OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `ARKIV_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `ARKIV_VISION_MODEL` | `llava:7b` | Vision model for frames |
| `ARKIV_WHISPER_MODEL` | `mlx-community/whisper-large-v3-mlx` | Whisper model |
| `ARKIV_HOST` | `0.0.0.0` | Server bind address |
| `ARKIV_PORT` | `8501` | Server port |

## CLI Usage

```bash
# Ingest with options
python ingest.py --dir ./media --limit 10 --skip-vision

# Rebuild vector index
python embed.py

# Health check
python health.py
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Tailwind CSS + vanilla JS |
| Backend | FastAPI + Uvicorn |
| Database | SQLite (metadata) + ChromaDB (vectors) |
| Embedding | Ollama nomic-embed-text (768d, cosine) |
| Transcription | mlx-whisper (Mac) / faster-whisper (CUDA/CPU) |
| Vision | Ollama llava:7b |
| Media | FFmpeg (probe, thumbnails, scene detection) |
| Desktop | Tauri (native app wrapper) |

## FAQ

**Q: Which Whisper backend should I use?**
- macOS with Apple Silicon: `mlx-whisper` (fastest, uses Metal GPU)
- NVIDIA GPU: `faster-whisper` + `torch` (CUDA acceleration)
- CPU only: `faster-whisper` (slower but works everywhere)

**Q: Do I need Ollama running?**
Yes, for semantic search (embedding) and optional frame descriptions. Run `ollama serve` before starting arkiv.

**Q: How do I add media?**
Use the `+` button in the Media Pool sidebar, or run `python ingest.py --dir /path/to/media` from CLI.

**Q: Can I use this without Docker?**
Yes — the native Python install is the primary workflow. Docker is optional for deployment.

**Q: What file formats are supported?**
Video: `.mp4`, `.mov`, `.m4v`, `.mts`
Audio: `.wav`, `.mp3`, `.m4a`, `.aac`

## License

MIT
