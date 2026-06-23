# Changelog
## Unreleased

### Added
- **VTT subtitle export in the inspector (Phase 9.7 G3).** The export row now offers VTT alongside EDL / FCPXML / SRT (the backend already produced WebVTT — the button was just missing). Honours the IN/OUT trim window like the other subtitle exports.
- **Tag-source breakdown in the inspector (Phase 9.7 G8).** The Tags section header shows `N AUTO · N MANUAL`, surfacing how many tags came from vision vs were hand-added — matching the design SSOT. Reads the existing `tags.source` field; no schema change.
- **Correction-dictionary editor in Settings (Phase 9.6c).** Settings → **Vocabulary**: edit the per-project dictionary (rule table `from → to · scope · pre · post`), then batch-recorrect from the same panel — `預覽命中` (dry-run) → `套用校正` (gated on a preview with hits) with an optional vector-rebuild, plus `還原最近一次`. The dictionary is the unified vocab UI — its `pre` toggle is the hotword function, so no separate `vocabulary.txt` editor is needed.
- **Project-wide batch retranscribe (Phase 9.6d, the 2a upgrade path).** `POST /api/retranscribe-all` re-runs Whisper across every audio clip so newly-added hotwords / correction-dictionary pre-terms take effect — for the rare case a term was mis-heard so badly that find→replace can't recover it. Single-flight + `GET /api/retranscribe-all/status` progress poll (mirrors the embed-rebuild pattern); snapshots all transcripts to the shared correction-backups first, so the existing revert restores them. Preserves the single-clip guard (never blanks a good transcript on an empty/failed decode). Wired into Settings → Vocabulary as a clearly-marked heavy "升級" action with live progress. 6 tests in `tests/test_retranscribe_all.py`.
- **Per-project correction dictionary + batch recorrect (Phase 9.6b).** A `.arkiv/corrections.json` dictionary of `{from, to, scope, pre, post}` rules drives two paths from one source: `pre` terms feed the Whisper hotword list (`initial_prompt`, hot-read like `vocabulary.txt`), and `post` rules batch-rewrite **already-stored** transcripts — fixing the whole backlog's search recall in seconds without re-running audio. Endpoints: `GET/PUT /api/corrections`, `POST /api/recorrect` (defaults to dry-run preview — writes nothing; `dry_run=0` applies, `rebuild=1` chains the embedding rebuild), `GET /api/recorrect/backups`, `POST /api/recorrect/revert`. CLI: `python recorrect.py --dry-run | --apply [--rebuild] | --revert [NAME]`. **RP-4 safe**: preview-first, and every apply writes a timestamped backup restorable via revert. The recorrect **syncs `segments_json` alongside the transcript blob** (else search/SRT drift), and `words_json` gets whole-token renames. Scope `word` guards against bleeding into a longer token (a `松→鬆` rule never touches `馬拉松`). Operates on the active project. 16 tests in `tests/test_corrections.py`; full suite 608 passed / 5 skipped.

### Changed
- **Default vision model is now `qwen2.5vl:7b` (was `qwen3-vl:8b`).** Qwen3-VL's vision path (DeepStack / interleaved-MRoPE) runs ~10x slower than Qwen2.5-VL under Ollama and often offloads the vision encoder to CPU — measured **~60s/frame vs ~8s/frame on an M2 Max** for identical frames (Ollama issues #12854 / #12882 / #14548). At ~2000 frames that's ~30h vs ~3.5h, at comparable tag quality (spot-checked). Override with `ARKIV_OLLAMA_VISION_MODEL=qwen3-vl:8b` for the higher ceiling once the Ollama regression is fixed.

### Fixed
- **Vision fallback no longer 404s on every fresh install.** The Phase-2 fallback model was hardcoded — and inconsistently (`ingest.py` used `minicpm-v`, `server.py` used `moondream2`) — while `install.sh` pulled neither, so the issue-#48 resilience path silently 404'd per failed frame and left those frames empty. Now: a single configurable `ARKIV_OLLAMA_VISION_FALLBACK_MODEL` (default `minicpm-v:latest`) used by both paths, pulled by `install.sh`, and **skipped gracefully** (logged once, frame left for a later `--vision-only` retry) when the model isn't installed instead of erroring per frame.
- **`install.sh` pulls the right models** for the new defaults: `qwen2.5vl:7b` (vision) + `minicpm-v` (fallback), alongside `bge-m3` + `qwen2.5:14b`.

## v0.9.2 - 2026-06-17

### Added
- **New Svelte UI is now the default at `/`; the old Tailwind UI moves to `/legacy` (cutover Phase 1+2, #78).** `server.py` serves the built SPA from `frontend/dist` with a `/assets` mount; it auto-falls back to the legacy page when no build is present, and `ARKIV_UI=legacy` forces the old page. `install.sh` and the `Dockerfile` (new `node:20` build stage) now run `npm run build`, so a fresh clone / image ships the new UI. The old UI stays reachable at `/legacy` as an escape hatch during the cutover bake. Hash-routed SPA → no server-side fallback needed.
- **Tier 1 backend-parity features wired into the new UI (#78).** Inline tag editing (add/remove); per-clip re-processing (retranscribe / retry-vision / reingest); editing-proxy build + status (whole-library and per-clip); DaVinci Resolve metadata CSV export (whole-library and current selection); and a chat-history sidebar that lists past conversations and restores their threads.

### Notes
- Tier 2 backend capabilities are not yet wired into the new UI (in/out position save, chapters, export-to-path, remotion-props, open-file, project federation, pool view, admin tokens, cache ops, analytics breakdowns). They remain reachable via the API and the `/legacy` UI; tracked in #78.

### Performance
- **DIT `--organize` / preview now probe metadata in one ExifTool spawn, not one per file.** `_probe_camera_meta` spawned ExifTool per file, so a 400-clip card preview/offload paid ~400 process spawns (tens of seconds of pure spawn overhead). Extracted `_exiftool_batch()` (a single `exiftool -json …file1 …file2 …fileN` call keyed by `SourceFile`) + `_probe_camera_meta_batch()`; `preview_layout()` and `_ensure_file_records()` batch-probe up the whole file list once. The Sony XAVC sidecar / mtime fallbacks still run per file (cheap, no subprocess). The single-file `_probe_camera_meta` (card-watch) is unchanged in behaviour.

### Added
- **DIT Offload UI (`/dit`).** A working control panel for the DIT engine: type a source card, destination(s), and an `--organize` template, hit **Preview** to see the exact source→destination layout (read-only, no copy), then **Run** to offload (copy + hash-verify + ascMHL, never deletes the source). New endpoints `POST /api/offload/preview` (pure layout via `offload.preview_layout()`) and `POST /api/offload` (subprocess, mirrors `/api/ingest`; state file scoped to the project's `.arkiv`, not the install dir); page served at `GET /dit`. The run **streams live per-file progress** — `offload.py --progress json` emits single-line `dst_start`/`file`/`done` events and `/api/offload` relays them as ndjson via a `StreamingResponse`, so the UI shows a progress bar + current-file count instead of blocking on one giant request. Verified end-to-end (preview layout + streamed copy + MHL manifest). Tests in `tests/test_dit_offload_ui.py` + `tests/test_offload_organize.py`.
- **DIT card-watcher (`offload.py --watch`).** Waits for a camera card to mount, then auto-offloads it — copy + hash-verify + MHL, **never deletes the source** — with the `--organize` naming policy. A card is a newly-mounted volume with a `DCIM/` folder or media files; only NEW inserts trigger (already-plugged disks at startup are the baseline and ignored), and a removed→reinserted card re-triggers. One failed offload is logged and the watch loop survives. The "insert → it just copies" half of the Gate replacement, paired with the `--organize` folder policy. Requires at least one `--dst`. Tests in `tests/test_offload_card_watch.py`.

### Fixed
- **`--refresh` now actually re-extracts thumbnails + frames (issue #53).** It used to re-run vision/embed but reuse cached frame thumbnails (the `already_ok` check skipped extraction whenever a file existed), so a change to the extraction logic itself — e.g. the Phase 8.3b 360 reproject — never re-applied to already-ingested clips; they kept their stale raw-fisheye frames. `--refresh` now threads `force=True` down to frame/thumbnail extraction. Re-extraction is atomic (ffmpeg writes a temp sibling, `os.replace` onto the canonical file only on success), so a forced re-extract that times out / fails can't destroy the prior good thumbnail while the DB still points at it. Non-refresh ingest still reuses existing thumbnails (cheap re-ingest, unchanged).

### Added
- **360 footage is reprojected to equirectangular before vision (Phase 8.3b).** Dual-fisheye `.insv` / `.360` (Insta360 / GoPro Max) now get both lenses stitched into a full equirectangular panorama (`hstack` → `v360=dfisheye:equirect`) before frame extraction, so the vision model sees an undistorted scene. The raw fisheye buries the wearer and on-screen text (event name, bib numbers) in the distorted edge — vision reads nothing useful; the equirect stitch surfaces them (verified end-to-end: the same clip went from "a group in blue shirts" to reading "POCARI SWEAT RUN" + bib number off the reprojected frame). Detection is ext-gated + confirmed by a two-video-stream ffprobe (a single-lens file mislabeled `.360` won't try to stitch a missing stream); 360 frames extract at 1024px (vs the normal 320px) so reprojected text survives for the VLM. Audio/transcript is projection-agnostic and unchanged. Tests in `tests/test_frames_360.py`; design in `references/plans/arkiv/2026-06-13-arkiv-8.3a-360-indexing-poc.md`.
- **DIT naming / folder policy (`offload.py --organize TEMPLATE`).** Lay card files into camera-metadata folders instead of mirroring the card — the thing Gate's folder logic got wrong. Folder template with `{date}/{camera}/{reel}/{stem}/{ext}` tokens (original filename always appended), e.g. `--organize "{date}/{camera}/{reel}"`. Metadata via ExifTool + a Sony XAVC NRT sidecar fallback (so FX30/FX-series get make/model even when the `.mp4` carries none); missing values resolve to `UNKNOWN`, the date falls back to file mtime. Token values are sanitized to one filesystem-safe segment (a `Sony/FX30` can't spawn a nested directory; no path traversal), and two source files mapping to the same destination are **refused** (a DIT tool must never silently overwrite). Pair with `--dry-run` to preview the layout. The default (no `--organize`) still mirrors the source exactly. Tests in `tests/test_offload_organize.py`. (Follow-up: ExifTool currently spawns per file — batchable for large cards.)
- **Vision frame-failure tolerance (`--max-failures` / `--skip-failed`, issue #48).** The vision phase used to halt the entire run on the first frame that failed both the primary and fallback model — fine interactively, but a single transient Ollama hiccup killed a 481-frame overnight run. `--max-failures N` tolerates N cumulative failed frames before halting (N=0, the default, keeps the historical zero-tolerance behaviour). `--skip-failed` never halts on individual frame failures — it leaves them with an empty description (so a later `--vision-only` retries exactly those frames) and prints a report at the end. A consecutive-failure guard (whole files producing nothing) still halts fast under either flag, so a real Ollama outage can't spin all night. Successful frames are now committed even on a file that later triggers a halt (no lost work). `--vision-only` and the main Phase-2 loop share one describe→fallback→tolerance path. Tests in `tests/test_vision_tolerance.py`.

## v0.8.1 - 2026-06-12

### Added
- **360-camera formats (`.insv`, `.360`) are now first-class video.** Insta360 `.insv` and GoPro Max `.360` are HEVC-in-MOV/MP4 — ffmpeg probes them and extracts frames fine, but they were absent from the ingest whitelist, so `is_video` was False and they silently skipped thumbnail/frames/vision. Added to `VIDEO_EXT`/`SUPPORTED` (ingest), `_VIDEO_EXTS`/`MEDIA_EXTS` (server), `watch.py`, and the UI format lists. Verified on a real `.insv` (dual 2880×2880 HEVC fisheye + AAC; thumbnail decodes). `.insp` (Insta360 stills) not included — no image pipeline. Tests in `tests/test_ingest_microtasks.py`.

### Tests
- **Pinned 3 Codex-flagged edges as resolved** (H5 abs/rel row merge, scene_ids dangling reference, M24 codec backfill freshness). All were already fixed in the v0.8.0 sprint; regression tests in `tests/test_v081_edges.py` lock the behaviour — frames+tags merge to the survivor with no orphans + FK integrity (incl. the frame_index collision branch); a dangling `scene_ids_json` id drops gracefully (`WHERE id IN` + `find_similar` return `[]`); stored codec skips re-probe, NULL probes once + backfills, probe failure falls through without crashing playback.

### Docs
- README (EN + zh-TW): documented 360 format support and that camera identity is read from embedded EXIF **and** the Sony XAVC NRT sidecar XML (so FX30 / FX-series footage keeps make/model/lens/timecode that consumer DAMs drop).

## v0.8.0 - 2026-06-12

### Security
- **Stored/reflected XSS in the web UI closed.** `esc()` only escaped `&<>` (the textContent trick), leaving `'`/`"` raw — so LLM-generated tag names / filenames interpolated into HTML attributes and inline `onclick` strings were injectable, and the vision-LLM frame description was written into `innerHTML` unescaped (stored XSS via on-screen text in footage). `esc()` is now quote-safe, a dedicated `escJsAttr()` guards values placed in inline-handler JS strings, and the frame description, tag handlers, autocomplete row, and search-query title are all escaped.
- **`/api/media` no longer turns every authenticated read into a DB write.** Token resolution updated `last_used_at` on every request, so a `GET` competed for the SQLite write lock during concurrent ingest ("database is locked"). The write is now throttled to once per 60s per token.
- **`uninstall.sh` refuses to `rm -rf` a non-arkiv directory.** `ARKIV_DIR` came straight from the environment; a stray value (`$HOME`, a footage volume) would wipe an unrelated tree. It now requires the install markers (`server.py` + `config.py`) before removing anything.

### Added
- **Custom-vocabulary file for transcription hotwords.** Beyond the comma-separated `ARKIV_CUSTOM_VOCABULARY` env, you can now keep a newline-delimited wordlist (one term per line, `#` comments) — `ARKIV_VOCABULARY_FILE`, defaulting to `.arkiv/vocabulary.txt` when present. Terms (names, places, product jargon) merge with the env list, dedup, and feed Whisper's `initial_prompt` — the persistent-wordlist workflow FatSub validated with editors. Tests in `tests/test_transcribe_faster_whisper.py`.
- **Chapter-marker export (`export.py chapters <id>`).** Turns a clip's sampled
  scene frames (scene-change timestamps for long clips) into chapter markers, with
  titles auto-generated from each frame's vision description (first sentence,
  truncated). Two formats: `--format youtube` emits `MM:SS Title` lines (first
  marker forced to `0:00` per YouTube's rule) for video descriptions; `--format
  ffmetadata` emits an ffmpeg metadata file to embed real chapters (`ffmpeg -i in
  -i chapters.txt -map_metadata 1 -codec copy out`). Borrowed from the FatSub
  ProChapter benchmark; reuses existing scene segmentation, no new deps. Also
  exposed over HTTP: `GET /api/media/{id}/chapters?format=youtube|ffmetadata`
  (scope `media_read`) returns `{media_id, format, chapters, count}`.

### Fixed
- **`probe()` no longer swallows ffprobe failures, hangs, or lets one bad clip poison a batch.** It used `-v quiet` with no timeout and returned `None` on any non-zero exit — so a mid-ingest failure printed only `[ffprobe failed]` with no cause, and a transient subprocess-spawn failure (e.g. Windows handle exhaustion after a heavy clip) could fail every subsequent clip. Now: `-v error` surfaces the real stderr, a 120s timeout bounds hangs, the actual rc/stderr/exception is printed, and one retry (2s back-off) recovers transient spawn/resource pressure. Tests in `tests/test_ingest.py`.
- **Headless / SSH ingest on Windows no longer dies at the first `ffprobe` ([WinError 448]).** arkiv invoked `ffmpeg`/`ffprobe` by bare name, so on a non-interactive Windows session (headless service, SSH) they resolved to the WinGet `Links` App Execution Alias — a reparse-point shim that raises `[WinError 448]` outside an interactive token, crashing `probe()` before any clip is processed. ffmpeg/ffprobe are now resolved once via `config.FFMPEG_PATH` / `config.FFPROBE_PATH`: `ARKIV_FFMPEG_PATH` / `ARKIV_FFPROBE_PATH` env > `shutil.which()` (skipping alias shims) > common install paths (Gyan winget full build, choco, scoop, Homebrew, `/usr/bin`) > literal name. macOS/Linux behavior is unchanged (`which` returns a real path). New tests in `tests/test_config.py`.
- **`UnicodeDecodeError` on Windows zh-TW (cp950) during proxy generation / codec probe.** `subprocess.run(..., text=True)` without an explicit encoding decoded ffmpeg/ffprobe output with the cp950 locale codec and crashed on utf-8 bytes. Proxy gen (`ingest.py`) and codec probe (`codec.py`) now pin `encoding="utf-8", errors="replace"`.

#### Audit batch 2026-06-10 (5-agent review + Codex verification)
- **Thumbnails no longer cross-contaminate between same-named clips.** Camera cards reuse filenames (`C0001.MP4`, `GX010001.MP4`); with `--recursive` over several cards, the second clip found the first's thumbnail already on disk and reused it — so its vision tags / editability score were computed from the wrong frames and written to its DB row. Thumbnails are now keyed by `{stem}_{sha1(abs_path)[:10]}`, the same path-hash guard proxies already had.
- **`Phase 3` no longer re-runs ffprobe over the entire library every ingest** (the mid-run ffprobe storm behind the stalled large-batch runs). The detected codec is persisted in `media.codec` at ingest time and reused; only legacy NULL-codec rows probe once, then persist. `watch.py`'s per-file process spawn no longer triggers a full-library probe per new clip.
- **A mid-run crash no longer leaves "processed but unsearchable" records.** The media row, its auto-tags, and its frame rows are written in one transaction (so a crash can't leave a frame-less row that `is_processed` skips forever); embedding now runs even when `ok == 0` to reconcile rows a prior crashed run left un-embedded, force re-embeds `--refresh`'d rows, and prunes ChromaDB entries whose SQLite row is gone; `upsert_record` deletes a media's old chunks before re-inserting (no orphan chunks from a shrunk transcript).
- **`--refresh` no longer wipes `frame_tags` / `thumbnail_path` when vision or thumbnail extraction fails.** Those fields are omitted from the refresh upsert instead of being set to `None`, so the prior searchable values survive a failed re-vision.
- **Vision halt now exits non-zero.** A halt on the first file left `vision_fail = 0` → implicit exit 0, hiding a fully-unindexed library from cron/watch; the halt now counts as a failure and `--vision-only` propagates the halt as exit 1.
- **`/api/media?q=…` honors `rating` / `lang` with semantic search.** The filter ran against the raw vector hit (which carries no `rating`), dropping every semantic result under `rating=good` and silently falling back to an unfiltered SQL `LIKE`. It now filters the enriched record, and the SQL fallback applies the same filter.
- **Chat is hardened against malformed/injected model output.** Refinement intersects model-returned scene ids with the prior set (rejecting hallucinated / transcript-injected ids before they're persisted); analytics validates `time_range` as `YYYY-MM` and `dispatch` has a catch-all so a bad model response returns a clean error turn instead of a 500; the intent limit is clamped `>= 1`; intent is classified once per request instead of twice.
- **WebSocket ingest no longer deadlocks or runs unbounded.** `stderr` is merged into the read stream (an unread `stderr=PIPE` deadlocked once its 64 KB buffer filled), the background task is held with a crash-logging callback, a 409 guard serializes concurrent ingests, the progress regex handles filenames with spaces, and the completion message reports the real failed-count and exit code.
- **"Export GOOD as EDL" exports all good clips**, fetched across the whole library and sequenced via the batch timeline endpoint, instead of only the first good clip on the current grid page.
- **`federation` search timeout actually bounds wall-clock.** `search_all_projects` waited on `shutdown(wait=True)` even after a per-future timeout, so a worker hung on a stale mount hung the whole request; it now uses a shared deadline and abandons stragglers.
- **Installer fixes:** the venv is built from the freshly-installed `python@3.12` binary (not the rejected `python3`), and Ollama pulls match `config.py` defaults (`bge-m3` / `qwen3-vl:8b` / `qwen2.5:14b`) — the old `nomic-embed-text` pull left a fresh install's semantic search silently degraded.

### Changed
- **`media.codec` column added** (additive, backward-compatible migration) to persist the ffprobe codec for proxy decisions.
- **Dropped the unused `pandas` dependency** (~70 MB, imported nowhere) and removed the orphaned, broken `vision_refresh.py` (superseded by `ingest.py --vision-only`). `chromadb` and `pydantic` now carry upper version bounds.

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
