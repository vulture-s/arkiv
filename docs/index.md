# arkiv — Quickstart

**Open-source AI metadata layer for DIT workflows — Resolve-native, CJK-first.**

arkiv attaches AI metadata (transcript, vision tags, atmosphere, edit position) to your footage and surfaces clips via natural-language search in Chinese, Japanese, or English.

---

## 5 steps to first search

**Step 1 — Install dependencies (macOS)**

```bash
brew install python ffmpeg ollama
```

Linux / Windows: see [install.md](install.md).

**Step 2 — Clone and set up**

```bash
git clone https://github.com/vulture-s/arkiv.git && cd arkiv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install mlx-whisper          # Apple Silicon
# pip install faster-whisper torch  # NVIDIA GPU
```

**Step 3 — Pull Ollama models**

```bash
ollama pull nomic-embed-text     # required
ollama pull qwen3-vl:8b          # optional: vision tags
ollama pull qwen2.5:14b          # optional: transcript polish
```

**Step 4 — Ingest media**

```bash
python ingest.py --dir /path/to/footage
python embed.py
```

**Step 5 — Search**

```bash
uvicorn server:app --host 0.0.0.0 --port 8501
# Open http://localhost:8501
```

---

## Verify setup

```bash
python health.py   # all required checks should PASS
```

---

## Links

- [Full install guide](install.md) — Linux, Windows, Docker
- [FAQ](faq.md) — common questions
- [Architecture](architecture-anti-hallucination-guard.md) — Whisper 4-layer guard

---

*MIT licensed. Self-hosted, no cloud, no telemetry.*
