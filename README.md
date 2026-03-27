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

## Features

- **Semantic search** — query in natural language (Chinese/English/Japanese)
- **AI transcription** — Whisper large-v3 (Apple Silicon MLX / NVIDIA CUDA / CPU)
- **Frame analysis** — llava:7b scene descriptions
- **Rating system** — GOOD / NG / Review with notes
- **Tag system** — auto (AI) + manual tags with autocomplete
- **DaVinci Resolve UI** — dark theme, 3-panel layout, filmstrip, waveform

## Quick Start

### Prerequisites
- Python 3.9+
- FFmpeg 6.0+
- Ollama with `nomic-embed-text` model

### Install

```bash
git clone https://github.com/yourname/arkiv.git
cd arkiv
pip install -r requirements.txt

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
| `ARKIV_OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `ARKIV_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `ARKIV_VISION_MODEL` | `llava:7b` | Vision model for frames |
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

## License

MIT
