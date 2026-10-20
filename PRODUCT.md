# Product

<!-- impeccable:product-schema 1 -->

> 2026-10-03 由 CC 從 repo 證據起草，**Hevin 2026-10-05 逐條確認定稿**。

## Platform

web

## Stack

Astro（`site/`），GitHub Actions build → GitHub Pages。Hevin 2026-10-03 選定。
文件頁直接讀 repo 的 `docs/*.md` 與 `CHANGELOG.md`，不複製。

## Users

小型影視團隊與個人創作者：沒有 DIT 崗位，收工後自己過檔、挑毛片、分類、轉字幕的人。
次要讀者是評估「要不要裝」的技術型 Mac 使用者，與想串 MCP／Resolve 的工具型使用者。

## Product Purpose

以本地 AI 模型建立影音素材庫：有畫面的分析畫面、有聲音的轉成逐字稿，兩邊都進索引，
之後用口語搜尋素材。流程向影視工業的 DIT 工作流程靠攏（收檔、編號、校驗、建索引）。
成功＝收工後不用把素材全部看過一遍就找得到要的那一顆。

## Positioning

- 本機優先：不依賴雲端服務、軟體不外傳素材與逐字稿。
- 檔案不移動：路徑式 ingest，素材留在原處。
- 時間碼對得上畫面：逐字稿點一句就跳到那一句真正的位置；逐格 IN／OUT（59.94p）。
- 無對白素材也搜得到：視覺模型自己寫場景描述與標籤。
- 中／日／英語意搜尋；DaVinci Resolve 原生外掛；360 素材（`.insv`／`.360`）進同一個庫。

## Operating Context

拍攝結束後的過片、挑毛片、分類場景、轉錄字幕。交棒給剪輯軟體走 EDL／FCPXML／SRT 匯出，
或 Resolve 外掛一鍵匯入 Media Pool。使用者常把素材放在外接碟或 NAS。

## Capabilities and Constraints

- macOS 僅 Apple Silicon；Windows x64。安裝檔已含 Python 後端，但仍需自行安裝 FFmpeg 與 Ollama。
- 安裝檔尚未數位簽章（macOS 右鍵打開、Windows SmartScreen 一次）。
- Windows 無 NVIDIA 時轉錄需設 `ARKIV_WHISPER_DEVICE=cpu`，處理時間以數倍計。
- 授權 PolyForm Perimeter 1.0.1（source-available，非 OSI 開源）：免費、商用不受限，
  唯一限制是不得開發與 arkiv 競爭的產品。
- 免費版 3 個專案；Pro NT$3,000 一次買斷（立凡科技有限公司銷售），無限專案＋跨專案聚合。
  1.1.0 前已在使用的安裝永久保留兩項。

## Brand Commitments

- 名稱小寫 `arkiv`，字標後接青色句點。現行官網：深色底、Archivo Black 字標、
  Inter／Noto Sans TC 內文、JetBrains Mono 標籤、單一青色強調（`#18b6dc`）。
- 中文段落在原始碼裡不折行（HTML 會把換行算成空白，中文句讀後會多出半形空隙）。
- 版號執行時向 GitHub 取，取不到就不顯示 —— 不寫死。
- 訂閱表單不在無法確認時顯示「訂閱成功」。

## Evidence on Hand

- 實際運作規模：1,506 支真實商業案件素材完整索引，其中 1,161 支無對白 B-roll，單張 RTX 4070。
- 產品畫面：`docs/img/arkiv-ui.webp`（3200px）。
- 設計稿：`docs/design/redesign-2026/`（8 張）。
- **不可捏造／不可公開**：客戶名稱與案場名不上公開頁面（arkiv #463 已把客戶名從 docstring 拿掉）。
  沒有 hero demo 影片（尚未拍）、沒有使用者推薦語、沒有對外 benchmark 圖表 —— 這些位置在補到真材料前留空，不用假資料填。

## Product Principles

1. 誠實優先於好看：做不到的寫出來（系統需求、未簽章、CPU 很慢），不藏在 FAQ 底下。
2. 失敗要出聲：靜默降級比明確失敗更糟（產品裡反覆出現的原則，官網也照做）。
3. 證據取代形容詞：用數字與實際畫面，不用「強大」「智慧」。
4. 本機與檔案主權是核心承諾，不是功能之一。

## Accessibility & Inclusion

繁體中文為主要語言，英文走 README。對比與鍵盤操作依 WCAG AA（Hevin 2026-10-04 確認；當天首頁灰字從 3.7:1 調到 5.8:1）。
