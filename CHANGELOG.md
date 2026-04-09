# Changelog

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
