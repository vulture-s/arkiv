# CODEX RESULT — `claude/continue-fixes-SY9Eh`

## 結論

本 branch 在既有的 Windows dialog crash 修復之上，延伸 8 個交付，分兩類：

- **Bug fix / 既有功能缺口**：Tauri dialog 殘留 title、proxy Chrome 不可播、server/CLI proxy 命令不一致、`ARKIV_PROXIES_DIR` 半生效、`exiftool_extract` 定義但沒呼叫
- **新功能**：`--regenerate-proxies` CLI、真實 waveform、In/Out marker 拖曳、marker-aware export

`pytest` 從 43 → 49 tests，新增的 6 個 trim export regression 全綠。受環境限制未能親自驗證的：Windows Tauri dialog 實際互動、DaVinci/FCP 匯入 trimmed EDL/FCPXML、Mac WebView 拖曳手感。

## 交付列表

| # | Commit | Scope | 狀態 |
|---|---|---|---|
| 1 | `625a2f9` | `fix(ui)`: drop title arg from remaining Tauri file dialogs | PASS |
| 2 | `09e738c` | `feat(ingest)`: wire exiftool_extract into process_file | PASS |
| 3 | `312b627` | `fix(proxy)`: force yuv420p + tighter GOP for browser-playable proxies | PASS（未驗證 Chrome 實際播放） |
| 4 | `2d13a3f` | `feat(ingest)`: --regenerate-proxies CLI + dedupe proxy command | PASS |
| 5 | `1f0d58a` | `feat(waveform)`: real audio peaks from ffmpeg | PASS |
| 6 | `59f986d` | `fix(server)`: respect ARKIV_PROXIES_DIR env var across all proxy paths | PASS |
| 7 | `13af833` | `feat(ui)`: draggable In/Out markers on inspector waveform | PASS（未驗證 Tauri WebView 拖曳） |
| 8 | `ba7373b` | `feat(export)`: apply In/Out marker trim to SRT/VTT/TXT/EDL/FCPXML | PASS（未驗證 NLE 匯入） |
| 9 | `0f35365` | `test(export)`: regression tests for In/Out trim | PASS — 6 新 tests 全綠 |

## Root Cause Summary

### 625a2f9 — Tauri dialog 殘餘 title
前次修復只拿掉 primary path。fallback 的 `plugin:dialog|open` invoke 和 `doExport` 的 `dialog.save` 還帶 `title`，同一個 rfd crash 風險。三處共用 `rfd::FileDialog` backend，一起清掉。`message`/`ask` 走 `rfd::MessageDialog`，不受影響，保留 title。

### 09e738c — exiftool 沒接線
Phase 8 已定義 `exiftool_extract()` 且 DB 有 11 個 exif 欄位，但函式從未被呼叫。`process_file` 加一行 `exif = exiftool_extract(str(path))` + 解構到 record dict 完成接線。`exiftool_extract` 已處理 `FileNotFoundError`（無 exiftool binary 時回 `{}`），對現況零回歸。

### 312b627 — 短片 proxy Chrome 不可播
Root cause 不是「短片」本身，是 `libx264` 預設輸出像素格式沿用輸入。ProRes/HEVC 10-bit 源會產 yuv422p/yuv444p/yuv420p10le，Chrome HTML5 decoder 一律拒播。加：
- `-pix_fmt yuv420p`（強制 8-bit 4:2:0，唯一瀏覽器普遍支援的 chroma subsampling）
- `-profile:v high -level:v 4.0`（相容 profile）
- `-g 30`（GOP 1s 而非 libx264 預設 250；<8s clip 原本只有單一 keyframe）

### 2d13a3f — proxy 命令雙份
312b627 只改到 `ingest.generate_proxy`；`server._build_proxies`（`POST /api/proxy/build`）有自己一套 ffmpeg 命令，UI 按鈕觸發的 proxy 還是舊設定。改為 lazy import `ingest` + 呼叫 `generate_proxy`，單一 source of truth。另加 `--regenerate-proxies` CLI 讓舊 proxy 能重建（`if proxy_path.exists(): return` 短路會跳過已存在者）。

### 59f986d — ARKIV_PROXIES_DIR 半生效
`server.py` 三處（stream/status/build endpoint）寫死 `ROOT / "proxies"` 而非 `config.PROXIES_DIR`。設了 `ARKIV_PROXIES_DIR` env var 時，ingest 會寫到新位置但 server 繼續找舊路徑，結果 `/api/stream` 永遠 fallback 到原檔（可能不可播）。三處統一 + `mkdir(parents=True)`。

### 1f0d58a — waveform 寫死假資料
`waveformBars()` 是 36 格寫死高度陣列。新增 `GET /api/media/{id}/waveform?bins=N`：ffmpeg decode mono 8kHz PCM → numpy 分 bin 取 `max(|amp|)` → 正規化 0..1 → JSON 快取 `waveforms/<id>_<bins>.json`。前端 `renderRealWaveform(id, bins)` 非同步抓、原地替換 DOM。沒音訊的 clip 短路回零，不叫 ffmpeg。首次 ~1s，後續 <10ms。

### 13af833 — In/Out marker 不可拖曳
Marker 原本純 CSS `left: 15%/72%` 寫死。改為：
- CSS 移除寫死 `left`、加 `cursor: ew-resize`、6px hit area（視覺 2px 透過 `translateX(-3px)` 居中）、selected band `pointer-events: none`
- `inOutState` per-clip 狀態，key 為 media id（切 clip 自動重置）
- `setupMarkerDrag`：window-level `mousemove`/`mouseup` 監聽，按 `rec.duration_s` 換算 pointer X 為秒，強制 `in < out - 0.1s`，同步更新 marker 位置 + selected band + IN/OUT 時間 label
- Marker 上的 `onclick` stopPropagation，避免拖曳結束觸發 parent 的 seek
- Play-mode template 原本連 marker DOM 都沒有，補回來讓播放中仍可拖

### ba7373b — marker 沒驅動 export
Trim 語意有 5 種格式要處理：

| 格式 | 行為 |
|---|---|
| SRT/VTT | 過濾 overlap segment、rebase 起始 0 |
| TXT | 僅串接 in-window segment 文字；無 segment 資料故意傳空（無法時間切純文字） |
| EDL | source TC = camera_tc + trim_in，record TC 仍 01:00:00:00，`* LOC` markers 過濾+rebase |
| FCPXML | `<asset>` 保留完整檔長，`<sequence>`/`<asset-clip>` 用 trim 長度，`asset-clip start = camera_tc + trim_in` 讓 NLE 定位正確 source frame；marker rebase |

`has_trim` 以 0.05s 為容差避免像素誤差把 full-clip 誤判為 trimmed。未移 marker → byte-identical 跟原本一樣。前端 `doExport` 讀 `inOutState`、加 `_trim` 檔名後綴。

### 0f35365 — trim regression tests
覆蓋前述所有分支：full-range trim no-op、SRT filter+rebase、TXT 無 segment 空輸出、EDL TC 平移（含 full-clip src_end 不得出現的反向 assertion）、FCPXML asset vs clip duration 區分 + start frame 計算、`/export-to` 傳遞 trim 參數到檔案。

## 驗證

### 本地 pytest

```text
$ python -m pytest tests/
49 passed, 1 warning in 5.19s
```

| 測試檔 | 數量 |
|---|---|
| `test_db.py` | 既有，全綠 |
| `test_server.py` | 5 → **11**（+6 trim） |
| `test_phase8.py` | 既有，全綠（需 real numpy + PIL） |
| `test_transcribe_guards.py` | 既有，全綠 |
| `test_vectordb.py` | 既有，全綠 |

### 靜態

- `python -m py_compile` 對 `server.py` / `ingest.py` / `db.py` / `config.py` 全數 OK
- `index.html` tag balance：div 190/190、span 83/83、button 42/42、script 4/4（5/4 的 mismatch 是 line 1357 的註解 `<script>` 假匹配）

## ⚠️ REVIEW

| 項目 | 為何 REVIEW | 下一步 |
|---|---|---|
| 312b627 proxy Chrome 播放 | 環境無 Windows + Chrome，沒親自打開 trimmed proxy 驗證 | 在 PC 跑 `ingest --regenerate-proxies`，隨機抽 3 支打開 Tauri WebView 播放 |
| 13af833 marker 拖曳 UX | 無 Tauri WKWebView 可互動測；`touch-action: none` 對觸控螢幕行為未驗 | 開 Tauri app 手動拖曳，確認拖曳中 seek 不觸發、IN/OUT label 即時更新 |
| ba7373b EDL/FCPXML 匯入 | 無 DaVinci/FCP 可開驗證 `01:00:10:00` source TC 是否被 NLE 正確解讀 | 匯出 trimmed EDL/FCPXML 到 DaVinci Resolve + Final Cut Pro 各試一次 |
| 59f986d ARKIV_PROXIES_DIR override | 沒實測設 env var 後 ingest → server round-trip | `ARKIV_PROXIES_DIR=/tmp/px python server.py` 啟動 → 觸發 proxy build → 確認 `/api/stream` 會從 `/tmp/px` 取檔 |

## Red Lines 自檢

- [x] 沒改 Tauri major / `tauri.conf.json` / `capabilities/default.json`
- [x] 沒改 `config.py` 預設閾值
- [x] 沒刪 `nativePickFolder` 的 `prompt()` fallback
- [x] 沒碰 `resolve_plugin/`
- [x] Pre-commit：無 `.env` / `settings.local.json` / credentials 被 staged
- [x] Python 3.9 相容（無 `match/case`、無 `X | None` type union、無 `list[dict]`）
- [x] 依賴版本未降級（只新增 numpy / Pillow / fastapi testclient 到 test env，未動 `requirements.txt`）

## 檔案變更

| 檔案 | 行數變動 |
|---|---|
| `index.html` | +119 / -17 |
| `server.py` | +112 / -20 |
| `ingest.py` | +58 / -2 |
| `tests/test_server.py` | +105 / 0 |

## 未處理（明確留給下輪）

- `A.1` Phase 7.6 CSV Metadata Import — 需要先研究 DaVinci CSV 規範
- `A.2` Plugin search crash — 需要 DaVinci Resolve 環境重現，靜態看不出 root cause
- `D.8` AC 5/6 Windows dialog 手動驗證證據
- `D.9` Tauri sidecar PyInstaller 打包（跨 commit 大工程，需 Windows + Mac 驗）
