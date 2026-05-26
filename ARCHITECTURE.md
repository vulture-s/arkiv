# arkiv — Architecture

> **For**：contributors / UI redesigners / fork authors / future maintainers
> **Updated**：2026-05-26
> **Stack**：FastAPI + SQLite + ChromaDB + Tauri WebView + FFmpeg + Whisper + Ollama

---

## Overview

arkiv 是 local-first 開源 media asset manager — ingest 拍攝素材，做 transcoding probe / Whisper 轉錄 / Vision tagging / metadata extraction，存 SQLite + ChromaDB，提供 GUI（Tauri WebView）/ CLI / REST API 三條入口。MIT license，0 雲端，0 phone-home。

設計取捨：
- **Local-first** — 全部運算本機（FFmpeg / Whisper / Ollama），檔案不離開使用者磁碟
- **Per-project storage**（Phase 8.0c）— DB / thumbnails / chroma / proxies 默認落 `$ARKIV_PROJECT_ROOT/.arkiv/`，搬家不斷線
- **API-first** — 41 endpoints，UI 只是 API consumer 之一
- **Schema simple**（19 cols `media` table），避免 ORM 過設計

---

## Module Map

| Module | 職責 |
|---|---|
| `server.py` | FastAPI app — 41 endpoints（search / media CRUD / ingest control / export / stats / proxy / WS）|
| `ingest.py` | Pipeline CLI — probe → thumbnail → whisper → vision → proxy；含 `--refresh` `--migrate-storage` `--regenerate-proxies` flags |
| `db.py` | SQLite schema + `init_db()` migrations + `to_relative()`/`resolve_path()` per Phase 8.0b；`media` 表 19 cols（path / duration / camera / lens / iso / aperture / shutter / WB / start_tc / reel_name / rating / file_hash / hash_algo / ...）|
| `vectordb.py` | Chroma client + embed + `search()` with dedup |
| `config.py` | Env vars + paths + `PROJECT_ROOT` + `_ASCMHL_DIR` + `discover_projects()` helper |
| `health.py` | Preflight 4-check + `project_health()` per-project state + `_check_mount(path)` NAS helper |
| `mhl.py` | ASC MHL v2 generate/verify CLI（xxh3-128 + chain + interop with ascmitc/mhl reference impl）— W3.1 |
| `offload.py` | DIT multi-destination copy + chunked-hash verify + state file resume + sidecar 5 family + MHL emit — W3.2 |
| `camera_report.py` | 20-col DaVinci/Numbers/Excel CSV w/ summary footer，CJK UTF-8 — W3.3 |
| `federation.py` | Cross-project query fan-out（ThreadPoolExecutor + dedicated sqlite3/Chroma connections per project + timeout + errors[] response）— W2.2 |
| `projects.py` | `~/.arkiv-projects.json` registry CLI（add/list/remove/sync）|
| `search.py` | CLI entry — `--query "..." --all-projects` |

---

## Data Flow

**Ingest**：
```
file path → ffprobe → thumbnail (FFmpeg) → DB row (media table)
    → Whisper transcript (zh/en/ja auto-detect)
    → Vision frames (Ollama qwen3-vl:8b → tags)
    → ChromaDB embed (semantic search index)
    → ExifTool metadata extraction (camera / lens / iso / aperture / shutter / WB / start_tc)
```

**Search**：
```
query
  ├─ single project    → DB LIKE fallback OR ChromaDB semantic
  └─ --all-projects    → federation fan-out (ThreadPoolExecutor)
                          → per-project DB + Chroma 獨立 path
                          → merge + dedup + path 消歧 + errors[]
```

**Export**：
```
DB row → format dispatch
  ├─ Subtitle: SRT / VTT / TXT (with In/Out marker trim)
  ├─ NLE: EDL (CMX3600 w/ frame markers) / FCPXML (asset-clip start frame)
  ├─ Report: DaVinci CSV (20-col w/ summary)
  └─ DIT: MHL v2 (xxh3-128 chain)
```

---

## Anti-patterns（實戰教訓，不是預測）

### 1. Federation 不重用既有 module singleton
**規則**：跨 project query 必走獨立 low-level path（dedicated `sqlite3.connect` + 獨立 Chroma client），不重用 `db.get_conn()` 跟 `vectordb` module-level singleton。
**為什麼**：`db.py / vectordb.py / config.py` 全 module-level globals；federation 若 mutate `config.DB_PATH` 跨 thread 跑會撞 race。
**代替方案**：見 `federation.py` `_query_single_project()` per-project connection pattern；2026-05-26 W2.2 ship 教訓 codified。

### 2. Path normalization 必 NFC
**規則**：跨檔案系統的 path string（寫進 MHL XML、寫進 DB、傳 hash）必先 `unicodedata.normalize('NFC', s)`。
**為什麼**：macOS HFS+/APFS 預設 NFD（拆解 unicode），跨 NTFS/ext4 NFC 會 hash mismatch + 字面比對失敗（同一檔案 looks like 兩個）。
**代替方案**：見 `mhl.py normalize_mhl_path()` helper；W3.1 spec §1.6 強制。

### 3. subprocess 必走 sys.executable
**規則**：spawn Python subprocess 必用 `sys.executable`，不 hardcode `python3` / 路徑。
**為什麼**：FastAPI server + ingest pipeline 跨 Python venv / Conda env / system Python，hardcoded 抓錯 interp 就跑錯 model。
**代替方案**：見 `core/rules/common/platform-compatibility.md §Python Concurrency`（hevin-ai-os）；歷史踩三次。

### 4. Per-project storage 走 config.PROJECT_ROOT
**規則**：DB / thumbnails / chroma_db / proxies 路徑必 derive from `config.PROJECT_ROOT`，不寫死 `~/.arkiv/`。
**為什麼**：Phase 8.0c 之前 fresh-clone install 會抓 maintainer 的 proxy 路徑（cross-install carry），曝光別人素材；per-project storage 永久隔離。
**代替方案**：`config.PROJECT_ROOT / ".arkiv" / "<subdir>"` pattern；`health.preflight_paths()` 啟動驗。

### 5. Vision pipeline timeout 不靠 urllib timeout 單獨
**規則**：Vision call hang detection 必補 per-frame elapsed gate + per-file overall timeout（e.g. 5 min），不依賴 `urllib timeout=` 單一機制。
**為什麼**：chunked-transfer 邊界 case `timeout=` 不 fire；qwen3-vl 在 Ollama 0.23 全 CPU 撞此 case → 8hr 0 progress（2026-05-25 incident）。
**代替方案**：見 `apps/brand-studio/skills/arkiv-ingest/SKILL.md §1.7`（hevin-ai-os） — 4 條 awareness rule。

---

## Extension Points

- **新 export format** → `server.py /api/media/{id}/export/{fmt}` dispatch + 對應 builder function
- **新 metadata extraction** → `ingest.py exiftool_extract()` 加 tag list + `db.py init_db()` 加 ALTER TABLE migration row
- **新 vision model** → `vision.py` swap model name；上 model 第一動跑 `ollama ps` 看 PROCESSOR=GPU 不是 CPU
- **新 cross-project aggregation** → `federation.py _query_single_project()` 加邏輯；ThreadPoolExecutor 邊界注意 timeout + errors[]

---

## References

- **Roadmap**：`references/plans/arkiv/arkiv-roadmap.md`（hevin-ai-os）— phase 進度 + 已完成里程碑
- **Spec dir**：`references/plans/arkiv/`（hevin-ai-os） — 各 W phase / Phase 8.0c / W3 三件套詳細 spec
- **Skill**：`apps/brand-studio/skills/arkiv-ingest/SKILL.md`（hevin-ai-os） — ingest 操作 SOP + failure-mode awareness
- **W3.1 PR #13** / **W2.2 PR #14** / **W3.2 PR #17** / **W3.3 PR #16** / **B10b3 PR #15** — module 落地時序
