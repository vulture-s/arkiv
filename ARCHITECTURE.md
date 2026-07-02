# arkiv — Architecture

> **For**：contributors / UI redesigners / fork authors / future maintainers
> **Updated**：2026-07-02
> **Stack**：FastAPI + SQLite + ChromaDB + Ollama (chat/vision/embed) + Whisper + FFmpeg + Svelte SPA + Tauri WebView

---

## Overview

arkiv 是 local-first 開源 media asset manager — ingest 拍攝素材，做 transcoding probe / Whisper 轉錄 / Vision tagging / metadata extraction，存 SQLite + ChromaDB，並提供**語意搜尋 + 自然語言 chat（RAG）+ NLE/DIT 導出**。四條入口：Svelte SPA（Tauri WebView / 瀏覽器）、REST API、CLI、read-only MCP server。MIT license，0 雲端，0 phone-home。

設計取捨：
- **Local-first** — 全部運算本機（FFmpeg / Whisper / Ollama），檔案不離開使用者磁碟
- **Per-project storage**（Phase 8.0c）— DB / thumbnails / chroma / proxies 默認落 `$ARKIV_PROJECT_ROOT/.arkiv/`，搬家不斷線（path 存相對）
- **API-first** — ~70 endpoints，SPA 只是 API consumer 之一
- **Local-trust auth** — 同機瀏覽器免 token；遠端（含 tailscale/reverse-proxy）一律要 scoped Bearer token（見 anti-pattern #6）
- **Schema simple**（`media` 表 ~19 cols），避免 ORM 過設計

---

## Module Map（backend，`*.py` @ repo root — 37 模組，按 concern 分組）

**Core API & auth**
| Module | 職責 |
|---|---|
| `server.py` | FastAPI app — ~70 endpoints（search / media CRUD / ingest control / export / stats / proxy / **chat** / WS）+ SPA 靜態服務 + per-route scope 閘 |
| `auth.py` | Token scopes + loopback 信任（同機瀏覽器免 token，除非帶 forwarding header）|
| `arkiv_token.py` | API token mint/list/revoke CLI |
| `admin.py` | Token admin（bootstrap seed + per-machine token 派發）|

**Ingest pipeline**
| Module | 職責 |
|---|---|
| `ingest.py` | Pipeline CLI — probe → thumbnail → whisper → vision → proxy → embed；`--refresh` `--migrate-storage` `--regenerate-proxies` |
| `transcribe.py` | 轉錄後端（Apple Silicon 走 MLX，其餘 faster-whisper）|
| `vision.py` | Ollama VLM 逐幀 tagging（model 安裝檢查 + GPU/CPU guard）|
| `embed.py` / `vectordb.py` | 嵌入 + Chroma client + `search()`（dedup + embedding-dimension mismatch guard）|
| `frames.py` | 360/dual-fisheye 偵測 + 抽幀 |
| `geo.py` | EXIF GPS → 地點標籤（Tier 1）|
| `codec.py` | 輕量 codec 偵測（無 ML 依賴）|
| `jobs.py` | SQLite-backed ingest job queue（Phase 11.5c）|
| `resource_probe.py` | 資源感知層（Phase 11.5）|
| `watch.py` | Folder watcher（Phase 11.2）— stability + unmount guard，入 job queue |

**Storage & config**
| Module | 職責 |
|---|---|
| `db.py` | SQLite schema + `init_db()` migrations + `to_relative()`/`resolve_path()`（per-project 相對路徑）；`media` ~19 cols |
| `config.py` | Env vars + `PROJECT_ROOT` + 路徑 derive + `discover_projects()` |

**Search / chat / curation**
| Module | 職責 |
|---|---|
| `chat.py` | Chat RAG surface — 5-intent 分類 → vector search → LLM 回應 → scene_ids |
| `llm.py` | LLM router（Ollama chat）|
| `query_builder.py` | 結構化 query builder（G6）|
| `smart_collections.py` | 規則驅動策展（Smart Collections）|
| `tag_quality.py` / `tag_aliases.py` | Vision tag 首篩品質過濾 + library tag 別名圖（SKOS-lite）|
| `corrections.py` / `recorrect.py` | Per-project 修正字典 + 其 CLI（Phase 9.6b）|
| `federation.py` | 跨 project query fan-out（ThreadPoolExecutor + per-project 連線 + timeout + `errors[]`）|
| `search.py` | CLI search 入口（`--query "…" --all-projects`）|

**Delivery / DIT / export**
| Module | 職責 |
|---|---|
| `offload.py` | DIT 多目的地 copy + chunked-hash verify + 斷點續傳 + sidecar 5 family + MHL emit（W3.2）|
| `mhl.py` | ASC MHL v2 generate/verify（xxh3-128 chain，interop ascmhl 參考實作）（W3.1）|
| `camera_report.py` | 20-col DaVinci/Numbers CSV（CJK UTF-8，summary footer）（W3.3）|
| `export.py` / `subtitle.py` | Corpus/JSONL 導出 CLI（Phase 12）+ 字幕排版引擎（Phase 12.5）|

**Ops / registry / MCP**
| Module | 職責 |
|---|---|
| `projects.py` | `~/.arkiv-projects.json` registry CLI（add/list/remove/sync）|
| `settings.py` | 持久化設定覆寫（default ← global ← project）（G5）|
| `health.py` | Preflight 4-check + per-project health + `_check_mount()` NAS helper |
| `mcp_server.py` | Read-only MCP server（Phase 14）— 同 DB/search 的工具面 |
| `bench_stt.py` | STT benchmark util |

### Frontend（`frontend/src/`，Svelte 4 + Vite 5 SPA）
- **`App.svelte`** — `svelte-spa-router` hash routing。10 條 live route 佔 bare path（`/`→MainLive、`/chat-live`、`/search-live`、`/query-live`、`/ingest-setup`、`/ingest-live`、`/offload`、`/settings`、`/live`）；10 個 mock artboard 命名在 `/_design/*`（設計參考，S-Cleanup 保留）
- **`routes/`** — MainLive（grid + inline Inspector，唯一掛 shared TopBar）、ChatLive（RAG 對話 + scene 縮圖 deep-link）、Search/QueryLive（排名檢視）、Ingest/Offload/Settings
- **`lib/`（21 元件）** — TopBar、PoolSidebar、MediaCard、Inspector、Waveform、Pano360 等 + `api.js`（fetch client；base `VITE_API_URL`，remote 帶 Bearer token）+ `prefs.js`/`toast.js` stores
- **Deep-link**：`#/main-live?sel=<id>` 選中某片（grid + inspector），SearchLive / QueryLive / ChatLive 共用（`MainLive.selectFromParam` 在 mount 讀取，庫外 id 會 fetch-by-id 補上）

---

## Data Flow

**Ingest**：
```
file path → ffprobe → thumbnail (FFmpeg) → DB row (media)
    → Whisper transcript (MLX / faster-whisper, zh/en/ja auto-detect)
    → Vision frames (Ollama VLM → tags → tag_quality 篩)
    → ChromaDB embed (bge-m3, 語意索引)
    → ExifTool metadata (camera / lens / iso / aperture / shutter / WB / start_tc / GPS→geo)
```

**Watch → Queue → Ingest**（Phase 11.2 / 11.5c）：
```
folder watcher (stability + unmount guard) → SQLite job queue (jobs.py)
    → resource_probe gate → ingest pipeline
```

**Search**：
```
query
  ├─ single project  → DB LIKE fallback OR ChromaDB semantic
  └─ --all-projects  → federation fan-out (ThreadPoolExecutor)
                        → per-project DB + Chroma 獨立連線 → merge + dedup + path 消歧 + errors[]
```

**Chat（RAG）**：
```
prompt → 5-intent classify (compilation / refinement / similarity / analytics / general)
       → vector search (Chroma, 可帶 project_scope)
       → LLM 回應 (Ollama via llm.py)
       → scene_ids[] → UI 解析成縮圖 / #/main-live?sel= deep-link
```

**Export**：
```
DB row → format dispatch
  ├─ Subtitle: SRT / VTT / TXT (In/Out marker trim)
  ├─ NLE: EDL (CMX3600 frame markers) / FCPXML (asset-clip start frame)
  ├─ Report: DaVinci CSV (20-col summary)
  └─ DIT: MHL v2 (xxh3-128 chain)
```

**Auth（每個 API 請求）**：
```
request → loopback peer 且「無」forwarding header → 免 token（full scopes）
        → 否則 → 要求 scope 相符的 Bearer token
        （tailscale serve / reverse proxy 從 127.0.0.1 連入但加 X-Forwarded-For → 依設計要 token）
```

---

## Anti-patterns（實戰教訓，不是預測）

### 1. Federation 不重用既有 module singleton
**規則**：跨 project query 必走獨立 low-level path（dedicated `sqlite3.connect` + 獨立 Chroma client），不重用 `db.get_conn()` 跟 `vectordb` module-level singleton。
**為什麼**：`db.py / vectordb.py / config.py` 全 module-level globals；federation 若 mutate `config.DB_PATH` 跨 thread 跑會撞 race。
**代替方案**：見 `federation.py` `_query_single_project()` per-project connection pattern（W2.2）。

### 2. Path normalization 必 NFC
**規則**：跨檔案系統的 path string（寫 MHL XML、寫 DB、傳 hash）必先 `unicodedata.normalize('NFC', s)`。
**為什麼**：macOS HFS+/APFS 預設 NFD，跨 NTFS/ext4 NFC 會 hash mismatch + 字面比對失敗（同檔看似兩個）。
**代替方案**：見 `mhl.py normalize_mhl_path()`；W3.1 spec §1.6 強制。

### 3. subprocess 必走 sys.executable
**規則**：spawn Python subprocess 必用 `sys.executable`，不 hardcode `python3` / 路徑。
**為什麼**：FastAPI server + ingest pipeline 跨 venv / Conda / system Python，hardcoded 抓錯 interp 跑錯 model。
**代替方案**：`core/rules/common/platform-compatibility.md §Python Concurrency`（hevin-ai-os）；歷史踩三次。

### 4. Per-project storage 走 config.PROJECT_ROOT
**規則**：DB / thumbnails / chroma_db / proxies 路徑必 derive from `config.PROJECT_ROOT`，不寫死 `~/.arkiv/`。
**為什麼**：Phase 8.0c 之前 fresh-clone install 會抓 maintainer 的 proxy 路徑（cross-install carry），曝光別人素材。
**代替方案**：`config.PROJECT_ROOT / ".arkiv" / "<subdir>"`；`health.preflight_paths()` 啟動驗。

### 5. Vision pipeline timeout 不靠 urllib timeout 單獨
**規則**：Vision call hang detection 必補 per-frame elapsed gate + per-file overall timeout（e.g. 5 min），不依賴 `urllib timeout=` 單一機制。
**為什麼**：chunked-transfer 邊界 case `timeout=` 不 fire；qwen-VL 在全 CPU 撞此 case → 8hr 0 progress（2026-05-25 incident）。
**代替方案**：見 `apps/brand-studio/skills/arkiv-ingest/SKILL.md §1.7`（hevin-ai-os）。

### 6. Loopback trust ≠ forwarded 請求
**規則**：免 token 只認「真正同機 peer 且無 forwarding header」。tailscale serve / reverse proxy 從 127.0.0.1 連入但加 `X-Forwarded-For` → 一律要 token。
**為什麼**：否則遠端攻擊者偽造 `X-Forwarded-For: 127.0.0.1` 就能拿本機全權限。
**代替方案**：`auth.py _looks_proxied()` + `_trust_loopback()`；remote 存取走 mint token（2026-07-02 tailscale 存取實測教訓）。

---

## Extension Points

- **新 export format** → `server.py /api/media/{id}/export/{fmt}` dispatch + 對應 builder（`export.py`/`subtitle.py`）
- **新 metadata 抽取** → `ingest.py` exiftool 段加 tag + `db.py init_db()` 加 `ALTER TABLE` migration
- **新 vision / chat / embed model** → `config.py` 的 `ARKIV_VISION_MODEL` / `ARKIV_CHAT_MODEL` / `ARKIV_EMBED_MODEL`；上 vision model 先 `ollama ps` 確認 PROCESSOR=GPU
- **新 chat intent** → `chat.py` `KNOWN_INTENTS` + 對應 `handle_*()`，回 scene_ids 供 UI deep-link
- **新 cross-project 聚合** → `federation.py _query_single_project()`；ThreadPoolExecutor 邊界注意 timeout + `errors[]`
- **新 SPA 畫面** → `frontend/src/routes/` 加 route + `App.svelte` 註冊；跨畫面跳素材用 `#/main-live?sel=<id>`

---

## References

- **Roadmap / spec**：`references/plans/arkiv/`（hevin-ai-os）— phase 進度 + W-phase / Phase 8.0c / W3 三件套 spec
- **Ingest SOP**：`apps/brand-studio/skills/arkiv-ingest/SKILL.md`（hevin-ai-os）— 操作 SOP + failure-mode awareness
- **Auth / token**：`docs/auth-tokens-1a/1b/1c-handover.md` · **Chat RAG**：`docs/chat-rag-4a/4b/4c-handover.md` · **MCP**：`docs/phase-14-mcp-handover.md`
- **ADR**：`docs/adr/`（DIT offload wrapper 0001 等）
