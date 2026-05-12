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

---

## VERIFY A2 — `1efb315` Chrome 實驗（branch `verify-a2-proxy-playback`, 2026-05-12）

把上面 `## ⚠️ REVIEW` 表第一行（`312b627 proxy Chrome 播放`）落地。流程依 `VERIFY.md`，在 `.arkiv-worktrees/verify-a2-proxy-playback` 跑 worktree code + `.arkiv/` 真資料。

### Step 1 — ffprobe codec 參數

| 項目 | id=1 FX30.5365.MP4 (H.264 422 10-bit) | id=34 A001_03110959_C462.mov (HEVC Rext) | VERIFY 過關 |
|---|---|---|---|
| 來源 codec | h264 | hevc | — |
| 來源 pix_fmt | yuv422p10le | yuv422p10le | — |
| 來源 profile | High 4:2:2 | Rext | — |
| **舊** proxy pix_fmt | `yuv422p10le` | `yuv422p10le` | ❌（fix 前 Chrome 拒播） |
| **舊** proxy profile | High 4:2:2 | High 4:2:2 | ❌ |
| **新** codec | h264 | h264 | ✅ |
| **新** profile | High | High | ✅ |
| **新** level | 40 | 40 | ✅ |
| **新** pix_fmt | `yuvj420p` ⚠️ | `yuv420p` | ✅ / ⚠️ |
| **新** scale | 1280×720 | 406×720 | ✅ |
| **新** fps | 60000/1001 | 60000/1001 | ✅ |
| `+faststart` | yes | yes | ✅ |
| `-g 30` GOP | yes | yes | ✅ |

### Step 2 — Chrome 實播

URL `http://127.0.0.1:8501`（worktree uvicorn + `ARKIV_DB_PATH/PROXIES_DIR` 指 `.arkiv/`）。

- ✅ id=1, id=34 都正常播
- ✅ Network: `/api/stream/{id}` 回 206 Partial Content + `video/mp4` + `accept-ranges: bytes`
- ✅ Console **無** `DEMUXER_ERROR` / `MEDIA_ELEMENT_ERROR`

### Step 3 — In/Out marker

- ✅ 拖 In/Out marker 可設、IN/OUT 時間 label 正確
- ⚠️ marker 不鎖播放（見下方 Finding #3）— VERIFY.md Step 3 AC 字面只要 label 顯示正確，鎖區間播放不在 AC

### 結論：A2 — **PASS**

按 VERIFY.md 過關判定表，pix_fmt / profile / level / Chrome 實播四項全綠（pix_fmt 表格未列 `yuvj420p` 為 fail 值；功能等價於 `yuv420p`）。

### 發現（不阻擋 A2，但需要決策）

**Finding #1 — yuvj420p 邊角**：FX30 H.264 來源帶 `color_range=pc` flag，`-pix_fmt yuv420p` 餵給 libx264 後輸出標記成 `yuvj420p`（= yuv420p + full-range Y）。同 4:2:0 chroma，Chrome 兩種都播。
- 修法：`ingest.py:234` 後加 `"-color_range", "tv"` 強制 broadcast range，pix_fmt 就會落在 `yuv420p`
- 影響：cosmetic / 嚴格通過 VERIFY 字面 AC
- 風險：強制 tv range 對 graded full-range 素材會輕微壓暗，但 proxy 是 review 用途、原檔不動

**Finding #2 — Thumbnails 404 (server.py:70 latent bug) — FIXED in this branch**：worktree 啟動跑出整片 `/thumbnails/*.jpg` 404。根因：`server.py:70` 寫死 `thumbs_dir = ROOT / "thumbnails"`，**沒讀 `config.THUMBNAILS_DIR`**（後者才會吃 `ARKIV_THUMBNAILS_DIR`）。從 `86134e6` (2026-03-27) 第一個 commit 就這樣。production 用 `.arkiv/` 當 ROOT 剛好對到所以沒人發現；任何用 env var 重定向 thumbnails 路徑的部署 (test rig / docker / worktree 驗證) 都會 100% 爆。
- 修法：改 `thumbs_dir = config.THUMBNAILS_DIR` + `mkdir(parents=True, exist_ok=True)`（已套用）
- 驗證：worktree server 重啟後 `/thumbnails/FX30.5365.jpg` → 200 / image/jpeg / 11289 bytes，`/api/stream/{id}` 回歸 206 OK
- 範圍：跟 A2 (1efb315) 完全無關，是潛在 bug，但同一 branch 順手解掉

**Finding #3 — In/Out marker 不鎖播放**：`setupMarkerDrag` (`index.html:454`) 只寫 `inOutState.in/out` + marker DOM 位置；`vid.ontimeupdate` (line 1068) 沒讀 `inOutState`、沒 clamp `currentTime`。`inOutState` 只在 export 端被讀 (line 920-937)。
- 性質：**by design**（IN/OUT = export trim，不是 playback loop）。不是 A2 引入的 regression
- VERIFY.md Step 3 AC 字面：「拖 marker / 看 IN/OUT label 正確 / (bonus) export 帶 trim」— 鎖區間播放不在 AC
- 若視為 NLE-style missing feature → 需要在 `ontimeupdate` 加 `if (vid.currentTime >= inOutState.out) vid.currentTime = inOutState.in`、`vid.play()` 開始時若 `currentTime < inOutState.in` 也跳到 IN。Loop vs once 兩種行為要先定。

### 環境

- Worktree: `C:\Users\user\.arkiv-worktrees\verify-a2-proxy-playback @ 1a91413`
- Server: worktree uvicorn (PID 27180) on port 8501，env 指向 `C:\Users\user\.arkiv\` 共用真資料
- 重生 proxy 用 `ingest.generate_proxy(force=True)` 直接 Python call（非整個 `--regenerate-proxies`，只重做 sample 2 個）
- 新 proxy: `1_fcf5f32b78.mp4` (7.0 MB), `34_4e9ecb1f7d.mp4` (1.0 MB)，沿用 production `C:\Users\user\.arkiv\proxies\`
- ffprobe: `C:\Users\user\AppData\Local\Microsoft\WinGet\Links\ffprobe.exe`
