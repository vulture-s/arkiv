## arkiv 工作狀態快照（截至 2026-04-09 17:30 PC session）

### 本 session 改動過的檔案

| 檔案 | 性質 | WhisperX 交叉 |
|------|------|--------------|
| `resolve_plugin/arkiv_resolve.py` | feature (Phase 7.6 tag→metadata) | ❌ |
| `server.py` | feature (proxy stream) | ⚠️ **是** — stream endpoint 加了 proxy 判斷 |
| `config.py` | feature (+PROXIES_DIR) | ❌ |
| `ingest.py` | feature (proxy generation + needs_proxy/generate_proxy) | ⚠️ **是** — 新增 3 個函數 + Phase 3 proxy block |
| `index.html` | bugfix (ctrl undefined in onerror) | ❌ |

### DB schema 變更
- **無**。沒有新增/修改欄位或 table。

### API contract 變更
- `GET /api/stream/{media_id}` — 行為變更：優先送 `proxies/{media_id}.mp4`（H.264 proxy），不存在才送原檔。response 格式不變。
- 無新增 endpoint。

### Roadmap 進度更新
- **Phase 7.6** SetMetadata 實測 ❌ 不生效 → 方案轉向 CSV Metadata Import（roadmap 已更新）
- **Phase 7.7** Browser Proxy ✅ 完成（7.7a-7.7e 全部 done，22 支 proxy 生成）
- **全流程實測** ingest 53支/200GB ✅、export 三格式 ✅、plugin import+color+markers ✅

### 未完成 / 進行中項目
- **SRT 時間軸偏移嚴重** — 另一 session 正在用 WhisperX 修
- **Phase 7.6 CSV export** — roadmap 已記錄，尚未實作
- **Plugin search crash** — 搜尋一次後無回應，缺 reset 機制
- **短片 proxy 播放** — <2s 的 clip proxy 在 Chrome 無法初始化（邊界問題，低優先）
