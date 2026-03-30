# Handover: arkiv Mac 前端大修完成

## Current State (2026-03-31)

### 已修復
- **WKWebView 空白** ✅ — `var isTauri` + `withGlobalTauri` 從 PC 修回來就好了
- **SVG 爆掉** ✅ — CSS reset 改 `width:auto;height:auto` + 所有 SVG 常數加 inline style
- **Grid 卡片 thumbnail** ✅ — SVG fallback 底層 + img 覆蓋 + onerror 不破壞 DOM
- **Inspector 串流播放器** ✅ — overlay video + 波形整合（seek + playhead + 時間同步）
- **Panel resize** ✅ — 左右拖曳調整 Pool/Inspector 寬度
- **Card size 控制** ✅ — +/- 按鈕調整 2-8 欄
- **UI Scale** ✅ — 100-200% 滑桿，預設 130%
- **Open File** ✅ — 瀏覽器模式用 server-side `open -R` 開 Finder
- **Export** ✅ — Tauri 用 dialog.save + server 寫檔，瀏覽器用 blob download
- **Ratio 自適應** ✅ — 直幅/橫幅素材在 Inspector 正確顯示
- **Rotation 修正** ✅ — ingest.py 處理 ffprobe rotate metadata，DB 已更新

### 已知限制（明天繼續）
- **波形是假資料** — `waveformBars()` 是固定高度陣列，非真實音頻波形
- **In/Out marker** — 靜態顯示，不可拖曳（排 Phase 6）
- **Filmstrip** — 移到 Frame Analysis section，仍是 placeholder
- **Watchdog re-ingest** — 已加入 Phase 6 roadmap，未實作

## 跨裝置開發注意事項

### PC → Mac 同步
```bash
cd ~/.arkiv && git pull
```
- DB 不在 git 裡（`.gitignore`），兩端各自 ingest
- `thumbnails/` 也不在 git，各端自動產生
- `tailwind-static.css` + `index.html` 是共用的，pull 即生效

### Mac → PC 同步
```bash
cd ~/.arkiv && git add -A && git commit -m "描述" && git push
```

### 跨平台陷阱清單
| 陷阱 | Mac | PC |
|------|-----|-----|
| Whisper | mlx-whisper | faster-whisper (CUDA) |
| Path separator | `/` | `\`（thumbUrl 已有 `.replace()` 處理） |
| ffprobe encoding | UTF-8 | cp950（已用 `encoding='utf-8'` 修正） |
| Tauri inject | `var` not `const` | 同 |
| video rotate | ffprobe `tags.rotate` or `side_data_list` | 同 |
| Tauri dialog | `window.__TAURI__.dialog.open()` | 同，已有 fallback chain |

### Chrome first, Tauri last
1. `uvicorn server:app --host 0.0.0.0 --port 8501 --reload`
2. Chrome `localhost:8501` — 做 90% debug
3. Safari `localhost:8501` — 測相容性
4. `WEBKIT_INSPECTOR=1 cargo tauri dev` — 最終驗證

## Key Files
| File | Status |
|------|--------|
| index.html | 大修完成（~1070 行） |
| tailwind-static.css | SVG/img reset 修正 |
| server.py | 加 `/api/open-file` + `/api/export-to` + `/api/client-log` |
| ingest.py | rotation metadata 處理 |
| media.db | 3 筆素材（1 橫幅 + 2 直幅），dimension 已修正 |

## Mac 快照（明天 PC 端對照用）
- **Tag**: `mac-snapshot-20260331`
- **Commit**: `f913fd9`
- **Repo**: `github.com/ourladypeace2011-commits/arkiv`

```bash
# PC 端同步
cd ~/.arkiv
git pull
git log mac-snapshot-20260331..HEAD   # 看 PC 之後多了什麼
git diff mac-snapshot-20260331        # 對比差異
```

## Environment
- Server: uvicorn port 8501 (--reload)
- Tauri: cargo tauri dev
- Python: 3.9
- Rust: 1.94.1
- DB: ~/.arkiv/media.db
