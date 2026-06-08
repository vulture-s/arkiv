# Changelog
## Unreleased

## v0.7.0 - 2026-06-08

### Security
- **Optional HMAC token hashing (Phase 16.1).** Set `ARKIV_TOKEN_HMAC_KEY` to store access tokens as HMAC-SHA256 instead of bare SHA-256. Non-breaking: existing tokens keep working (dual-read) and upgrade to HMAC in place on next use. Losing the key invalidates HMAC tokens (re-mint), the same failure mode as losing the DB.
- **API responses no longer leak absolute filesystem paths (Phase 16.2).** Read-scope endpoints (`/api/media`, `/api/media/{id}`, `/api/media?q=`, `/api/search/all`, `/api/cache/info`) previously returned absolute paths that exposed the operator's directory tree. They now return PROJECT_ROOT-relative paths (basename for legacy absolute rows), and `/api/cache/info` no longer returns cache directory paths. open-file round-trips the relative path. (FCPXML `file://` URIs and user-supplied export destinations intentionally remain absolute — see `docs/phase-16.2-path-leak-triage.md`.)
- **`/api/search/all` path-leak fully closed.** The Phase 16.2 boundary strip handled `items[].path` and each item's `project_path`, but left two absolute paths reachable by a `videos_read` client: `items[].relative_path` (federation sets this to the *absolute* path for out-of-root rows — `_resolve_paths` falls back to `str(stored)` when `relative_to` fails) and `errors[].project_path` (the absolute project root, returned on a project timeout / preflight failure). The endpoint now drops the internal `absolute_path` / `relative_path` fields and basenames `project_path` across both items and errors. Regression: `tests/test_search_all_path_leak.py`.
- **WebSocket ingest auth + secrets hardening.** `/ws/ingest` was open to any LAN/tailnet peer or malicious browser page (the HTTP scope-gate doesn't reach `@app.websocket` routes). The handshake now enforces a same-origin / allowlisted-Origin check (CSWSH guard), the same loopback-trust rule as HTTP plus a `?token=` carrying `ingest_write`, and a 32-connection cap (close 1013 over the limit). Loopback trust additionally requires **no forwarding header** (`X-Forwarded-For` / `X-Real-IP` / `Forwarded` / `X-Forwarded-Host`) — closing the hole where a reverse proxy / `tailscale serve` (which connects *from* 127.0.0.1) handed every proxied remote request full admin. `ARKIV_ADMIN_BOOTSTRAP_TOKEN` now refuses tokens < 24 chars (a weak one was a remotely brute-forceable admin credential). `?token=` is redacted from uvicorn access logs; the token DB is created `0600` (data dir `0700`) on POSIX. `.env.example` now documents `ARKIV_TRUST_LOOPBACK` and `ARKIV_ADMIN_BOOTSTRAP_TOKEN`.

### Changed
- **Default embedding model is now `bge-m3` (1024-dim), up from `nomic-embed-text` (768-dim).** bge-m3 is multilingual (100+ languages, 8192-token context) and substantially stronger on Chinese retrieval while staying on par with nomic for English — a better default for mixed-language media libraries. **Breaking for existing indexes:** the dimension change means stored vectors are incompatible; run a full re-index (`python embed.py --rebuild` or 進階設定 → 重建向量索引) after upgrading. Override with `ARKIV_EMBED_MODEL` to keep the old model.

### Added
- **MCP server (Phase 14).** `mcp_server.py` exposes arkiv's local media knowledge layer to MCP clients (Claude Desktop, Claude Code, OpenClaw) over stdio, so an agent can search and read your footage library without going through the HTTP API. Six **read-only** tools: `search_media` (semantic search over transcripts + vision tags, SQL text fallback), `get_media`, `get_transcript`, `list_recent`, `library_stats`, `list_tags`. Reuses `db` + `vectordb` (does not import `server`, so no FastAPI startup cost) and returns JSON with `ensure_ascii=False`. **No absolute-path leak:** every path is PROJECT_ROOT-relative with a basename fallback for out-of-root rows — the same red line as the Phase 16.2 HTTP hardening, enforced in `_safe_path`. New optional dep `mcp`; 18 unit tests + an end-to-end stdio smoke. See `docs/phase-14-mcp-handover.md`.
- **Smart Collections — rule-driven curation with a GPS location signal (Tier 1).** `smart_collections.py` scores each clip's existing vision metadata (tags + both per-frame *and* media-level `content_type`/`atmosphere`/`energy`) against predefined collection definitions; a clip non-exclusively joins every collection it scores ≥ 0.40 for. Surfaced at `GET /api/collections` and in the UI sidebar (rule-driven, **not** ML clustering). New `geo.py` turns EXIF `gps_lat`/`gps_lon` into a stable *location label* — a named place from an operator gazetteer (haversine within radius) or a coarse rounded-coordinate cell fallback — exposed in the collection signal and usable as a booster condition. Pure-Python, no new deps, no external geocoding service; missing coords and the `(0,0)` GPS "null island" cameras emit with no fix never resolve to a phantom location. (Domain-specific definitions beyond the food/interior archetypes await real footage from those shoots so their tag vocabularies can be verified against live `qwen3-vl` output rather than guessed.)
- **`whisper_guard` package (Phase 10).** The pure text-level hallucination filters (`is_repetitive` / `has_char_loops` / `remove_char_loops` + a `HallucinationGuard` class) are now a standalone, stdlib-only package extracted from `transcribe.py`, staged for a future `whisper-guard` PyPI release. `transcribe.py` imports them with unchanged behavior. `install.sh` now also ships first-party package directories (via a `*/__init__.py` glob), so a copy-install includes the new package.
- **`export.py` corpus/JSONL CLI (Phase 12, core).** Turns a library's transcripts + vision metadata into LoRA/RAG-ready formats. `export.py corpus [--lang] [--out]` emits a merged plain-text corpus (transcripts only, no JSON residue); `export.py jsonl [--lang] [--out]` emits one JSON object per media (`{id, text, metadata}` with tags + frame descriptions); `export.py txt <id>` dumps a single transcript. Reads the DB directly (no server import); `ensure_ascii=False` keeps Chinese readable. (Batch-zip API remains deferred.)
- **Subtitle layout engine (Phase 12.5).** `subtitle.py` re-wraps raw Whisper text into broadcast-style captions, surfaced as `export.py srt <id> [--max-cjk N]`. Lines are capped in CJK units (default 14, Netflix zh-Hant; Latin counts 1/3) as a hard invariant, broken at natural points (CJK punctuation / spaces) without splitting a Latin word or separating a number from its measure word (`14字`). Long segments are time-split into multiple proportional cues; optional bilingual cues (original above translation). Uses segment-aligned timestamps when present, else a single transcript cue.
- **Resource-aware ingest pipeline (Phase 11.5).** Ingest now probes machine state before a vision batch and backs off when memory is saturated — directly addressing the 427-clip stress test that lost 20 frames to a cold-start timeout and hit a 28 GB unified-memory crunch.
  - `resource_probe.py` reports loaded Ollama models (`/api/ps`), GPU VRAM (nvidia-smi on PC), unified memory (psutil on Apple Silicon), and active-job count. It is a **sensor, not a gate**: any source failing degrades that field to `None` and never raises — a broken probe can't block ingest. `ARKIV_PROBE_DISABLE=true` makes it a full no-op.
  - **Backpressure**: before each vision batch, ingest waits (bounded exponential backoff, `ARKIV_BACKPRESSURE_MAX_WAIT`) while memory pressure exceeds `ARKIV_GPU_MEM_THRESHOLD` (default 0.8), then warms the vision model only if it isn't already resident. Systematizes the previously ad-hoc warm-up.
  - **`python ingest.py --queue status|cancel|retry [--job-id N]`** — a SQLite-backed job queue (no Redis/Celery) with type-derived priority.
  - **`python ingest.py --status [--json]`** — one-shot resource + queue snapshot and the decision the next vision phase would take.
  - New env vars: `ARKIV_GPU_MEM_THRESHOLD`, `ARKIV_BACKPRESSURE_MAX_WAIT`, `ARKIV_PROBE_DISABLE`, `OLLAMA_NUM_PARALLEL`. `psutil` added as a soft dependency.

  > ⚠️ Mock-tested + verified live on Apple Silicon (probe reads real Ollama/memory). The **11.5d throughput A/B** (`OLLAMA_NUM_PARALLEL=1` vs `2`) and the **427-clip cold-start-elimination** acceptance still need a real GPU run — see `docs/phase-11.5-acceptance.md`.
- **`POST /api/embed/rebuild`** (scope: `ingest_write`) — drops and rebuilds the ChromaDB semantic index from all media in a background subprocess. Backs the existing 進階設定 → 搜尋引擎 「重建向量索引」 button, which previously called a non-existent route (404).
- **More ingest container formats + cache visibility (ingest microtasks B3–B6).** `.mkv` / `.avi` / `.webm` now get thumbnails, frames, and vision — they were ingested into the DB but missing from `VIDEO_EXT`, so `is_video` was False and they silently skipped all visual processing. New `ingest.py --regenerate-thumbnails` rebuilds every video's poster (force-bypasses the reuse cache). `health.py` "Cache dirs" now reports file count + size for thumbnails / proxies / chroma_db, and the regen commands print the net size delta on completion.

### Fixed
- **WebSocket token auth was broken on `main`.** `server.py`'s `_ws_authorized` called `auth.resolve_raw_token`, but the refactor that introduced it was never staged (a `git add` missed `auth.py`), so on `main` every token-based `/ws/ingest` handshake hit `AttributeError` (caught by a bare `except` → rejected) and the ingest-progress WebSocket only worked on loopback. `resolve_raw_token` (hash / expiry / IP-allowlist / scope) is now extracted and `verify_token` delegates to it; the WS Origin check accepts same-origin on any deployment host/port plus the Vite dev origin (`:5173`), and the frontend WS URL carries the token.
- **Embedding-dimension mismatch now fails loud instead of silently degrading.** After the bge-m3 (1024-dim) default, querying a stale 768-dim index threw a raw ChromaDB error → 500s on chat/federation and a *silent* SQL-only degradation on `/api/media?q=` (a bare `except: pass` swallowed it). `vectordb` now stamps the embedding model + dim into the collection on create/rebuild, raises a clear `EmbeddingDimensionMismatch` ("run `python embed.py --rebuild`") when the active model differs, and defensively wraps query/upsert (catching legacy/unstamped indexes by message). `/api/media?q=` surfaces a `search_degraded` warning instead of hiding it, chat returns a clean error (not a 500), federation reports it per-project in `errors[]`, and an incremental `embed.py` run aborts loudly. `config.EMBED_DIM` is no longer purely informational.
- **`install.sh` no longer hangs on headless installs.** Step 8's desktop-shortcut `cp` into `~/Desktop` blocked indefinitely on a headless / piped (`curl | bash`) / nohup install — macOS Desktop (TCC) prompts for access with no GUI to answer. It is now guarded on an interactive **stdin** (`[ -t 0 ]`, not stdout — for `curl | bash` fd 0 is the pipe) and skipped silently otherwise; when skipped, the success output prints the `cd … uvicorn` launch line instead of advertising the absent shortcut.
- **Windows zh-TW console no longer reports false errors during embedding.** `embed.py`'s per-file success marker used `✓` (U+2713), which raised `UnicodeEncodeError` on cp950 consoles and made every successfully-embedded file print as `[ERROR: ...]` — masking real failures. Replaced with an ASCII marker.

## v0.6.1 - 2026-05-28

### Fixed
- **Non-Mac (CUDA) ingest no longer breaks at transcription.** whisperx 3.8.5 moved the per-call ASR options (`beam_size` / `condition_on_previous_text` / `compression_ratio_threshold` / `log_prob_threshold` / `initial_prompt`) into `load_model(asr_options=…)` and now pulls torchcodec, whose DLLs fail to load on the Windows/CUDA box. The non-Mac backend now routes to **faster-whisper** (already a declared non-Mac dependency, no torchcodec), which still accepts those options per call — a clean drop-in for the whisper-guard layer config. Set `ARKIV_TRANSCRIBE_BACKEND=whisperx` to force the legacy path. The Apple-Silicon MLX path is unchanged.

> ⚠️ Verified on Mac with mocked `WhisperModel` unit tests only. **Live CUDA ingest→embed→chat on PC is still pending** before this is tagged/released — see `OVERNIGHT_RESUME.md`.

## v0.6.0 - 2026-05-28

### Added
- **Chat in the web UI.** A new "對話" button in the header opens an interim chat panel that calls `/api/chat`: conversation thread, intent badge, latency/token meta, and a scene-thumbnail strip whose clips open in the inspector. The canonical chat screen is being designed separately — this is a functional interim so the feature is usable today.

### Fixed
- **Web UI works in a plain browser again.** Since v0.4.1 every route required a Bearer token, but the browser frontend never sent one — so the web UI returned 401 on all data calls (and a fresh install was unusable in a browser). `auth.py` now trusts loopback (`127.0.0.1` / `::1`) as fully-scoped, so the local UI works with no token. Remote / fleet access still requires a token; set `ARKIV_TRUST_LOOPBACK=false` for reverse-proxied or network-exposed deployments.

### Docs
- README chat section now states the prerequisite: ingest **and** build the index (`python embed.py`) before chatting. `compilation` / `refinement` / `similarity` need the vector index; `analytics` needs ingested media; only `general` works on an empty library.

## v0.5.2 - 2026-05-28

### Changed
- **Chat needs only one model now.** `ARKIV_INTENT_MODEL` defaults to `ARKIV_CHAT_MODEL` (`qwen2.5:14b`), so a single `ollama pull` covers both intent classification and answers. Previously it defaulted to `qwen2.5:7b-instruct` — undocumented and frequently not installed, which silently broke chat. Override only if a smaller intent model is actually present.

### Added
- **`health.py` now checks the chat model** and warns with an `ollama pull …` hint when the configured chat (or a distinct intent) model is missing — previously health was blind to chat models.
- **README chat documentation (EN + zh-TW)** — model requirement, all five intents, `project_scope`, response shape, chat hardware floor, and an "embedding model is locked to your index" warning.

### Fixed
- **Missing chat model returns a clear message instead of HTTP 500.** `/api/chat` now catches the Ollama `HTTPError` and tells you to `ollama pull` the model.

## v0.5.1 - 2026-05-28

### Fixed
- **Chat `similarity` intent crashed with HTTP 500** on real ChromaDB data. `vectordb.find_similar()` used `ref.get("embeddings") or []`, which raises `ValueError: truth value of an array ... is ambiguous` because ChromaDB returns embeddings as NumPy arrays. Replaced with explicit `None` / `len()` checks. Mocked tests did not cover this path; caught by live verification against a real index.

## v0.5.0 - 2026-05-28

### Added — Chat: RAG over your video library
- **5-intent classifier** routes natural-language prompts to specialized handlers: `compilation`, `refinement`, `similarity`, `analytics`, and `general`.
- **Conversation memory** persists chat messages and scene IDs, then feeds recent history into follow-up prompts.
- **Project-scoped vector search** adds `project_scope` filtering to `vectordb.search()` and `vectordb.find_similar()`.
- **Chat API docs** in README EN / zh-TW describe intent examples and authenticated curl quickstarts.

### Hardening
- Chat dispatch now handles Ollama `Timeout` / `ConnectionError` without returning HTTP 500.
- Oversize prompts are trimmed before classification and handler execution.
- Invalid or empty classifier intents fall back to `general`, and classifier limits are capped at 100.

### Tests
- Expanded `tests/test_chat.py` to 17 cases covering timeout handling, prompt trimming, classifier fallback, project scope propagation, and compilation-to-refinement flow.

### Changed — LLM router abstraction
- **`llm.py` router** — centralized `chat` / `embed` / `vision` helpers with consistent request payloads, token counting, and provider metadata. `vision.py`, `vectordb.py`, and `transcribe.py` route Ollama calls through it while keeping existing module names and fallback hooks intact.
- **Model config** — `config.py` exposes `OLLAMA_CHAT_MODEL`, `OLLAMA_EMBED_MODEL`, and `OLLAMA_VISION_MODEL` (legacy `EMBED_MODEL` / `VISION_MODEL` aliases preserved).
- **Router coverage** — `tests/test_llm_router.py` adds schema, json-mode, token-clamping, and default-model checks.

## v0.4.1 (2026-05-27) — API Scope Token Auth

> **Security release.** All `/api/*` endpoints now require a Bearer token with the proper scope. See [API Authentication](README.md#api-authentication) for the bootstrap SOP.

### New Features
- **`arkiv_token.py` CLI** — `create` / `list` / `show` / `revoke` subcommands manage access tokens directly in local SQLite, with scope validation, CIDR allowlists, and optional expiry.
- **`tests/conftest.py` auth fixture** — `fastapi_client` now injects an admin bearer token so existing server tests continue to hit the real authenticated API without per-test boilerplate.
- **Auth coverage** — `tests/test_auth.py` adds CLI create/list/revoke, CIDR allowlist, and multi-scope token cases.

### Notes
- Scope-based tokens are designed for multi-machine fleets: read-only review stations can use `videos_read` or `media_read`, ingest boxes can use `ingest_write`, and admin boxes can manage tokens.
- Bootstrap remains first-run only: set `ARKIV_ADMIN_BOOTSTRAP_TOKEN`, start the server once, create per-machine tokens, then unset the env var and revoke the bootstrap token.

## v0.4.0 (2026-05-27) — W3 DIT three-pack complete

> **DIT companion ready.** Three new CLI tools bring arkiv from "AI metadata layer" to "DIT-grade companion": hash manifests interoperable with Silverstack / Hedge / MediaVerify, multi-destination resumable offload, and Resolve-friendly camera reports.

### New Features
- **ASC MHL v2 generation + verify** — `mhl.py create` / `mhl.py verify` CLI emits `urn:ASC:MHL:v2.0` manifests with `xxh3` / `md5` / `sha1` / `sha256` / `c4` hashing, directory + structure root hashes, and a chained `ascmhl_chain.xml`. Output lands at `<PROJECT_ROOT>/ascmhl/NNNN_<dir>_<timestamp>Z.mhl`. Round-trips through ASC reference impl 1.2 (`ascmhl info` reads emitted manifests, exit 0).
- **Multi-destination offload** — `offload.py --src <SD> --dst <dst1> --dst <dst2>` does chunked-read parallel copy with per-file hash verify, retry up to N times on hash mismatch, atomic `.partial → final` rename, sidecar-aware (Sony XAVC / ARRI / RED / iPhone Live Photo / generic). JSON state file at `offload-state.json` lets you kill mid-copy and resume — pending files pick up exactly where they stopped. Emits a per-dst MHL v2 manifest verifying every copied file (via `mhl.create_manifest`).
- **Camera report CSV + day summary** — `camera_report.py` writes a 20-column DIT-spec CSV (Reel / TC / Date / Camera / Lens / ISO / Shutter / Aperture / WB / FPS / Codec / Resolution / FileSize / Notes / ...) ready for Resolve's `File → Import Metadata from CSV`. Day-summary footer aggregates clip count / total runtime / by-camera / by-card.

### Internals
- `mhl.py` 723 lines, real ASC MHL v2 schema with `creatorinfo` (incl. `hostname`), `processinfo.process` enum (ingest/offload/verify), per-file `<hash>` + per-directory `<directoryhash>` with `<content>` + `<structure>` separation, `<roothash>` aggregate. `xxhash>=2.0.0` required for `xxh3` / `xxh64`.
- `offload.py` 416 lines + 5 pytest scenarios (2-dst copy, hash-mismatch retry, mid-copy resume, src-unmount cleanup, sidecar families).
- `camera_report.py` 501 lines + 2 pytest scenarios.
- `health.py` gains `_check_mount(path)` helper extracted from `project_health()` — single mount-precondition check reused by offload + ingest preflight.

### Driving incident
W3 milestone (per `references/plans/arkiv/2026-04-29-positioning-and-moat.md` §6): pre-W3, arkiv could only claim "AI metadata layer for DIT workflows" (soft). W3 ship locks in "DIT companion" (hard) — minimum bar to not get fact-checked on r/DaVinciResolve / r/cinematography.

---

## v0.3.1 (2026-05-25) — Per-Project Storage + Startup Preflight

> **⚠️ Breaking change** — default storage layout moves from `BASE_DIR/{media.db, thumbnails/, chroma_db/, proxies/}` to `BASE_DIR/.arkiv/{project.db, ...}`. One-shot migration provided. See [Upgrade SOP](docs/pipeline.md#upgrading-from-v030) before running.

### New Features
- **Phase 8.0c per-project storage** — `config.PROJECT_ROOT` is now the single source of truth. All 4 storage paths (DB, thumbnails, chroma, proxies) default to `PROJECT_ROOT/.arkiv/<xxx>`. Explicit `ARKIV_<X>_PATH` env vars still override per-path. Setting `ARKIV_PROJECT_ROOT=/path/to/your/footage/` produces a self-contained, portable archive next to the media.
- **Phase 8.0e startup health check** — new `health.preflight_paths()` runs before any pipeline work. Catches: dangling symlinks, unwritable storage, NAS mount precondition (`/Volumes/` paths), stale `PROJECT_ROOT` (sample DB resolve fails). Returns `(ok, errors)` for embedding in other entry points. `ingest.py main()` calls it pre-warm-up; fail → `sys.exit(4)`.
- **Phase 8.0f NAS-unavailable degradation** — preflight short-circuits with explicit "NAS not mounted" message + expected path when `PROJECT_ROOT` lives on `/Volumes/` but mount root is gone. Avoids the "process N files with the same root error" failure mode.
- **`--migrate-storage`** — one-shot migration from legacy `BASE_DIR/xxx` to new `BASE_DIR/.arkiv/xxx` layout. Backup-first (`.legacy-backup-{ts}.tar.gz`), idempotent, cross-checks `sqlite COUNT` + thumbnails file count post-move, cleans dangling symlinks left over from pre-8.0c workarounds.
- **`docs/pipeline.md`** + **`docs/pipeline.zh-TW.md`** — complete pipeline reference (4 stages, paths, exit codes, maintenance modes, upgrade SOP) in EN + zh-TW.

### Bug Fixes — Silent failure suite
- **Exit code reflects fail state** — `ingest.py main()` now exits non-zero on any failure: `2` if all files failed, `1` on partial fail, `3` on dangling symlink, `4` on preflight fail. Before this, `main()` always exited 0 even with 222/222 file failures, hiding regressions from launchd/cron runners.
- **`frames.py` dangling symlink defense** — new `_ensure_thumbnails_dir()` helper fails fast (exit 3) instead of letting `Path.mkdir(exist_ok=True)` raise `FileExistsError` on every file. Last line of defense behind preflight.
- **`frames.py` strict ffmpeg success check** — new `_run_ffmpeg()` enforces both `returncode == 0` AND non-zero-size output file. Catches "ffmpeg exits 0 but writes 0-byte file" edge case that would otherwise register empty frames as valid.
- **Phase 2 honest skip messages** — when vision phase starts with empty queue, message now distinguishes "phase 1 had X/Y failures" vs "all already indexed" vs "genuinely no new files". Replaces single misleading "No new files to run vision on".
- **Phase 2 halt-on-3-consecutive-fail** — symmetric with existing `still_failed` halt for both-models-fail. Avoids burning through every remaining file writing the same Ollama-disconnect / model-crash error.

### Bug Fixes — Other
- **`--dir` argparse fix** — maintenance modes (`--migrate-storage` / `--migrate-relative` / `--regenerate-proxies` / `--vision-only`) no longer require `--dir`. Long-standing usability bug (running `ingest.py --migrate-relative` previously failed with argparse error).

### Internals
- `health.preflight_paths()` returns `(bool, list[str])` for programmatic embedding (server startup, smoke tests, CI gates).
- Migration backup format = single `.tar.gz` per timestamp; rollback is `rm -rf .arkiv/ && tar xzf <backup>`.

### Documentation
- `docs/pipeline.md` + `docs/pipeline.zh-TW.md` ship with full 4-stage flow, storage layout diagram, exit code table, maintenance mode reference, upgrade SOP with rollback, and per-project layout example.
- README (EN + zh-TW) Architecture section links to the new pipeline doc + adds "Upgrading from v0.3.0 → v0.3.1" mini section.

### Driving incident
Codified after 2026-05-25 overnight ingest failure: 222 video files, 222/222 phase-1 fail on dangling thumbnails symlink (5/15 NAS workaround for `server.py:70` hardcode, since fixed but symlink never cleaned), exit code 0 hid the regression. Root-cause-of-root-cause was Phase 8.0c never wiring config defaults despite roadmap BEFORE/AFTER table specifying it.

---

## v0.3.0 (2026-05-22) — DIT Companion

### New Features
- **DaVinci Resolve metadata CSV export** — `/api/export/metadata-csv` endpoint + toolbar button + plugin auto-prompt. Drop-in for Resolve's `File → Import Metadata from CSV` workflow (Phase 7.6b/c/d/e)
- **ExifTool full integration** — Sony XAVC sidecar `.XML` parsing, iPhone Keys group fallback, Blackmagic Cam app per-vendor lens tags (`Blackmagic-designCameraLensType`). LensModel chain: ExifTool LensModel → BMD vendor tag
- **ExifTool auto-detect** — `config._detect_exiftool()` fallback chain (env var → `shutil.which` → Windows winget LOCALAPPDATA / Program Files / chocolatey / scoop / macOS homebrew / Linux apt + `~/.local/bin` + Strawberry Perl). Solves silent skip on fresh Windows clone
- **HEVC/ProRes browser proxy** — `/api/proxy/build/{id}` POST endpoint + 409 surface in frontend + "build proxy" button (Phase 7.7g)
- **Tauri panic hook** — surfaces Rust crashes to stderr (Windows dialog crash diagnosis)

### Bug Fixes
- **EDL reel name (B10)** — was `stem[:8]` unconditionally. Now uses ExifTool ReelName when present, falls back to filename stem; sanitizes control chars; pads/truncates to 8-char CMX3600 spec
- **EDL reel injection hotfix (B10-hotfix)** — control char (`\r\n` etc.) stripped before encode; whitespace-only reel falls back to stem (Codex audit)
- **Tauri dialog crash (Windows)** — `rfd` folder picker crash workaround: drop `title` arg
- **Vision Ollama timeout** — bump 120s → 300s for large prompts
- **mlx-whisper backend** — drop unsupported `beam_size` kwarg

### Security
- Codex Round 1+2 audit: 5 SSRF / path-bound hardenings (Batch J), export-to dest allowlist (blocks `~/.ssh` / LaunchAgents RCE), CSV formula injection prevention, XSS hardening, vectordb `build_doc_text` production schema alignment

### Internals
- Cross-platform `detect_gpu()` for `bench_ingest.json`
- `arkiv_resolve.py` honors `ARKIV_API` / `ARKIV_HOST` / `ARKIV_PORT` env
- 6+ new tests (ExifTool fallback chain + reel name regressions + CSV scope + BMD lens + ExifTool sidecar)

### Known Issues
- Resolve conform pathname pattern `*/%R/%D` mis-parses paths like `iphone 16pro` as reel — workaround: set conform pattern to filename-based, or use FCPXML import (B10c-resolve, open)

---

## v0.2.1 (2026-04-12)

### Performance
- **Vision O2+O6 代表幀策略** — 每支影片只對代表幀做完整 12 欄分析，其餘用 LIGHT_PROMPT 11 欄 + 只繼承 edit_reason。未來 ingest 預估省 50% vision 時間
- **廢幀過濾（O6）** — PIL+numpy 偵測全黑/全白/嚴重模糊幀，跳過 LLM 推理
- **SQLite WAL mode** — 啟用 Write-Ahead Logging，允許讀寫併發

### Bug Fixes
- **DB self-deadlock** — `_run_vision_only` 內巢狀 `get_conn()` 導致自鎖。`add_tag`/`delete_frames`/`upsert_frame` 新增 `_conn` 參數，所有 ingest 呼叫改用同一 connection
- **sqlite3.Row immutable** — `--vision-only` 模式傳入 Row 物件給需要可寫 dict 的函式，改為 `dict()` 轉換
- **Vision 冷啟動失敗** — 新增 `_warm_up_vision_model()` 發送 dummy request 確認模型已載入 VRAM

### Tests
- 新增 4 測試：`_conn` 參數 ×3 + vision-only 整合流程 ×1（39 → 43 tests）

### Data-Driven Insight
- 用 427 支 / 1,844 幀實測驗證 O2 繼承假設：content_type 一致率僅 23%、atmosphere 11%、edit_reason 3%。原始 5 欄繼承方案推翻，改為只繼承 edit_reason（D+ 方案）

---

## v0.2.0 (2026-04-09)

### New Features
- **WhisperX 整合** — word-level timestamps + `words_json` / `remotion-props` 支援
- **Phase 7.6 Tag→Keyword 映射** — 標籤寫入 DaVinci Keywords + Comments，支援 Smart Bin 篩選
- **Phase 7.7 Browser Proxy** — HEVC/ProRes .mov 透過 FFmpeg 產生 H.264 代理，瀏覽器直接播放
- **Vision 錯誤處理強化** — 兩階段 fallback（主模型 → 備援模型）+ 前端 Retry UI
- **Silero VAD 前處理** — 語音活動偵測，提升轉錄品質
- **FCPXML 匯出** — Final Cut Pro / DaVinci Resolve 時間線匯出
- **Clip Color 分級** — 匯入時依評級自動上色（GOOD=Green, NG=Orange, Review=Yellow）
- **Ingest 兩階段 Pipeline** — Phase 1 轉錄 + Phase 2 視覺分析，分離 VRAM 使用
- **pytest 測試框架** — 23 tests 覆蓋 DB CRUD、API endpoints、轉錄 guard、向量搜尋

### Localization
- **繁體中文化** — Web UI、DaVinci Plugin、API 錯誤訊息全面中文化

### Improvements
- EDL reel name 改用檔名 stem
- Markers 改為 clip marker（非 timeline marker）
- Health check 動態讀取 vision model
- Docker Compose 移除廢棄 version 欄位
- LLaVA retry on empty/garbage response + dynamic ingest timeout

### Bug Fixes
- FCPXML spec compliance 修正
- NG clip color 改為 Orange（Red 在 DaVinci 不可見）
- Auto/manual tag legend 改用 inline color（替代 Tailwind class）
- Stream endpoint 使用 ROOT 路徑
- XSS / CORS / path validation 安全修復

### Breaking Changes
- 移除 EDL+ markers export（FPS 限制 + 不支援 CJK）

---

## v0.1.0 (2026-03-31)

Initial release — Local Media Asset Manager MVP
- Ingest pipeline (FFprobe + Whisper + LLaVA vision)
- Web UI with grid/list view, rating, tagging
- DaVinci Resolve plugin
- SRT/VTT/EDL export
- ChromaDB semantic search
- Tauri desktop app
- Docker deployment
