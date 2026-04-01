# Contributing to arkiv

Thanks for your interest in contributing! arkiv is a local-first media asset manager for filmmakers.

## Development Setup

### Prerequisites

- Python 3.9+
- FFmpeg 6.0+
- [Ollama](https://ollama.com/) with `nomic-embed-text` model
- Git

### Getting Started

```bash
git clone https://github.com/vulture-s/arkiv.git
cd arkiv
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Whisper backend (pick one)
pip install mlx-whisper          # macOS Apple Silicon
pip install faster-whisper torch  # NVIDIA GPU

# Ollama models
ollama pull nomic-embed-text
ollama pull llava:7b

# Verify environment
python health.py
```

### Running

```bash
uvicorn server:app --host 0.0.0.0 --port 8501 --reload
```

## How to Contribute

### Reporting Bugs

Use the [Bug Report](https://github.com/vulture-s/arkiv/issues/new?template=bug_report.md) template. Include:
- Your OS and Python version
- Steps to reproduce
- Expected vs actual behavior
- `python health.py` output if relevant

### Suggesting Features

Use the [Feature Request](https://github.com/vulture-s/arkiv/issues/new?template=feature_request.md) template.

### Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Test locally: ingest a file, search, verify UI
4. Submit a PR with a clear description

### Commit Messages

Use conventional commits:

```
feat: add ExifTool metadata extraction
fix: handle Unicode paths on Windows
docs: update Quick Start section
chore: update requirements.txt
```

## Code Style

- Python: follow existing patterns in the codebase
- Frontend: vanilla JS + Tailwind CSS (no build step)
- Keep modules small and focused — each `.py` file has a single responsibility
- Use `config.py` for all configurable values (never hardcode paths/URLs)

## Project Structure

```
server.py      — FastAPI REST API (the main entry point)
index.html     — Tailwind frontend (single file)
config.py      — Centralized configuration
db.py          — SQLite data layer
vectordb.py    — ChromaDB + Ollama embeddings
ingest.py      — Media file processing pipeline
transcribe.py  — Whisper transcription + anti-hallucination guard
frames.py      — FFmpeg frame extraction
vision.py      — Ollama vision analysis (JSON output)
health.py      — Environment health check
```

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
