# arkiv — Windows Quickstart (x64)

A 10-minute hand-holding guide to get arkiv running on Windows 10/11 (64-bit).
arkiv runs **100% locally** — nothing is uploaded. It needs three helpers on your
machine first (Ollama for the AI models, FFmpeg for video, ExifTool for camera
metadata), then it does the rest.

> **Heads up on size:** the installer is ~230 MB and the AI models are ~15 GB
> total, downloaded once. Budget a few minutes on first pull, and let the first
> ingest run overnight for a big library.

---

## 1. Install the three helpers

From PowerShell:

```powershell
winget install Ollama.Ollama
winget install Gyan.FFmpeg
winget install OliverBetz.ExifTool
```

Close and reopen PowerShell afterwards so the new tools land on `PATH`.

Start Ollama (leave it running — arkiv talks to it in the background):

```powershell
ollama serve   # or just launch Ollama once from the Start menu; it stays running
```

Pull the three models arkiv uses (embeddings + vision + chat):

```powershell
ollama pull bge-m3          # semantic search embeddings (multilingual)
ollama pull qwen2.5vl:7b    # frame descriptions (vision)
ollama pull qwen2.5:14b     # chat / RAG
```

> These are the **defaults**. `qwen3-vl:8b` is a higher-quality vision model but
> ~10× slower — don't pull it unless you specifically want it and set
> `ARKIV_VISION_MODEL=qwen3-vl:8b`. Note that having `qwen3-vl:8b` does **not**
> satisfy the `qwen2.5vl:7b` check: `/api/health` reports the configured model,
> so pull the default or set the override.

---

## 2. Open the app

**If you got the installer** (unsigned, so SmartScreen will warn once):

1. Run **`arkiv_<version>_x64-setup.exe`**. It installs per-user — no admin
   prompt — into `%LOCALAPPDATA%\arkiv\`. (An `.msi` is also published for
   admin/GPO deployment; it installs per-machine and does ask for elevation.)
2. **First launch only**: Windows SmartScreen shows *"Windows protected your
   PC"*. Click **More info** → **Run anyway**. This is the unsigned-installer
   warning, the same thing macOS does with right-click → Open; it does not come
   back afterwards.
3. Launch **arkiv** from the Start menu.

The app starts its own backend on a free local port and opens a window. Your
library lives in `%LOCALAPPDATA%\com.hevin.arkiv\arkiv\` (DB, thumbnails, logs).

> **First run gives you something to look at.** On a fresh, empty library arkiv
> loads a **pre-indexed** sample of four CC-BY Blender clips — thumbnails,
> transcripts and vectors are baked in, so the grid is browsable and searchable
> immediately, with no pipeline run and no models required. Semantic search over
> it still needs Ollama (to embed your *query*). One click removes it, and it
> never touches a library that already has media in it. Licenses:
> `sample/LICENSES.md`.

**If you're running from source** instead:

```powershell
git clone https://github.com/vulture-s/arkiv.git
cd arkiv
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt -c constraints.txt
python health.py        # confirm everything is READY (see step 3)
python server.py        # then open http://localhost:8501
```

> On Windows, transcription runs on `faster-whisper`, not `mlx-whisper` — that
> one is Apple-Silicon-only. Nothing to configure; it is already the platform
> default in `requirements.txt`.

---

## 3. Confirm it's ready BEFORE your first ingest

From a source checkout, run the health check:

```powershell
python health.py
# All required checks: PASS. SKIP = optional, does not affect functionality.
```

With the packaged app you don't need a terminal — the same checks are served at
`/api/health` on the app's port, and the app surfaces them.

If it reports Ollama unreachable or a model missing, fix that first — otherwise
ingest/search will fail quietly. Common causes: Ollama not running (`ollama
serve`), or a model not pulled yet (step 1).

> **Chinese text looks garbled in the console?** Set UTF-8 for the session with
> `$env:PYTHONUTF8=1` before running. This only affects the terminal; the app
> itself is unaffected.

---

## 4. Ingest your first footage

Point arkiv at a folder of clips. **If your footage is not Chinese, pass the
language** — arkiv defaults to `zh` and will otherwise transcribe e.g. English
audio as garbled Chinese:

```powershell
python ingest.py --dir "D:\path\to\your\clips" --language en   # zh / en / ja / ko
```

### Ingesting from a NAS / network folder

By default arkiv only reads from a few places: this library's folder, your
Desktop, Documents, Videos and Pictures, and **network drives you have mapped**.
That limit is deliberate — it stops someone else on your network from using
arkiv to list every file on your PC. A NAS folder picked as a network path
(`\\NAS\home\Photos`) is outside it, and the app answers *"這個資料夾不在 arkiv
允許匯入的位置"*. Two ways in, pick either:

1. **Map the NAS share as a network drive** (simplest). File Explorer → *This PC*
   → *Map network drive* → pick a letter such as `Z:` and enter
   `\\NAS\home`. Then choose `Z:\Photos\…` in arkiv. Every mapped network
   drive is allowed automatically; your own `C:\` is not.
2. **Add the folder in Settings.** arkiv → *Settings* → *Ingest · 匯入* →
   *額外允許的資料夾*, one folder per line, e.g. `\\100.64.154.6\home\Photos`,
   then *儲存*. It applies to this library only. A whole local disk (`C:\`) is
   refused.

(Advanced: the `ARKIV_INGEST_ROOTS` environment variable *replaces* the default
list entirely — `;`-separated on Windows. Folders added in Settings are still
added on top.)

Then search in plain language (中文 or English) in the app. That's the moment
arkiv is built for: type a phrase, and matching clips from years of footage
surface.

---

## Troubleshooting

- **Empty grid / search returns nothing** → Ollama isn't running or models
  aren't pulled. Check `/api/health` or re-run `python health.py`.
- **`ffmpeg` not found even though you installed it** → WinGet can leave only a
  shim on `PATH` that a non-interactive session doesn't resolve. Point arkiv at
  the real binary with `ARKIV_FFMPEG_PATH`.
- **A `.mov`/HEVC clip won't play in the preview** → arkiv builds a proxy on
  ingest; give it a moment, or use the "build proxy" action.
- **Pro-camera files (`.braw`/`.r3d`) don't show up** → not yet supported for
  indexing (ffmpeg can't decode them without vendor SDKs). Sony `.mxf` does work.
- **Something broke** → send the log:
  `%LOCALAPPDATA%\com.hevin.arkiv\arkiv\logs\backend.log` (and `backend.log.prev`
  for the previous run). That's exactly what's needed to debug it.
