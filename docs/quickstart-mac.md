# arkiv — Mac Quickstart (Apple Silicon)

A 10-minute hand-holding guide to get arkiv running on an Apple Silicon Mac.
arkiv runs **100% locally** — nothing is uploaded. It needs three helpers on your
machine first (Ollama for the AI models, FFmpeg for video, ExifTool for camera
metadata), then it does the rest.

> **Heads up on size:** the AI models are ~15 GB total and download once. Budget a
> few minutes on first pull, and let the first ingest run overnight for a big library.

---

## 1. Install the three helpers

```bash
brew install ollama ffmpeg exiftool
```

Start Ollama (leave it running — arkiv talks to it in the background):

```bash
ollama serve   # or just open the Ollama.app once; it stays running
```

Pull the three models arkiv uses (embeddings + vision + chat):

```bash
ollama pull bge-m3          # semantic search embeddings (multilingual)
ollama pull qwen2.5vl:7b    # frame descriptions (vision)
ollama pull qwen2.5:14b     # chat / RAG
```

> These are the **defaults**. `qwen3-vl:8b` is a higher-quality vision model but
> ~10× slower — don't pull it unless you specifically want it and set
> `ARKIV_VISION_MODEL=qwen3-vl:8b`.

---

## 2. Open the app

**If you got the `arkiv.app` / `.dmg`** (unsigned, so macOS Gatekeeper will warn once):

1. Open the `.dmg` and drag **arkiv** to **Applications** (or just keep the `.app`).
2. **First launch only**: right-click (or Control-click) `arkiv.app` → **Open** →
   **Open** again in the dialog. This clears the "unidentified developer" warning
   *once*; after that you can double-click normally. (Double-clicking the first
   time just shows a dead-end "can't be opened" dialog — use right-click → Open.)

The app starts its own backend and opens a window. Your library lives in
`~/Library/Application Support/com.hevin.arkiv/arkiv/` (DB, thumbnails, logs).

**If you're running from source** instead:

```bash
git clone https://github.com/vulture-s/arkiv.git && cd arkiv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install mlx-whisper
python health.py        # confirm everything is READY (see step 3)
python server.py        # then open http://localhost:8501
```

---

## 3. Confirm it's ready BEFORE your first ingest

From a source checkout (or the bundled python), run the health check:

```bash
python health.py
# All required checks: PASS. SKIP = optional, does not affect functionality.
```

If it reports Ollama unreachable or a model missing, fix that first — otherwise
ingest/search will fail quietly. Common causes: Ollama not running (`ollama serve`),
or a model not pulled yet (step 1).

---

## 4. Ingest your first footage

Point arkiv at a folder of clips. **If your footage is not Chinese, pass the
language** — arkiv defaults to `zh` and will otherwise transcribe e.g. English
audio as garbled Chinese:

```bash
python ingest.py --dir "/path/to/your/clips" --language en   # zh / en / ja / ko
```

Then search in plain language (中文 or English) in the app. That's the moment
arkiv is built for: type a phrase, and matching clips from years of footage surface.

---

## Troubleshooting

- **Empty grid / search returns nothing** → Ollama isn't running or models aren't
  pulled. Re-run `python health.py`.
- **A `.mov`/HEVC clip won't play in the preview** → arkiv builds a proxy on
  ingest; give it a moment, or use the "build proxy" action.
- **Pro-camera files (`.mxf`/`.braw`/`.r3d`) don't show up** → not yet supported
  for indexing (ffmpeg can't decode them without vendor SDKs).
- **Something broke** → send the log: `~/Library/Application Support/com.hevin.arkiv/arkiv/logs/backend.log`
  (and `backend.log.prev` for the previous run). That's exactly what's needed to
  debug it.
