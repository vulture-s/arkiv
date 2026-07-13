# arkiv Round-5 Review — Data Integrity / Performance / Frontend / Architecture

**fable self-check · 2026-07-13 · complement to round 4 (security / CI gates / concurrency, PRs #129–#132)**

> **Method.** 9 fable-model auditors fanned across four dimensions (data-integrity, performance, frontend/UX, architecture) → barrier dedupe → adversarial skeptics (2 votes for critical/high, 1 for medium/low, each prompted to *refute*) → ranked synthesis. 66 raw → 62 unique → **59 CONFIRMED**, 0 PLAUSIBLE, 3 REFUTED. A second independent **Codex** pass cross-validated the 8 highest-stakes data-loss findings (§ Codex cross-validation) per the "run BOTH on production-bound audits" discipline.
>
> **Line-number caveat.** Anchors were captured against the PR7 (`feat/hardening-state-extraction`) worktree. On `main`, `server.py` line numbers below the removed slot block (server.py:37–116) sit ~40 lines higher, and the ingest slot lives in `server.py`, not `state.py`. Treat line numbers as approximate; each fix PR re-locates the exact site.

59 findings CONFIRMED after adversarial verification (severities post-skeptic-corrected): **1 critical, 16 high, 37 medium, 5 low**. By dimension: data-integrity 17 (1C/11H/5M), performance 14 (1H/11M/2L), frontend 15 (2H/11M/2L), architecture 13 (2H/10M/1L). The dominant themes: derived artifacts written non-atomically and gated on bare `exists()` (#1/#2/#59); transcript work destroyed by its own repair/rollback paths (#7/#14/#17); the H5/H8/B3/B11 fix patterns existing in one module while their siblings kept the defect; and a router split (#51) that is safe only if four prerequisite refactors land first.

## Confirmed findings (ranked)

| Rank | Dim | Sev | Finding | file:line | Failure scenario (short) | Fix |
|---|---|---|---|---|---|---|
| 1 | data | **critical** | #3 Bin copy-into-project silently overwrites same-basename clips; verified-copy reports success | server.py:691 | C0001.MP4 from two projects → second `os.replace` destroys first's bytes; `copied: 2, skipped: []`; source wiped trusting the copy → clip gone | Refuse clobber in `_copy_clip_verified`; per-run `name.casefold()` set; surface renamed/skipped |
| 2 | data | high | #7 migrate_to_relative merge cascade-deletes the merged row's transcript archive | db.py:427 | H5 merge moves frames/tags only; `DELETE FROM media` cascades into `transcripts` (FK ON); zh+en archives vanish silently (empirically reproduced) | `UPDATE OR IGNORE transcripts SET media_id=?` + delete leftovers before the media DELETE |
| 3 | data | high | #17 Same-language single-clip retranscribe destroys previous transcript, zero backup | server.py:2525 | Hand-corrected zh transcript replaced by degraded non-empty Whisper output in media.* AND the zh archive row, same transaction; unrecoverable | Write corrections backup (`_write_backup`) before overwrite when `active_lang == rec['lang']`; return backup name |
| 4 | data | high | #14 retranscribe-all backup omits `lang`; revert leaves text/lang mismatch that self-propagates | server.py:4205 | zh library → batch en → revert: zh text with lang='en'; lazy backfill + next G2 archive cross-contaminate the per-language archive | Include lang in backup SELECT; revert restores lang (COALESCE); batch archives outgoing transcript |
| 5 | data | high | #18 ascMHL manifests stale `.partial` files as verified content | mhl.py:268 | Killed offload leaves truncated `.partial`; next card's manifest hashes it in; verify passes forever on a truncated clip | `.partial` exclusion in `_should_ignore`; sweep stale partials per destination at run start |
| 6 | data | high | #1 generate_proxy writes ffmpeg output straight to final path — kill mid-encode = permanently accepted corrupt proxy | ingest.py:520 | Power loss/SIGKILL/Ctrl-C mid-encode → truncated mp4; every consumer (Phase 3, /api/stream, both /api/proxy/build) treats existence as done; no self-heal | Mirror frames.py `_extract_to`: tmp.<pid> + `os.replace` after rc==0 + size check |
| 7 | data | high | #2 /api/ingest and reingest use plain `subprocess.run(timeout=)` — the H8 defect watch.py already fixed | server.py:2325 | 1800s cap kills ingest.py only; orphan ffmpeg keeps writing the final-path proxy with NO timeout; slot released; retry accepts mid-write proxy as done | Extract watch.py `run_tree()` (start_new_session+killpg / taskkill /T) into shared helper; use in both routes |
| 8 | data | high | #16 Default cache-clear deletes DB-referenced thumbnails AND vision frame images | server.py:2828 | One-click '清 app 快取' → whole-library broken grid + dead frame strips + failing vision-patch; frame images need hours-long `--refresh` | Remove thumbnails from 'app' composite; explicit target + warning; NULL dangling paths / skip referenced `*_frame*.jpg` |
| 9 | data | high | #15 chromadb clear rmtree's under the cached client — search serves the deleted index; rebuild invisible until restart | server.py:2847 | Clear → rebuild → "verify by searching" shows old deleted index all session (empirically reproduced on chromadb 1.5.9) | `SharedSystemClient.clear_system_cache()` after rmtree; 409 while `_embed_rebuild_active`; drop `ignore_errors` |
| 10 | data | high | #8 Dedup is path-only; `file_hash` never populated — rename/reorg re-ingests library as duplicates | ingest.py:1522 | Folder reorg → hours of re-Whisper/vision; old rows keep ratings/tags but point at dead paths; stats double-count | Populate xxh3 at ingest; on unknown path, hash-match → `UPDATE path` (re-point) instead of INSERT; fix bogus 'xxh3-128' default |
| 11 | data | high | #9 Legacy backslash-relative rows invisible to dedup AND unfixable by migrate_to_relative | db.py:454 | NAS DB written by pre-fix Windows ingest, opened on Mac → whole-library duplication with split metadata; migration reports success, fixes nothing | Test backslash form in `_is_known`/`is_processed`; treat relative backslash paths as dirty in migration |
| 12 | data | high | #4 `--vision-only` rebuilds media.frame_tags from ONLY previously-failed frames | ingest.py:886 | Documented recovery path erases 4 good frames' text from embed/tag/classify/export source; editability_score can drop | Rebuild blob from ALL frames rows; `max(existing, new)` for score (Phase 2 M2 pattern) |
| 13 | data | med | #10 Date range excludes the entire end day (ISO timestamp vs date-only bound) | query_builder.py:125 | `['2026-07-06','2026-07-12']` misses everything ingested on the 12th; single-day range always empty | Day-ceiling / exclusive `< date+1` (chat.py already does this correctly) |
| 14 | data | med | #11 Tag `eq` skips the lowercase normalization add_tag applies on write | query_builder.py:88 | `{'field':'tag','op':'eq','value':'Interview'}` → 0 rows despite dozens tagged 'interview' | Bind `v.strip().lower()`; add mixed-case test to test_query_builder_g6 |
| 15 | data | med | #12 SORT_MAP has no unique tiebreaker — LIMIT/OFFSET pagination can repeat/drop rows | db.py:888 | Hundreds of C0001.MP4 ties + mid-scroll ingest → boundary dupes/drops in a review pass; media_position can disagree with the grid | Append `, id` to every SORT_MAP entry |
| 16 | data | med | #19 /api/offload never resumes; each run clobbers shared offload-state.json; concurrent runs tear it | server.py:2401 | 400GB offload dies at 92% → UI retry destroys resume state and re-copies from zero | Per-source state path always passed as `--resume`; single-flight lock; tmp+`os.replace` state writes |
| 17 | data | med | #5 watch.py re-dispatches a permanently-failing file forever — no backoff/quarantine | watch.py:230 | Corrupt .MP4 in inbox → retry every tick 24/7, whisper warm-up each time, log spam, RAM pressure | Per-path failure count + exponential backoff + quarantine after N=3 (cleared on signature change) |
| 18 | perf | high | #21 /api/search/query materializes ALL matching ids; unbounded `IN()` breaks SQLite variable limit | server.py:1371 | Broad query → `too many SQL variables` HTTP 500 (at 1,000 rows on old SQLite); below limit: full fetch + Python sort per page click | SQL-side ORDER/LIMIT/COUNT; TEMP-table or ≤500-chunked IN for the semantic leg |
| 19 | perf | med | #20 Zero indexes on any media/tags query column — every gallery page full-scans + sorts | db.py:911 | Two full media scans + temp B-tree sort per page; tags name-lookups scan the whole table | 5 `CREATE INDEX IF NOT EXISTS` in init_db incl. covering `tags(name, media_id)` |
| 20 | perf | med | #24 Federation SQL fallback fetches full transcripts of every match, no LIMIT | federation.py:199 | Embedder outage + common CJK token + big library → hundreds of MB per project, pinned workers, false neg-cache | `LIMIT ?` bound + `substr(transcript,1,300)`; FTS5 longer-term |
| 21 | perf | med | #29 In-process retranscribe pins whisper weights in the server forever (no unload) | server.py:2509 | One retranscribe + card-dump ingest → resident server whisper + subprocess whisper + Ollama stack on the 16GB box | `transcribe.unload()` in finally, or shell out like /api/ingest |
| 22 | perf | med | #28 Phase-1 records (transcripts + words_json) held in RAM through the entire vision/proxy phases | ingest.py:1627 | Interview-heavy batch retains 100–300MB of dead strings while Ollama runs → swap pressure | Queue only `_auto_tags` + frame index/path; consume `phase1_results` destructively |
| 23 | perf | med | #23 Bin detail is N+1 across processes: per-item registry read + health stats + fresh SQLite conn | server.py:700 | 200-item NAS bin → ~5-6 network ops + sqlite open PER item, on every open AND every add/remove | Group by project: one registry read/health/conn, `WHERE id IN (...)` per project |
| 24 | perf | med | #26 GET /api/media/{id} `SELECT *` ships words_json/segments/transcript per inspector click | db.py:573 | Multi-MB per arrow-key over NAS/Tailscale for fields the inspector never renders | Explicit column list excluding words_json/segments_json |
| 25 | perf | med | #25 /api/collections re-scans + re-classifies the whole library per request, unbounded response | server.py:1717 | needs_review matches every unrated item → O(N) payload per dashboard visit, no cache | Cache keyed on (MAX(id), COUNT, MAX(processed_at)); counts + top-K + paged members |
| 26 | perf | med | #33 Waveform buffers full decoded PCM + float32 copy; timeout → uncached 500 loop | server.py:1481 | 4-hour clip ≈ 0.7GB transient; >120s decode = full re-decode + 500 on every open forever | Popen chunked read with running per-bin max; cache a failure sentinel |
| 27 | perf | med | #30 VAD loads entire decoded audio + full concat copy (~2× file in RAM) | transcribe.py:149 | 3-4h recording ≈ 1.5-1.8GB transient; concurrent retranscribes stack it on resident whisper | Stream via `sf.blocks()` + incremental `SoundFile` write |
| 28 | perf | med | #31 Auto-embed reconcile fetches every chunk's metadata just to build an id set — on every ingest | embed.py:54 | 150-250k chunks → hundreds of MB transient per run, including no-op watch runs | `col.get(include=[])`; derive media ids from chunk-id prefixes |
| 29 | perf | med | #34 Tag-alias clustering: O(n²) pure-Python cosine over 1024-dim vectors | ingest.py:1233 | 5k tags ≈ 18 min silent single-core clustering before LLM judging | numpy: normalize, `V @ V.T`, union-find over threshold pairs |
| 30 | perf | low | #32 One exiftool Perl spawn per file — no `-stay_open` | ingest.py:390 | 12k-file dump ≈ 15+ min pure spawn overhead (offload.py already batches) | `-stay_open True -@ -` coprocess with `{ready}` sentinel |
| 31 | perf | low | #27 get_stats() = 5 separate full-table aggregate scans per /api/stats | db.py:635 | ~6-7 sequential scans per dashboard load where 2 queries suffice | Single-pass `COUNT(*)/COUNT(col)/SUM(...)`; fold ratings via CASE |
| 32 | fe | high | #37 Main grid silently caps at 500 items — no pagination, no indicator, unvirtualized | MainLive.svelte:175 | Pool says 2,340, grid shows 500; clips 501+ unreachable from browse; backend offset paging exists unused | 'showing 500 of N' + offset load-more; then viewport windowing |
| 33 | fe | high | #45 Running offload cannot be aborted — Cancel orphans the stream; server keeps copying | Offload.svelte:228 | Wrong-destination 512GB copy uncancellable from UI; server-side terminate-on-disconnect exists but is never triggered | AbortController through `api.offloadRun`; confirm-stop while running; onDestroy cleanup |
| 34 | fe | med | #43 Rating/tag/language failures written to a never-rendered `err` var — silent revert | MainLive.svelte:573 | Rating flips back ~100ms later with zero feedback; the exact B11 anti-pattern toast.js was built to kill | `pushToast(..., 'error')` at the 4 catch sites |
| 35 | fe | med | #35 Bins detail has no stale-response guard — remove/rename can hit the wrong bin | Bins.svelte:60 | Slow NAS bin A response lands after fast bin B → × on displayed item DELETEs against bin B, silently | `if (activeId === id)` guard (MainLive pattern); use `detail.id` for mutations |
| 36 | fe | med | #36 SearchLive has no request sequencing — stale federated response clobbers Current-only results | SearchLive.svelte:185 | Toggle to Current-only paints rows; stalled /api/search/all resolves later → unclickable rows, contradictory facets | Module seq counter; bail after await if superseded (or AbortController) |
| 37 | fe | med | #44 Project registry removal is a one-click un-confirmed ✕ | SettingsLive.svelte:532 | Misclick → bins flip to project_unregistered, federation drops the library, path lost from UI | `confirm()` mirroring Bins.del(), spelling out consequences |
| 38 | fe | med | #47 '清全部' wipes chromadb + thumbnails + waveforms in one un-confirmed click | SettingsLive.svelte:510 | Semantic search returns nothing, grid loses thumbs; only feedback is '已清除 all' | confirm() with consequences + inline rebuild offer (pairs with #15/#16 backend fix) |
| 39 | fe | med | #40 Smart Collection thumbnails bypass appendToken/BASE — 401 on tokened remote deploys | MainLive.svelte:236 | Every collection thumb fails over Tailscale while the normal grid works; same latent bug in ChatLive | `api.thumbUrl(relPath)` helper used in both call sites |
| 40 | fe | med | #41 load()/runSearch()/loadIds() race — grid desyncs from filter UI | MainLive.svelte:262 | Slow semantic search resolves after a camera-filter load() → stale results under cleared query, selection jumps | Shared list seq token, same pattern as fetchDetail's existing guard |
| 41 | fe | med | #46 Ingest view goes permanently stale after WS drop — no resync on reconnect | IngestLive.svelte:128 | Laptop sleeps at 12/40; run finishes; morning reconnect shows '12/40 · in progress' forever | Snapshot fetch on reconnect (needs GET /api/ingest/status); staleness notice fallback |
| 42 | fe | med | #38 'Reconnect now' can stack parallel WebSockets; orphans never closed | IngestLive.svelte:172 | Double-click during handshake → duplicate event processing, spurious retry bumps, leaked sockets | Close prior socket in connect(); track 'connecting'; gate reconnectNow |
| 43 | fe | med | #42 Retranscribe status poll dies on one failed request — UI stuck 'running' | SettingsLive.svelte:225 | One missed poll under Whisper load → frozen counter + disabled button until page reload | Reschedule in catch with backoff; clear proxy setTimeout in onDestroy |
| 44 | fe | med | #48 Cards, pick toggles, rows, transcript seek are mouse-only divs — no keyboard access | MediaCard.svelte:22 | Keyboard/switch users cannot select any clip in grid view; SRs announce cards as plain text | button/role+tabindex+keydown; real checkbox for pick (SearchLive already does) |
| 45 | fe | low | #49 ApiError.message drops backend `detail` — toasts show opaque 'arkiv API 500 on /path' | api.js:23 | Every failure a status-code guessing game; actionable FastAPI detail discarded at ~20 call sites | Append `body.detail` in the constructor — one change upgrades every toast |
| 46 | fe | low | #39 imgFailed/playProgress never reset on clip change — one 404 suppresses later previews | Inspector.svelte:148 | One missing thumb → placeholder for all later audio/still/360 clips all session; stale playhead | Add both to the existing reset block |
| 47 | arch | high | #50 vision.VISION_MODEL swap is DEAD — fallback silently re-runs the failing primary | vision.py:201 | Log says 'trying fallback minicpm-v'; `_call_vision` re-reads settings → same model fails again; M9 lock guards a no-op; test mock reads the dead global | `describe_frames(paths, model=None)` threaded to `_call_vision`; delete global + lock; regression test on received model name |
| 48 | arch | high | #58 copy_bin acquires the ingest slot BEFORE its try/finally — GeneratorExit/Popen failure = 409 forever | server.py:873 | Tab closed at the pre-try yield → slot never released → every ingest endpoint 409s until restart | Move acquire inside try (or hoist try over yield+Popen); finally releases |
| 49 | arch | med | #51 Router split blocked by ~50 cross-group helpers — naive cut = import cycle | server.py:107 | `from server import _resolve_media_path` inside a router imported by server → partially-initialized ImportError | Extract service modules first (pathres/webguard/export_builders/ingest_opts), then peel 11 routers leaf-first, ingest+WS last |
| 50 | arch | med | #52 embed/retranscribe guards are rebindable module globals — by-name import freezes a bool | server.py:70 | Split router importing `_embed_rebuild_active` gets a frozen False → single-flight silently dead (embed path has no slot backstop) | `state.SingleFlight` objects + in-place-only progress dict, own PR before the maintenance router moves |
| 51 | arch | med | #53 Import-time side effects: db.init_db() + log filter fire from every importer | server.py:78 | Transitional `import server` creates .arkiv/, runs migrations against the env-configured production DB before any preflight | Move both into `_lifespan`; TestClient keeps working |
| 52 | arch | med | #54 DB_PATH dual source of truth — `--db` rebinds db.py while server/health read config | db.py:10 | Live today: `--db` run preflights/reports the DEFAULT DB while writing the backup DB — mixed-database run, no error | `get_db_path()/set_db_path()` accessor; lint-grep forbidding value imports |
| 53 | arch | med | #57 Media-extension sets hand-copied in 7 modules — and db.py's SQL copy has ALREADY drifted | ingest.py:29 | Live drift: media_type='video' SQL filter lacks .insv/.360 → 360 clips vanish from the non-search video filter; second shipped B3-class bug | mediatypes.py single source; identity test across ingest/server/watch/db |
| 54 | arch | med | #59 /api/proxy/build has no single-flight (unlike embed/retranscribe siblings) | server.py:3973 | Double-click → parallel full-library ffmpeg loops; mid-build playback streams truncated proxies | `_proxy_build_active` flag (M8 pattern); pairs with #1's atomic write |
| 55 | arch | med | #56 Whisper-guard config = 6 mutable module globals; server transcribes in-process | transcribe.py:100 | Future per-run guard knob would permanently retune the server's whisper/VAD/polish for all clients | Frozen GuardConfig via `resolve_guard(mode)`; `transcribe(path, language, guard)` — before any route exposes guard mode |
| 56 | arch | med | #60 generate_proxy swallows every failure as silent None — ffmpeg-missing indistinguishable from disk-full | ingest.py:544 | NAS with wrong FFMPEG_PATH: endless '[proxy] Failed N' + 409 「需先建 proxy」, zero lines saying why | Narrow except with class+msg; print `r.stderr[-300:]` on rc≠0 |
| 57 | arch | med | #61 Batch retranscribe swallows per-file exceptions — `failed: 500` with zero diagnostics | server.py:4224 | Hours-long run fails every file; single-clip twin surfaces errors, batch doesn't | Print class+msg per failure; last-N errors in `_retranscribe_progress` |
| 58 | arch | med | #62 Phase-1 catch-all wraps process_file AND the DB transaction, prints `str(e)` only | ingest.py:1642 | One DB defect masquerades as N bad media files (the 2026-05-25 '222/222' shape); whisper work discarded per file | Class + traceback; split try so DB-write failures say so |
| 59 | arch | low | #55 ingest.py pipeline is separable; blocked only by `_LANGUAGE_OVERRIDE` global (silently forces zh in-process) | ingest.py:56 | Future in-process `process_file` import loses operator language selection — en/ja/ko silently transcribed as zh | Maintenance subcommands → ingest_maint.py; thread `language=` param, delete the global |

## Proposed PR sequence

Constraints for every PR: Python 3.9 floor (3.8 for NAS-touching scripts), atomic, CI-green under the #131 coverage floor, ships its own regression test. Prior round's PRs #129–#132 are merged; PR7 (#133, state.py extraction) is the pending split foundation; the full APIRouter split is roadmap PR8–11.

### MUST — Wave 1: data safety (all independent of the router split)

| PR | Scope (one line) | Closes |
|---|---|---|
| R5-01 | Atomic proxy writes (same-fs `.mp4` tmp + `os.replace` + size check) + size/validity gate at the 4 proxy **consumer** sites (stream/status/build, codex C1) + narrow excepts with stderr tail in generate_proxy | #1, #60, C1 (defuses the mid-write-stream leg of #59) |
| R5-02 | `_copy_clip_verified` refuses basename clobber; per-run casefolded name set; `renamed` ndjson event | #3 |
| R5-03 | H5 merge re-parents `transcripts` before `DELETE FROM media` (3 lines + the skeptic's repro as test) | #7 |
| R5-04 | Retranscribe integrity: lang in batch backup (+ revert tolerates lang-less old backups) + batch archives the outgoing active transcript like single-clip (codex C2) + single-clip same-lang writes a corrections backup + correction writes sync the `transcripts` active-lang row (codex C3) | #14, #17, C2, C3 |
| R5-05 | Extract watch.py `run_tree()` into a shared module; use it in /api/ingest + reingest | #2 (module also preps the split) |
| R5-06 | copy_bin stream: acquire ingest slot inside try/finally | #58 |
| R5-07 | mhl `_should_ignore` excludes `*.partial`; stale-partial sweep per destination | #18 |
| R5-08 | Cache-clear hardening: `clear_system_cache()` after rmtree, 409 during rebuild, honest per-target results; thumbnails out of 'app'; UI confirms on 'all' | #15, #16, #47 |
| R5-09 | query_builder trio: date day-ceiling, tag `lower()`, `', id'` tiebreakers in SORT_MAP | #10, #11, #12 |
| R5-10 | Backslash write-side normalization in `_is_known`/`is_processed`/migrate_to_relative | #9 |
| R5-11 | Content-hash dedup — commit 1: populate xxh3 at ingest + fix 'xxh3-128' default; commit 2: hash-match re-point on unknown paths (largest DI PR) | #8 |
| R5-12 | Watch failure count + backoff + quarantine | #5 |

### MUST — Wave 2: performance (independent)

| PR | Scope | Closes |
|---|---|---|
| R5-13 | init_db indexes; structured_query SQL-side ORDER/LIMIT + chunked semantic IN; federation `LIMIT` + `substr` | #20, #21, #24 |
| R5-14 | Payload diet: detail column list, embed ids-only reconcile, collections cache + top-K, single-pass stats | #26, #31, #25, #27 |
| R5-15 | Pipeline memory: trim phase1_results to _auto_tags+frames and pop as consumed; `transcribe.unload()` in retranscribe finallys | #28, #29 |
| R5-16 | Bin detail: group items by project — one registry read/health probe/connection per project, `IN()` fetch | #23 |

### MUST — Wave 3: frontend (independent)

| PR | Scope | Closes |
|---|---|---|
| R5-17 | Offload lifecycle: AbortController + confirm-stop in UI (server disconnect-terminate already exists); per-source resumable state + single-flight + atomic state writes | #45, #19 |
| R5-18 | Stale-response seq guards in Bins/SearchLive/MainLive loaders; mutations target `detail.id` | #35, #36, #41 |
| R5-19 | Failure surfacing: pushToast at the 4 silent catch sites, poll reschedule with backoff, project-delete confirm, ApiError detail passthrough, tokened collection thumbs | #43, #42, #44, #49, #40 |
| R5-20 | Grid truncation notice + offset load-more (backend paging exists unused); windowing as follow-up commit | #37 |

### MUST — Wave 4: architecture (ordering constrained; R5-21…24 are prerequisites of the PR8–11 router split)

| PR | Scope | Closes | Split dependency |
|---|---|---|---|
| R5-21 | `describe_frames(model=None)` threaded to `_call_vision`; delete `VISION_MODEL` global + `_vision_fallback_lock`; regression test asserts fallback model reaches the call | #50 | Land **before** split (deletes a lock the split would otherwise move) |
| R5-22 | `state.SingleFlight` for embed/retranscribe + progress tracker; add `_proxy_build_active` on the same pattern | #52, #59 | **Blocking prerequisite** |
| R5-23 | `db.init_db()` + log-filter install into `_lifespan`; `get_db_path()/set_db_path()` accessor, health.py routed through it | #53, #54 | **Blocking prerequisite** |
| R5-24 | `mediatypes.py` single source for extension sets (fixes the live `.insv/.360` drift in db.py's SQL filter) + identity test | #57 | Land with the ingest_opts extraction step of the split |
| R5-25 | Execute the #51 plan: extract pathres/webguard/export_builders/ingest_opts service modules, then peel 11 routers leaf-first (admin → settings → … → ingest/WS last) | #51 | **Is** PR8–11 |

### STRETCH (any order, all independent of the split)

| PR | Scope | Closes |
|---|---|---|
| R5-S1 | Streamed VAD windows + streamed waveform peaks with failure sentinel | #30, #33 |
| R5-S2 | exiftool `-stay_open` coprocess; numpy tag-alias clustering | #32, #34 |
| R5-S3 | WS lifecycle: close-before-reconnect + connecting state; GET /api/ingest/status snapshot resync on reconnect | #38, #46 |
| R5-S4 | Keyboard access (button/role/tabindex/keydown on cards, rows, seek, pick); Inspector imgFailed/playProgress reset | #48, #39 |
| R5-S5 | ingest_maint.py extraction + `language=` parameter through process_file (note: absence forces zh, not auto-detect) | #55 |
| R5-S6 | Frozen GuardConfig replacing the 6 whisper-guard module globals — required before any route exposes guard mode | #56 |
| R5-S7 | Diagnostics: per-file error class+msg in retranscribe-all; class+traceback + split try in Phase-1 loop (trivial; may ride any wave-1 PR touching those files) | #61, #62 |

## Appendix

### PLAUSIBLE (needs manual confirmation)
None — every surviving finding was adversarially confirmed.

### REFUTED (for the record)
- **reingest's 600s cap guarantees mid-pipeline kills for long clips** — refresh path skips whisper/audio-extract entirely (`if has_audio and not existing`); the doomed-retry scenario is unreachable; residual: occasional recoverable 504 on very long clips, and a 1800s-vs-600s cap asymmetry worth normalizing.
- **WAL handoff: copying project.db without -wal drops commits** — arkiv opens/closes per operation, so SQLite checkpoints and deletes the WAL on last close; empirically no sidecar exists at quiescence; only crash-state copies are affected, which is unsafe in every journal mode.
- **GET /api/media/pool dumps the whole table on every sidebar load** — the endpoint has zero callers (sidebar derives from /api/stats); stale docstring misled the auditor; residual: dead unbounded route reachable by any videos_read token — delete or LIMIT it (overlaps baseline #21).

## Codex cross-validation

Independent Codex pass (session `019f572e`, 1m18s) over the 8 highest-stakes data-loss findings. **All 8 CONFIRMED — zero refuted, zero downgraded.** Codex's line numbers run ~40 higher than the fable table because it read `main` (slot still in `server.py`, pre-PR7); these are the anchors the fix PRs will actually cut against.

The dual-pass earned its keep on the **fixes**: Codex flagged a footgun in every proposed destructive fix, and surfaced 3 nearby defects the fable auditors missed.

| id | Codex verdict | fix-footgun Codex flagged (fold into the PR) |
|---|---|---|
| #3 copy basename clobber | CONFIRM (server.py:724/732/863/889) | Check BOTH existing dest names and per-run names, casefolded; skipped/renamed clips must be omitted or correctly remapped in `ingest_paths` (or the project index points at the wrong bytes) |
| #1 proxy final-path write | CONFIRM (ingest.py:520/533/540) | tmp must be a **same-filesystem sibling** and keep an `.mp4` suffix (or pass `-f mp4`) — else ffmpeg mis-infers format or `os.replace` throws cross-device |
| #2 timeout orphan ffmpeg | CONFIRM (server.py:2366/2771) | shared `run_tree()` helper must preserve `cwd`, env, text/encoding, AND the Windows `taskkill /T` tree-kill semantics |
| #7 migrate transcript cascade | CONFIRM (db.py:423-427, FK cascade at 338-346, proven by test_transcripts_g2.py:45-51) | `UPDATE OR IGNORE` **alone still deletes** a same-language conflicting transcript on the loser row — needs an explicit conflict policy or backup-before-delete, not a bare re-parent |
| #17 same-lang retranscribe overwrite | CONFIRM (server.py:2566-2574, db.py:585-589) | backup must include `lang`; reusing the current correction-backup format inherits #14's bug |
| #14 retranscribe-all backup lacks lang | CONFIRM (server.py:4246-4248, corrections.py:374-382) | revert must **tolerate old backups with no `lang`** (don't null/guess) |
| #18 MHL hashes partials | CONFIRM (mhl.py:268-276, offload.py:408/428/383-393) | stale-partial sweep must NOT delete another live offload's active partial — scope to this destination/state or hold a run lock |
| #58 copy_bin slot leak | CONFIRM (acquire server.py:914; yield+Popen precede the try/finally at 920-934; release only at 949-952) | use an `acquired` flag and extend the protected block to cover the first `yield` + `Popen`, not just the loop body |

### New findings from the Codex pass (not in the fable 59)

| # | Sev | Finding | file:line | Fold into |
|---|---|---|---|---|
| C1 | high | Proxy **consumers** (stream / status / build) also trust bare `proxy_path.exists()` — a truncated final proxy is served as valid AND blocks rebuild; fixing only the writer (#1) is insufficient | server.py:3924/4008/4024/4043 | **R5-01** must add a size/validity gate at these 4 consumer sites, not just the writer |
| C2 | high | retranscribe-all overwrites the per-language archive row for the NEW active language without first archiving the OUTGOING active transcript (single-clip does this; batch doesn't) — prior active text lost if backup is off/bad | server.py:4289 | **R5-04** — batch path must archive-outgoing like the single-clip twin |
| C3 | med | Correction backups/updates touch only `media.*`, never the matching active-language row in `transcripts`; the archived active row goes stale until a later lazy overwrite | corrections.py:323-328/339-340 | **R5-04** (or its own follow-up) — keep the `transcripts` active-lang row in sync on correction write |

**Net effect on the plan:** no finding was invalidated; the destructive-fix PRs (R5-01/02/03/04/07/06) gain hardened acceptance criteria, and R5-01/R5-04 grow slightly in scope (C1/C2/C3). The plan stands; execute with these footguns as the regression-test checklist.
