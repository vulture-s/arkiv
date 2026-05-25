# arkiv Ingest Pipeline

> 完整參考：各 stage、儲存路徑、exit code、maintenance modes、
> 以及 v0.3.0 → v0.3.1 升級流程。
>
> 🌐 [English](pipeline.md) | **繁體中文**

---

## 概觀

arkiv ingest 是 4 階段 pipeline，把原始素材變成可搜尋、可匯入 NLE 的
歸檔。三個階段由 `ingest.py` 依序跑；embedding 由 `embed.py` 獨立跑。

```
[ingest.py]   Preflight → Phase 1 → Phase 2 → Phase 3
[embed.py]                                              → Embedding
```

| 階段 | 做什麼 | 模型 / 工具 |
|------|--------|------------|
| 0. Preflight | 儲存路徑健康檢查（v0.3.1 新增） | — |
| 1. Phase 1 | Probe + 轉錄 + thumbnail + frames | FFmpeg + ExifTool + Whisper |
| 2. Phase 2 | Vision 描述（可 skip） | qwen3-vl:8b + minicpm-v fallback |
| 3. Phase 3 | 瀏覽器 proxy 生成 | FFmpeg（HEVC/ProRes → H.264） |
| 4. Embedding | 語意搜尋索引 | Ollama nomic-embed-text → ChromaDB |

---

## 儲存路徑（v0.3.1 起）

所有 arkiv 產出的檔案落在 `PROJECT_ROOT/.arkiv/` 下。`PROJECT_ROOT`
預設是 arkiv 安裝目錄（`~/.arkiv`），可用 `ARKIV_PROJECT_ROOT`
覆寫。

```
PROJECT_ROOT/                       ← $ARKIV_PROJECT_ROOT 或 ~/.arkiv
├── <你的素材檔>                    ← 原始素材（任何資料夾結構）
└── .arkiv/                         ← arkiv 產出全部落這
    ├── project.db                  ← SQLite（media + frames + tags）
    ├── thumbnails/
    │   ├── {stem}.jpg              ← 代表幀（50% 點）
    │   └── {stem}_frame{0..N}.jpg  ← 場景偵測 / 固定 N 幀
    ├── chroma_db/                  ← ChromaDB 持久化（embed.py）
    └── proxies/
        └── {media_id}_{path_sha1[:10]}.mp4   ← 只對 HEVC/ProRes 生
```

**Per-path env override** 仍可用：
- `ARKIV_DB_PATH` / `ARKIV_THUMBNAILS_DIR` / `ARKIV_CHROMA_PATH` / `ARKIV_PROXIES_DIR`

**DB 內 path 欄位**是相對 `PROJECT_ROOT` 的（v0.2.x Phase 8.0d 起）。
搬 `PROJECT_ROOT` 到別處不會壞，只要素材在新 PROJECT_ROOT 下的相對
位置不變。

---

## Stage 0: Preflight（`health.preflight_paths`）

在 pipeline 跑之前先驗，避免處理 N 個檔都撞同一個 root error。

| 檢查項 | 抓什麼 |
|--------|--------|
| Dangling symlink | symlink entry 存在但 target 不在（譬如 NAS share 沒掛載）|
| Writable test | 各儲存目錄跑 `mkdir + tmpfile + rm` |
| NAS mount precondition | `PROJECT_ROOT` 在 `/Volumes/` 下但 mount root 不存在 |
| Sample DB resolve | DB 抽 1 row，檔案 resolve 不到（stale `PROJECT_ROOT`） |

**失敗 → `sys.exit(4)`**。maintenance modes（`--migrate-storage` 等）
會 skip preflight，因為它們本來就是要修壞掉的狀態。

---

## Stage 1: Phase 1 — Metadata + 轉錄 + Frames

per file：

1. **ffprobe** — duration / fps / has_audio / codec / 寬高
2. **ExifTool** — 相機 / 鏡頭 / GPS / 曝光 / `ReelName` + sidecar 解析（Sony XAVC `.XML`、Blackmagic Cam app、iPhone Keys group）
3. **Whisper** — 轉錄 + Silero VAD + `segments_json` + `words_json`（CUDA 上跑 WhisperX）
4. **`extract_thumbnail`** — 50% 點代表幀，320 px 寬
5. **`extract_frames`** — 自適應 1–15 幀：
   - < 60 秒 → 固定均分
   - ≥ 60 秒 → 場景偵測（`select='gt(scene,0.3)'`）
   - 場景偵測沒抓到 → fallback 固定均分

寫一筆 `media` row + N 筆 `frames` row（vision 欄空，等 Phase 2）。

---

## Stage 2: Phase 2 — Vision

可 `--skip-vision`。在 Phase 1 後跑，因為 12 GB GPU 上 vision 跟 LLM
polish 不能共存。

1. **Unload** `qwen2.5:14b`（釋放 VRAM）
2. **Warm up** `qwen3-vl:8b`
3. per file：
   - 主模型 = `qwen3-vl:8b`
   - 失敗 frame 的 fallback = `minicpm-v:latest`
   - Scoring：`focus_score / exposure / stability / audio_quality / atmosphere / energy / edit_position / edit_reason / editability_score`
4. **連續 3 fail 就 halt**（v0.3.1 新增）— 不要在每個 file 都寫同一個 error，break + 印 resume 指令

寫入 `frames.description/tags/scores` + `media.editability_score`。

**誠實的 skip 訊息**（v0.3.1 新增）：當 Phase 2 開始 queue 是空的，
訊息會明確說明原因 —「phase 1 had X/Y failures」vs「all already
indexed」vs「genuinely no new files」— 不再只是一句誤導的「No new
files to run vision on」。

---

## Stage 3: Phase 3 — Proxy 生成

per file：如果 codec ∈ `{HEVC, ProRes, DNxHD, AV1, ...}` 且還沒 proxy，
FFmpeg 轉成 H.264 MP4，存 `PROXIES_DIR/{media_id}_{sha1[:10]}.mp4`。

H.264 MP4（譬如 Sony A7M4 預設）瀏覽器可播，skip。

---

## Stage 4: Embedding（`embed.py`）

獨立 entry — 不在 `ingest.py` 預設流程內。ingest 後手動跑或排 cron。

```bash
python embed.py             # 增量（跳過已索引）
python embed.py --rebuild   # 砍掉重建
python embed.py --search "drone footage aerial"   # CLI 快速測試
```

讀 `media.transcript` → chunk → Ollama `nomic-embed-text` →
`CHROMA_PATH/` collection `media_assets`（768-dim）。

---

## Exit Codes（v0.3.1 起）

| Code | 意義 |
|------|------|
| 0 | Clean — 全部成功 |
| 1 | 部分 fail — Phase 1 或 Phase 2 有 fail |
| 2 | 全 fail — 每個檔 Phase 1 都失敗（通常上游問題）|
| 3 | `frames.py` 最後防線：dangling thumbnails symlink（preflight 應該已抓到）|
| 4 | Preflight fail — 儲存路徑壞掉；修了再重跑 |

v0.3.1 之前，`ingest.py` 不管 fail 多少都 exit 0。runner / launchd /
cron 現在能看到實際結果。

---

## Maintenance Modes

這些 mode 不需要 `--dir`：

| Mode | 何時用 |
|------|--------|
| `--migrate-storage` | 升 v0.3.1 後第一次跑（見下方 Upgrade section） |
| `--migrate-relative` | 升 v0.2.x 後第一次跑（abs path → relative） |
| `--regenerate-proxies` | 改 codec 設定後 — 砍掉所有 H.264 proxy 重生 |
| `--vision-only` | Phase 2 halt 後 resume — 找 `description` 空的 frame 只處理那些 |

---

## DB Schema（3 表）

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

## v0.3.0 → v0.3.1 升級

v0.3.1 是 **breaking change** 預設儲存 layout：artifact 從
`BASE_DIR/{media.db, thumbnails/, chroma_db/, proxies/}` 搬到
`BASE_DIR/.arkiv/{project.db, thumbnails/, chroma_db/, proxies/}`。
提供一鍵 migration。

### Step 1：停掉跑中的 arkiv server / ingest

```bash
pkill -f "uvicorn server:app" 2>/dev/null
pkill -f "python.*ingest.py" 2>/dev/null
```

### Step 2：拉 v0.3.1，必要時重裝 deps

```bash
cd ~/.arkiv && git pull
# 或：重跑 install.sh
```

### Step 3：跑 migration

```bash
cd ~/.arkiv && python ingest.py --migrate-storage
```

Migration 會：
1. 如果 `~/.arkiv/.arkiv/project.db` 已存在就拒跑（冪等）
2. 在 `~/.arkiv/.legacy-backup-{timestamp}.tar.gz` 建 backup tarball（包含所有 legacy storage）
3. 搬 `media.db → .arkiv/project.db`（rename）+ `thumbnails/ → .arkiv/thumbnails/` + `chroma_db/ → .arkiv/chroma_db/` + `proxies/ → .arkiv/proxies/`
4. 順手清理 v0.3.1 之前留下來的 dangling symlinks
5. 驗證 `sqlite SELECT COUNT(*)` 跟 `thumbnails/` 檔數搬前搬後對得上

### Step 4：驗證

```bash
cd ~/.arkiv && python -c "
import config, sqlite3
print('DB:', config.DB_PATH)            # ~/.arkiv/.arkiv/project.db
print('Thumbs:', config.THUMBNAILS_DIR)
conn = sqlite3.connect(str(config.DB_PATH))
print('media rows:', conn.execute('SELECT COUNT(*) FROM media').fetchone()[0])
"
```

### Step 5：重啟 server

```bash
cd ~/.arkiv && bash arkiv.command   # 或 uvicorn server:app --host 0.0.0.0 --port 8501
```

### Rollback（如果需要）

```bash
rm -rf ~/.arkiv/.arkiv && tar xzf ~/.arkiv/.legacy-backup-{timestamp}.tar.gz -C ~/.arkiv
```

### Per-project layout（v0.3.1 新增，optional）

要讓每個專案的 archive 跟素材放一起，把 `ARKIV_PROJECT_ROOT` 指向
素材的父目錄：

```bash
# 每個專案有自己獨立的 .arkiv/
ARKIV_PROJECT_ROOT=/Volumes/footage/2026-client-X/ \
  python ingest.py --dir /Volumes/footage/2026-client-X/ --recursive
# → /Volumes/footage/2026-client-X/.arkiv/project.db
# → /Volumes/footage/2026-client-X/.arkiv/thumbnails/
# ...
```

這樣專案可攜 — 整個資料夾搬到別的硬碟 arkiv 仍能用（DB 內 path 是
相對 `PROJECT_ROOT` 的）。

---

## 一行驗證

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

## 相關文件

- 架構圖 + 技術棧：[../README.zh-TW.md](../README.zh-TW.md)
- 反幻覺設計：[architecture-anti-hallucination-guard.md](architecture-anti-hallucination-guard.md)
- 驗收條件：[../VERIFY.md](../VERIFY.md)
- Changelog：[../CHANGELOG.md](../CHANGELOG.md)
