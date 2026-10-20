---
version: 1
slug: "site-src-layouts-ledger-astro"
primary_target: "site/src/layouts/Ledger.astro"
related_targets: ["site/src/pages/how-it-works.astro","site/src/pages/cases.astro","site/src/pages/limits.astro","site/src/pages/demo.astro","site/src/pages/privacy.astro"]
---

# Surface brief — arkiv 內頁（對照雙欄）

Scope: 共用內頁版型 `Ledger.astro`，供 運作方式（how-it-works）／案例（cases）／誠實清單（limits）／Demo（demo）／Privacy（privacy）五頁使用。Mode: Read。
Audience: 小型影視團隊與個人創作者，在「要不要裝」的評估當下，帶著具體問題進來（會不會外傳素材？我的機器跑得動嗎？它到底怎麼找到那顆鏡頭？）。
Job: 掃左欄找到自己的問題，在右欄拿到答案與證據。
Proof/content: 全部來自 repo 與 code 實查；客戶與案場名不得出現；沒有 demo 影片、推薦語、對外 benchmark 圖 —— 不捏造。
Constraints: 視覺世界沿用首頁（home.css tokens），不改 DESIGN.md；中文段落原始碼不折行。

## Direction contract

THESIS: 每一頁是一本場記本：左欄記讀者會問的問題，右欄記答案與它的證據，一行一行對齊。拒絕的預設是「標題＋三張卡片＋CTA」的行銷內頁，以及一大段散文把答案埋在中間。

OWN-WORLD: 首頁的深色底 #0a0a0c、#f3f2ee 墨色、單一青色 #18b6dc；Archivo Black 只用在頁標題；Inter／Noto Sans TC 內文；JetBrains Mono 只用在證據行（版本、檔名、實測數字）。行與行之間 1px #26262a 細線，沒有卡片、沒有色塊框。狀態用記號：做得到 ✓（青）、做不到 ✕（紅 #e2686d）、有條件 △（墨色）。

STORY: 讀者知道 arkiv 怎麼運作、在哪裡跑、做不到什麼、資料去哪；相信這些是查過的而不是行銷話；然後去下載或去讀安裝說明。

FIRST VIEWPORT: 導覽列；左對齊的大標題（Archivo Black，clamp 40–64px）與一句 ≤40 字的開場回答；下方立刻開始雙欄：左欄 1/3 寬是問題（16–17px，墨色，半粗），右欄 2/3 寬是答案（16px，68ch 內）與一行 mono 證據；每列上方一條細線。首屏至少看得到兩列。右上角在標題列旁是本頁的「最後實查日期」。手機變成問題在上、答案在下。

FORM: 對照雙欄（ledger columns），第二手的第 4 順位；seed key fdee6d3a（reroll 1）。signature interaction：左欄問題是錨點，滑到某列時左欄問題 sticky 在該列頂端，直到右欄答案讀完；URL hash 可直接分享到某一列。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
