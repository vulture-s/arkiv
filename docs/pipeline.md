# arkiv Ingest Pipeline

> Complete reference: stages, storage paths, exit codes, maintenance modes,
> and upgrade procedure (v0.3.0 → v0.3.1).
>
> 🌐 **English** | [繁體中文](pipeline.zh-TW.md)

---

## Overview

arkiv ingest is a 4-stage pipeline that turns raw media into a searchable,
NLE-importable archive. Three stages run sequentially per `ingest.py`
invocation; embedding runs out-of-band via `embed.py`.

```
[ingest.py]   Preflight → Phase 1 → Phase 2 → Phase 3
[embed.py]                                              → Embedding
```

| Stage | What it does | Model / Tool |
|-------|--------------|--------------|
| 0. Preflight | Storage path health check (since v0.3.1) | — |
| 1. Phase 1 | Probe + transcribe + thumbnail + frames | FFmpeg + ExifTool + Whisper |
| 2. Phase 2 | Vision descriptions (skippable) | qwen3-vl:8b + minicpm-v fallback |
| 3. Phase 3 | Browser proxy generation | FFmpeg (HEVC/ProRes → H.264) |
| 4. Embedding | Semantic search index | Ollama nomic-embed-text → ChromaDB |

---

## Storage Layout (since v0.3.1)

All durable artifacts live under `PROJECT_ROOT/.arkiv/`. `PROJECT_ROOT`
defaults to the arkiv install dir (`~/.arkiv`) unless overridden by
`ARKIV_PROJECT_ROOT`.

```
PROJECT_ROOT/                       ← $ARKIV_PROJECT_ROOT or ~/.arkiv
├── <your media files>              ← raw footage (any folder layout)
└── .arkiv/                         ← all arkiv-generated artifacts
    ├── project.db                  ← SQLite (media + frames + tags)
    ├── thumbnails/
    │   ├── {stem}.jpg              ← representative frame (50% point)
    │   └── {stem}_frame{0..N}.jpg  ← scene-detect or fixed N frames
    ├── chroma_db/                  ← ChromaDB persistence (embed.py)
    └── proxies/
        └── {media_id}_{path_sha1[:10]}.mp4   ← HEVC/ProRes only
```

**Per-path env overrides** still work:
- `ARKIV_DB_PATH` / `ARKIV_THUMBNAILS_DIR` / `ARKIV_CHROMA_PATH` / `ARKIV_PROXIES_DIR`

**Path stored in DB** is relative to `PROJECT_ROOT` (since v0.2.x, Phase
8.0d). Moving `PROJECT_ROOT` to another location works as long as the
media layout under it stays the same.

---

## Stage 0: Preflight (`health.preflight_paths`)

Runs before any pipeline work. Fail-fast on broken storage to avoid
processing N files with the same root error.

| Check | Catches |
|-------|---------|
| Dangling symlink | Symlink entry exists but target gone (e.g. unmounted NAS share) |
| Writable test | `mkdir + tmpfile + rm` per storage dir |
| NAS mount precondition | `PROJECT_ROOT` under `/Volumes/` but mount root missing |
| Sample DB resolve | First media row's file no longer exists (stale `PROJECT_ROOT`) |

**Fail → `sys.exit(4)`**. Preflight is skipped for maintenance modes
(`--migrate-storage` etc.) since those tools fix broken state.

---

## Stage 1: Phase 1 — Metadata + Transcription + Frames

Per file:

1. **ffprobe** — duration / fps / has_audio / codec / width / height
2. **ExifTool** — camera / lens / GPS / exposure / `ReelName` + sidecar parsing (Sony XAVC `.XML`, Blackmagic Cam app, iPhone Keys group)
3. **Whisper** — transcribe + Silero VAD + `segments_json` + `words_json` (WhisperX on CUDA)
4. **`extract_thumbnail`** — representative frame at 50% point, 320 px wide
5. **`extract_frames`** — adaptive 1–15 frames per clip:
   - < 60 s → fixed evenly-spaced
   - ≥ 60 s → scene detection (`select='gt(scene,0.3)'`)
   - Fallback to fixed if scene detection yields none

Writes one `media` row + N `frames` rows (vision columns empty until Phase 2).

---

## Stage 2: Phase 2 — Vision

Skippable with `--skip-vision`. Runs after Phase 1 because vision and LLM
polish (Phase 1) can't coexist on 12 GB GPUs.

1. **Unload** `qwen2.5:14b` (free VRAM)
2. **Warm up** `qwen3-vl:8b`
3. Per file:
   - Primary describe = `qwen3-vl:8b`
   - Fallback for failed frames = `minicpm-v:latest`
   - Scoring: `focus_score / exposure / stability / audio_quality / atmosphere / energy / edit_position / edit_reason / editability_score`
4. **Halt-on-3-consecutive-fail** (since v0.3.1): break loop and print resume hint instead of burning through every file writing the same error.

Writes `frames.description/tags/scores` + `media.editability_score`.

**Honest skip messages** (since v0.3.1): when Phase 2 starts with an empty
queue, the reason is now explicit — "phase 1 had X/Y failures" vs "all
already indexed" vs "genuinely no new files" — instead of a single
misleading "No new files to run vision on".

---

## Stage 3: Phase 3 — Proxy Generation

Per file: if codec ∈ `{HEVC, ProRes, DNxHD, AV1, ...}` and no proxy exists yet,
FFmpeg transcodes to H.264 MP4 at `PROXIES_DIR/{media_id}_{sha1[:10]}.mp4`.

H.264 MP4 files (e.g. Sony A7M4 default) are browser-compatible and skipped.

---

## Stage 4: Embedding (`embed.py`)

Separate entry — not part of `ingest.py` default flow. Run manually after
ingest or via cron.

```bash
python embed.py             # incremental (skip already-indexed)
python embed.py --rebuild   # drop and rebuild
python embed.py --search "drone footage aerial"   # quick CLI test
```

Reads `media.transcript` → chunks → Ollama `nomic-embed-text` →
`CHROMA_PATH/` collection `media_assets` (768-dim).

---

## Exit Codes (since v0.3.1)

| Code | Meaning |
|------|---------|
| 0 | Clean — all files processed successfully |
| 1 | Partial fail — some files failed Phase 1 or Phase 2 |
| 2 | All-fail — every file failed Phase 1 (likely upstream issue) |
| 3 | `frames.py` last-line defense: dangling thumbnails symlink (preflight should have caught) |
| 4 | Preflight fail — storage path broken; fix before retry |

Before v0.3.1, `ingest.py` always exited 0 regardless of fail count.
Runners / launchd / cron now see actual outcome.

---

## Maintenance Modes

These modes don't require `--dir`:

| Mode | When to use |
|------|-------------|
| `--migrate-storage` | First run after v0.3.1 upgrade (see Upgrade section below) |
| `--migrate-relative` | First run after v0.2.x upgrade (abs paths → relative) |
| `--regenerate-proxies` | After changing codec settings — deletes and rebuilds all H.264 proxies |
| `--vision-only` | Resume Phase 2 after a halt; finds frames with empty `description` and processes only those |

---

## DB Schema (3 tables)

```sql
media:   id, path (rel), filename, ext, duration_s, fps, has_audio,
         transcript, lang, segments_json, words_json,
         thumbnail_path (rel), frame_tags, editability_score,
         camera_make, camera_model, lens_model, gps_lat, gps_lon,
         iso, aperture, focal_length, creation_date, reel_name,
         color_space, processed_at, rating, ...

frames:  id, media_id, frame_index, timestamp_s,
         thumbnail_path (rel), description, tags,
         content_type, focus_score, exposure, stability,
         audio_quality, atmosphere, energy,
         edit_position, edit_reason

tags:    id, media_id, tag_name, source (auto/manual)
```

---

## Upgrading from v0.3.0

v0.3.1 is a **breaking change** to default storage layout: artifacts move
from `BASE_DIR/{media.db, thumbnails/, chroma_db/, proxies/}` to
`BASE_DIR/.arkiv/{project.db, thumbnails/, chroma_db/, proxies/}`. A
one-shot migration is provided.

### Step 1: Stop any running arkiv server / ingest

```bash
pkill -f "uvicorn server:app" 2>/dev/null
pkill -f "python.*ingest.py" 2>/dev/null
```

### Step 2: Pull v0.3.1 + reinstall deps if needed

```bash
cd ~/.arkiv && git pull
# or: re-run install.sh
```

### Step 3: Run the migration

```bash
cd ~/.arkiv && python ingest.py --migrate-storage
```

The migrator will:
1. Refuse to run if `~/.arkiv/.arkiv/project.db` already exists (idempotent)
2. Create a backup tarball at `~/.arkiv/.legacy-backup-{timestamp}.tar.gz` containing all legacy storage
3. Move `media.db → .arkiv/project.db` (rename) + `thumbnails/ → .arkiv/thumbnails/` + `chroma_db/ → .arkiv/chroma_db/` + `proxies/ → .arkiv/proxies/`
4. Cleanup any dangling symlinks left over from pre-v0.3.1 workarounds
5. Cross-check `sqlite SELECT COUNT(*)` and `thumbnails/` file count pre vs post

### Step 4: Verify

```bash
cd ~/.arkiv && python -c "
import config, sqlite3
print('DB:', config.DB_PATH)            # ~/.arkiv/.arkiv/project.db
print('Thumbs:', config.THUMBNAILS_DIR)
conn = sqlite3.connect(str(config.DB_PATH))
print('media rows:', conn.execute('SELECT COUNT(*) FROM media').fetchone()[0])
"
```

### Step 5: Restart server

```bash
cd ~/.arkiv && bash arkiv.command   # or uvicorn server:app --host 0.0.0.0 --port 8501
```

### Rollback (if needed)

```bash
rm -rf ~/.arkiv/.arkiv && tar xzf ~/.arkiv/.legacy-backup-{timestamp}.tar.gz -C ~/.arkiv
```

### Per-project layout (optional, new in v0.3.1)

To keep each project's archive next to its media, point `ARKIV_PROJECT_ROOT`
at the media parent directory:

```bash
# Each project gets its own self-contained .arkiv/
ARKIV_PROJECT_ROOT=/Volumes/footage/2026-client-X/ \
  python ingest.py --dir /Volumes/footage/2026-client-X/ --recursive
# → /Volumes/footage/2026-client-X/.arkiv/project.db
# → /Volumes/footage/2026-client-X/.arkiv/thumbnails/
# ...
```

This makes the project portable — move the whole folder to another drive
and arkiv keeps working (paths in DB are relative to `PROJECT_ROOT`).

---

## Verification one-liner

```bash
cd ~/.arkiv && python -c "
import config, sqlite3
print('DB:', config.DB_PATH)
print('THUMB:', config.THUMBNAILS_DIR)
c = sqlite3.connect(str(config.DB_PATH))
print('media:', c.execute('SELECT COUNT(*) FROM media').fetchone()[0])
print('frames:', c.execute('SELECT COUNT(*) FROM frames').fetchone()[0])
print('vision:', c.execute(\"SELECT COUNT(*) FROM frames WHERE description != ''\").fetchone()[0])
"
```

---

## References

- Architecture diagram + tech stack: [../README.md](../README.md)
- Anti-hallucination design: [architecture-anti-hallucination-guard.md](architecture-anti-hallucination-guard.md)
- Acceptance criteria: [../VERIFY.md](../VERIFY.md)
- Changelog: [../CHANGELOG.md](../CHANGELOG.md)
