# Changelog

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
