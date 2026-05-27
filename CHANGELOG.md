# Changelog

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
