# arkiv — Full Install Guide

## Prerequisites

| Dependency | macOS (brew) | Linux (apt) | Windows |
|---|---|---|---|
| Python 3.9+ | `brew install python` | `sudo apt install python3 python3-venv` | [python.org](https://python.org) |
| FFmpeg 6.0+ | `brew install ffmpeg` | `sudo apt install ffmpeg` | [ffmpeg.org](https://ffmpeg.org/download.html) |
| Ollama | `brew install ollama` | [ollama.com/download](https://ollama.com/download) | [ollama.com/download](https://ollama.com/download) |

---

## macOS (brew + pip)

```bash
brew install python ffmpeg ollama
git clone https://github.com/vulture-s/arkiv.git && cd arkiv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install mlx-whisper          # Apple Silicon
ollama pull nomic-embed-text && ollama pull qwen3-vl:8b && ollama pull qwen2.5:14b
python health.py
```

> **Resolve plugin:** Requires Python 3.10 Framework .pkg from [python.org](https://www.python.org/downloads/release/python-31011/) — Homebrew Python is not recognized by Resolve.

---

## Linux (pip)

```bash
sudo apt install python3 python3-venv ffmpeg
git clone https://github.com/vulture-s/arkiv.git && cd arkiv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install faster-whisper torch  # NVIDIA GPU
# pip install faster-whisper      # CPU fallback
ollama pull nomic-embed-text && ollama pull qwen3-vl:8b && ollama pull qwen2.5:14b
python health.py
```

---

## Windows (pip, PowerShell)

Install [Python 3.9+](https://python.org), [FFmpeg](https://ffmpeg.org/download.html), and [Ollama](https://ollama.com/download) manually, then:

```powershell
git clone https://github.com/vulture-s/arkiv.git; cd arkiv
python -m venv .venv; .\.venv\Scripts\activate
pip install -r requirements.txt
pip install faster-whisper torch  # NVIDIA GPU
# pip install faster-whisper      # CPU fallback
ollama pull nomic-embed-text; ollama pull qwen3-vl:8b; ollama pull qwen2.5:14b
$env:PYTHONUTF8=1; python health.py
```

> **UTF-8 required for CJK search.** Always set `$env:PYTHONUTF8=1` before starting the server.

---

## Docker (all platforms)

```bash
git clone https://github.com/vulture-s/arkiv.git && cd arkiv
docker compose up -d
# Open http://localhost:8501
```

Models are pulled automatically on first run. For NVIDIA GPU passthrough on Linux, uncomment the `deploy` block in `docker-compose.yml`.

---

## Verify

```bash
python health.py
# All required checks: PASS. SKIP = optional, does not affect functionality.
```

---

## Tauri desktop app (optional)

```bash
npm install && cargo tauri dev   # requires Node.js + Rust toolchain
```
