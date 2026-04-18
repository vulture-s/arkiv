# Codex Agent Instructions — arkiv
<!-- AGENTS.md v3 | 2026-04-11 | Phase 8 handover -->

## 專案 Context
arkiv 是影片素材管理工具（Tauri + Python backend），功能包含：
- Whisper 語音轉錄 + 4 層防幻覺 Guard
- Vision 分析（qwen3-vl）
- ChromaDB 語義搜尋
- DaVinci Resolve / FCPX 插件整合
- SRT/VTT/EDL/FCPXML export

## Environment Constraints
- **語法相容 Python 3.9+** — 禁用 match/case、`str | None`、`list[dict]` 等 3.10+ 語法。測試可用任何 >= 3.9 版本跑
- **Tauri WebView** = WKWebView — 不是 Chrome，CSS/JS 相容性要驗證
- **HTML/CSS**: 修改後必須驗證所有 tag 正確關閉
- **跨平台**: PC (RTX 4070) + Mac (M2 Max) 都要能跑
- **測試**: `smoke-test.sh`（8 項）+ `health.py`（20+ 驗證）

## Debugging Protocol
1. **Root Cause 優先** — 沒找到原因就不准動手修
2. **Pattern Analysis** — 找 codebase 中同類正常範例比對
3. **三振出局** — 同一問題 3 次不同方法仍失敗 → 停下來，輸出已嘗試方法 + 建議下一步
4. **只修 root cause** — 不用 workaround 掩蓋

## Verification Gate
宣稱「完成」前必須通過：
1. 指出哪個指令/測試/輸出證明宣稱
2. 實際執行（不是回憶上次結果）
3. 檢查 exit code、失敗數、錯誤訊息
4. 禁止用「should work」「probably fixed」「seems fine」「Done!」「Perfect!」

## Test-Driven Verification
- 改之前：先確認測試覆蓋變更範圍
- 改之後：跑 `python health.py` + 相關 smoke test，全綠才能 commit
- UI 變更：驗證 HTML tag 配對、CSS 在 WKWebView 生效

## Git 規範
- commit message 寫「為什麼」不只是「什麼」
- 多個獨立修改 → 拆成每個一個 commit
- 升級 package 前：讀 changelog、列 breaking changes、失敗就 rollback

## 輸出風格
- 簡潔直接，先結論後理由
- 用表格做比較和摘要
- 用 `file_path:line_number` 格式引用程式碼位置
- 不要用 emoji 除非被要求

## 編碼規則
- **所有檔案必須是 UTF-8** — commit 前確認中文可讀，不能有亂碼
- 如果工具輸出亂碼，**停下來回報**，不要把亂碼寫進檔案

## 交接路徑（必做，不可跳過）
完成任務後，**必須**在專案根目錄建立或更新 `CODEX_RESULT.md`，這是 CC 審計的入口。缺少此檔案視為交付不完整。

內容：
1. 完成了什麼（用 checkbox 逐項列出）
2. 測試結果（完整 health.py / smoke-test 輸出，不是摘要）
3. 有疑慮的項目（`⚠️ REVIEW:` 標記）
4. 未完成的項目（如有）
5. 與 spec 不一致的實作決策（如有，說明為什麼偏離）

## 審計協定
- 你的產出會經過 Claude Code 審計，不會直接合併
- commit 前跑完 Verification Gate，附上測試結果
- 有疑慮的實作用 `⚠️ REVIEW:` 標記，不要藏
- 審計發現重大缺失 → 此 AGENTS.md 會被更新補強規則

## Pre-Commit Checklist（每次 commit 前逐項確認）
| # | 檢查項 | 說明 |
|---|--------|------|
| 1 | `settings.local.json` 未被 staged | `git diff --cached --name-only \| grep settings.local` 必須為空 |
| 2 | `.env` / credentials 未被 staged | 同上，grep `.env` / `credentials` / `api_key` |
| 3 | 狀態欄位未被擅自修改 | 不得修改任何文件中的「狀態: Draft/Final」「Status:」等欄位，除非 handover 明確指示 |
| 4 | 依賴版本未被降級 | `pip list` 核心 package（torch/transformers/whisperx）版本 ≥ handover 指定版本 |
| 5 | Python 3.9 語法相容 | 無 match/case、`str \| None`、`list[dict]` |
| 6 | health.py 全綠 | 0 FAIL |

**源自**：W15 三次同類問題（settings.local.json 被 commit、狀態欄被改、torch 被降版）

## Don't
- 不要硬編碼 IP、API key、密碼
- 不要加不必要的功能、註解、docstring、type annotation
- 不要用 workaround 掩蓋問題
- 不要在沒有測試驗證的情況下宣稱「已修好」
- 不要改 config.py 的預設閾值（除非明確指示）

---

## 當前狀態

Phase 1-10 全部完成（看 `CHANGELOG.md` + `docs/phase8-handover.md`）。

**Product roadmap / 未完成事項 SSoT** 在 hevin-ai-os（本機私有）：
- `~/Desktop/hevin-ai-os/references/plans/arkiv/arkiv-roadmap.md` — 完整 phase-level roadmap + 技術債 + 微型任務清單 + 需實機驗證錨點
- `~/Desktop/hevin-ai-os/references/project-logs/arkiv/dev-log.md` — 時序開發紀錄

挑工作前先讀 hevin-ai-os 的 roadmap，完成後更新該檔 + 本 repo 的 `CHANGELOG.md`。新發現的問題加到主 roadmap 對應 section。
