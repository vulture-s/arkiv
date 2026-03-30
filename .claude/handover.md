# Handover: arkiv Tauri App — 靜態 CSS 修復完成

## Current State (2026-03-30)
- **JS 執行** ✅
- **Toolbar** ✅
- **Media Grid 版面** ✅ — CSS 修復後 grid、卡片、SVG 圖示全部正常
- **Web UI 驗證** ✅ — PC 端瀏覽器 http://mac:8501 確認正常

## What Was Fixed
1. **16+ CSS class 小數點未跳脫** —  → （根本原因）
2. **缺少 CSS reset** — 加了 box-sizing, margin:0, img/svg max-width
3. **foundation vars 未初始化** — --tw-ring-*, --tw-gradient-*
4. **.border 預設色錯誤** — currentColor → var(--color-panel-border)
5. **.transition 用 all** — 改為指定屬性列表
6. **冗餘 dark mode override** — 刪除 10 條 no-op

## Pending
1. **Tauri 桌面 app 測試** —  驗證 WKWebView 渲染
2. **移除 _diag() 診斷覆蓋層** — index.html 中的 debug code
3. **Push to remote** — git push

## Key Files
| File | Status |
|------|--------|
| tailwind-static.css | ✅ 修復完成 (261 行) |
| index.html | 待清理 _diag() |
| server.py | OK |

## Environment
- Backend: uvicorn on port 8501 (running)
- Tauri: cargo tauri dev
- Python: 3.9
- DB: ~/.arkiv/media.db (8 筆素材)

## Mac 開發效率指南（從 PC 端 debug session 學到的）

### 核心原則：Chrome first, Tauri last
1. 開發時用 `uvicorn server:app --reload`（自動重載，不快取）
2. Chrome localhost:8501 + F12 做 90% debug
3. Safari localhost:8501 測相容性
4. `WEBKIT_INSPECTOR=1 cargo tauri dev` 只用於最終驗證

### 排錯順序
Chrome OK → Safari OK → Tauri WebInspector

### 已修復的 Mac 陷阱
- server.py HTML 快取已移除（dev mode 每次重讀）
- tailwind-static.css 小數點跳脫已修正
- withGlobalTauri:true 已設定（dialog API 可用）
- var isTauri 避免 Tauri inject 衝突
