# arkiv Roadmap

最後更新：2026-04-18 · branch `claude/continue-fixes-SY9Eh`

這個檔案是**未完成事項的單一來源**。已發布功能看 `CHANGELOG.md`，已交付的 phase 計畫看 `docs/phase8-handover.md`。

---

## 完成度速覽

| Phase | 內容 | 狀態 | 證據 |
|---|---|---|---|
| 1-7 | MVP + Whisper + vision + search + plugin + proxy | ✅ | CHANGELOG v0.1.0 / v0.2.0 |
| 8.0 | DIT 絕對→相對路徑 | ✅ | commit `f384e72` |
| 8.2 | 智慧取幀 + 品質分析 + AI UI | ✅ | commit `b7f8c33` |
| 8.3 | `--refresh` re-ingest | ✅ | 同上 |
| 9 | Custom Vocabulary + Filter Dictionary | ✅ | `transcribe.py:128-133, 265-277` |
| 10 | WhisperX word-level timestamps | ✅ | `_transcribe_whisperx` + `words_json` 欄位 + `/remotion-props` |

**所有文件化 phase 都已完成。** 以下是未文件化的剩餘工作。

---

## A. 需要實機才能驗證（不會凍結，等下一次有對應環境的 session）

| # | 項目 | 阻礙 | 來源 |
|---|---|---|---|
| A1 | Tauri folder dialog AC 5/6：證明資料夾選擇器實際彈出 + 取消後再點不閃退 | 需 Windows 桌面 GUI automation（PowerShell 座標法被前景視窗搶焦干擾） | `CODEX_RESULT.md` REVIEW |
| A2 | Chrome 播放 trimmed proxy（驗 312b627 的 `yuv420p`/profile 修正） | 需 Windows + Chrome | 本輪 |
| A3 | DaVinci Resolve 匯入 trimmed EDL：source TC 是否正確讀為 01:00:10:00 | 需 DaVinci Resolve | 本輪 ba7373b |
| A4 | Final Cut Pro 匯入 trimmed FCPXML：`asset-clip start` 是否定位正確 source frame | 需 FCP | 同上 |
| A5 | `ARKIV_PROXIES_DIR` env round-trip：ingest 寫到 override 位置 → server stream 從同位置取 | 需設環境變數啟動 server 跑一輪 | 本輪 59f986d |
| A6 | Tauri WKWebView 中 In/Out marker 拖曳手感（`touch-action: none` 觸控行為） | 需 Tauri app 互動 | 本輪 13af833 |
| A7 | DaVinci Resolve plugin 搜尋一次後無回應 root cause | 需 Resolve GUI 重現 + Fusion script debugger | `.claude/handover-current-status.md:28` |

---

## B. 小型可在 Linux 完整實作 + 驗證（下一輪可挑）

| # | 項目 | 範圍 | 備註 |
|---|---|---|---|
| B1 | Marker I/O 鍵盤快捷鍵（在 playhead 位置設 in/out，像 NLE） | `index.html` 約 30 行 | 對 `inOutState` 加 keydown listener，讀 `vid.currentTime` |
| B2 | Marker 拖曳中顯示 tooltip 當前時間 | `index.html` 約 20 行 | `setupMarkerDrag` 的 move 函式裡加浮動 div |
| B3 | 擴張 `ingest.VIDEO_EXT` 到 `.mkv/.avi/.webm`（目前這些檔 ingest 進 DB 但跳過 thumbnail/frames/vision） | `ingest.py:25` 一行 + 驗證 ffmpeg 對這些容器的 thumbnail 抽取 | 4886ebb 的對齊修正只動了 filter，這是源頭 |
| B4 | `--regenerate-thumbnails` CLI（對應 `--regenerate-proxies`） | `ingest.py` 約 20 行 | 利用既有 `extract_thumbnail` + `extract_frames` |
| B5 | health.py 加 waveforms/proxies 目錄狀態 + size 顯示 | `health.py` 約 15 行 | 跟 cache_info 對齊（f25194e） |
| B6 | `_regenerate_proxies` 完成時印出總 size delta | `ingest.py:_regenerate_proxies` 約 5 行 | UX 優化，知道省/增多少 MB |
| B7 | `/api/stream` 對 HEVC 但無 proxy 的檔案回 409 + JSON 訊息（讓前端 surface「需建 proxy」狀態） | `server.py:stream_media` 約 10 行 | 目前 silently 送原檔，Chrome 報錯但前端只看到「無法播放」 |
| B8 | watch.py 改 in-process 呼叫 `ingest.process_file`，避免每檔 subprocess 重熱模型 | `watch.py` 重構約 40 行 | 大幅加速多檔到位場景 |

---

## C. 中等 scope（需研究 + 設計）

| # | 項目 | 阻礙 | 來源 |
|---|---|---|---|
| C1 | Phase 7.6 CSV Metadata Import for DaVinci | 需研究 DaVinci CSV import 規範（欄位名、編碼、分隔符） | `.claude/handover.md` Phase 7.6 |
| C2 | Tag → Smart Bin auto-assignment in DaVinci plugin | 需 Fusion API + DaVinci Smart Bin filter rule API | `.claude/handover-current-status.md` |
| C3 | Marker 範圍應用到 Remotion props（目前 `/remotion-props` 全片，不吃 trim） | 跟 trim export 同源頭，但 word-level 需 rebase | 本輪 ba7373b 衍生 |
| C4 | `--file` 顯式 CLI 參數（取代當前 `--dir <single_file>` 的隱式行為） | 需要 deprecation 路徑或破壞性變更 | d184ee0 衍生 |

---

## D. 大型 scope / 跨平台 / 需 packaging

| # | 項目 | 阻礙 |
|---|---|---|
| D1 | Tauri sidecar：用 PyInstaller 打包 server.py 為單一執行檔，由 Tauri 啟動 | 需 Windows + Mac 各自打包驗證；PyInstaller 對 whisperx/torch 大型 deps 的相容性 |
| D2 | DaVinci Resolve plugin 重寫成 native panel（取代 Fusion script UI） | 需 Resolve Workflow Integration SDK；可能解掉 A7 的 search crash |
| D3 | 多用戶 / 多專案 隔離（目前 `media.db` + `chroma_db` 全域） | 需 schema 重設計 + migration |

---

## E. 技術債（不急但累積會痛）

| # | 項目 | 影響 |
|---|---|---|
| E1 | `frames.py` thumbnail 用檔名 stem 命名 — 兩個目錄各有同名 `clip.mp4` 會互相覆蓋 | 多來源混合素材有靜默資料損失 |
| E2 | `vision.OLLAMA_URL` 在 import 時計算 — runtime 改 `config.OLLAMA_URL` env var 不會傳播 | `ARKIV_OLLAMA_URL` 動態切換不生效 |
| E3 | `vectordb.build_doc_text` 在 transcript 已存在時也算 frame_tags JSON parse | 微小浪費 CPU |
| E4 | 兩處 vision fallback 模型不一致：ingest 用 `minicpm-v:latest`、`/retry-vision` 用 `moondream2:latest` | 結果品質可能不同 |
| E5 | `index.html` 1500+ 行單檔 — 拆模組會大改但對未來維護有助 | 維護負擔 |
| E6 | 廢欄位 `frame_tags` 仍在寫入（已有 `frames` 表 + `description`/`tags` 欄） | DB 體積膨脹、雙寫一致性風險 |

---

## 工作優先級建議

1. **下一輪先挑 B 區**（Linux 可完整驗證）：B1（marker 快捷鍵）+ B5（health 補檢查） 各 30 分以內
2. **A1-A6 等到有對應環境的 session 一次清掉**（手動 GUI 測試 + 截圖貼進 CODEX_RESULT）
3. **C1（CSV import）值得做**，因為 plugin 寫 SetMetadata 已知失效（`.claude/handover-current-status.md:21`），CSV 是已知唯一可行方案
4. **D 區先放著**，等核心穩定再考慮
5. **E 區累積到 5+ 項再排專門 cleanup sprint**

---

## 維護規則

- 完成項目 → 移到 `CHANGELOG.md`，從這裡刪除
- 新發現的問題 → 加進對應 section（A=需實機 / B=小可做 / C=中等 / D=大 / E=技術債）
- 每次 session 結束如果動到 roadmap，更新檔頭日期
