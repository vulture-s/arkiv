---
name: arkiv
description: 本機優先的影音素材管理工具官網：深色場記本，一個青色，證據用等寬字。
colors:
  bg: "#0a0a0c"
  surface: "#111114"
  surface-2: "#17171a"
  rule: "#26262a"
  rule-hi: "#3a3a3f"
  ink: "#f3f2ee"
  ink-2: "#b6b5b0"
  quiet: "#6a6a6d"
  lg-dim: "#8a8a8e"
  invert: "#f3f2ee"
  invert-ink: "#0a0a0c"
  cyan: "#18b6dc"
  no-red: "#e2686d"
typography:
  display:
    fontFamily: "'Archivo Black','Inter',sans-serif"
    fontSize: "clamp(52px,11vw,92px)"
    lineHeight: 0.92
    letterSpacing: "-.03em"
  ledger-title:
    fontFamily: "'Archivo Black','Noto Sans TC',sans-serif"
    fontSize: "clamp(40px,6.4vw,64px)"
    fontWeight: 900
    lineHeight: 1
    letterSpacing: "-.03em"
  lede:
    fontFamily: "'Inter','Noto Sans TC',system-ui,sans-serif"
    fontSize: "clamp(22px,4vw,34px)"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-.02em"
  ledger-lede:
    fontFamily: "'Inter','Noto Sans TC',system-ui,sans-serif"
    fontSize: "clamp(18px,2.4vw,21px)"
    lineHeight: 1.6
  headline:
    fontFamily: "'Inter','Noto Sans TC',system-ui,sans-serif"
    fontSize: "clamp(20px,3.2vw,26px)"
    fontWeight: 600
    letterSpacing: "-.01em"
  group:
    fontFamily: "'Inter','Noto Sans TC',system-ui,sans-serif"
    fontSize: "clamp(21px,2.6vw,24px)"
    fontWeight: 700
    letterSpacing: "-.01em"
  question:
    fontFamily: "'Inter','Noto Sans TC',system-ui,sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "-.005em"
  body:
    fontFamily: "'Inter','Noto Sans TC',system-ui,sans-serif"
    fontSize: "16px"
    lineHeight: 1.75
    fontFeature: "'ss01','cv11','tnum'"
  evidence:
    fontFamily: "'JetBrains Mono',ui-monospace,monospace"
    fontSize: "12.5px"
    lineHeight: 1.6
    fontFeature: "tabular-nums"
  label-mono:
    fontFamily: "'JetBrains Mono',ui-monospace,monospace"
    fontSize: "12px"
    letterSpacing: ".12em"
rounded:
  code: "4px"
  crop: "6px"
  control: "10px"
  shot: "12px"
  panel: "14px"
  pill: "999px"
spacing:
  gutter: "24px"
  row-gap: "48px"
  row-pad: "28px 0 32px"
  section: "72px"
  wrap: "960px"
  doc-wrap: "760px"
components:
  button-primary:
    backgroundColor: "{colors.invert}"
    textColor: "{colors.invert-ink}"
    rounded: "{rounded.control}"
    padding: "13px 22px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "13px 22px"
  badge:
    textColor: "{colors.ink-2}"
    typography: "{typography.label-mono}"
    rounded: "{rounded.pill}"
    padding: "5px 13px"
  input:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "13px 16px"
  nav-link:
    textColor: "{colors.ink-2}"
  ledger-question:
    textColor: "{colors.ink}"
    typography: "{typography.question}"
  ledger-answer:
    textColor: "{colors.ink-2}"
    typography: "{typography.body}"
    width: "68ch"
  ledger-evidence:
    textColor: "{colors.lg-dim}"
    typography: "{typography.evidence}"
  pin:
    backgroundColor: "{colors.cyan}"
    textColor: "{colors.invert-ink}"
    rounded: "{rounded.pill}"
    size: "26px"
---

# Design System: arkiv

## Overview

**Creative North Star: "The Script Supervisor's Notebook"**

arkiv 的官網是一本攤開在剪輯台旁的深色場記本：近黑的底、暖白的墨、一支青色筆。首頁用一個巨大的 Archivo Black 字標加一句回答開場；內頁（運作方式、實際畫面、案例、誠實清單、隱私）全部是同一種形狀 —— 左欄是讀者會問的問題，右欄是答案與一行等寬字證據，一列一列用 1px 細線隔開。

密度偏讀物而非行銷頁：段落 62–68ch、行高 1.7–1.75、區塊之間大量留黑。強調只靠一個青色（`cyan`）與墨色層級完成，沒有漸層、沒有插圖、沒有色塊卡片堆疊。證據（版本、檔名、實測數字）一律等寬字，讓「查過的事實」在視覺上與散文分開。

本系統由已上線的程式碼記錄而來（`site/src/styles/home.css`、`site.css`、`ledger.css`）；PRODUCT.md 仍是草稿，這裡只採用它與實作一致的品牌承諾（小寫字標接青色句點、單一青色、中文段落原始碼不折行）。

**Key Characteristics:**
- 深色單一地面，三階表面只差 3–6% 明度。
- 一個強調色；紅色只表示「做不到」。
- 細線分隔取代卡片（內頁）；首頁保有少數幾個帶框面板。
- 等寬字 = 證據與標籤，不是裝飾。
- 中文優先排版：`word-break:keep-all` 加作者手動標的 `<wbr>` 斷句點。

## Colors

近黑帶極微冷調的中性色階，配一個飽和的青色與一個只用於否定的珊瑚紅。

### Primary
- **Notebook Cyan** (`cyan`): 唯一的強調色。連結、字標句點、「做得到」記號、目前頁的導覽底線、被分享列的頂線、demo 圖釘、證明區左邊線、焦點外框、文字選取。

### Tertiary
- **Strike Coral** (`no-red`): 只用於「做不到」記號與表單錯誤訊息。目前在程式碼中是寫死的字面值（`#e2686d`），尚未成為 CSS 變數。

### Neutral
- **Lamp-off Black** (`bg`): 全站地面。
- **Desk** (`surface`) / **Desk Raised** (`surface-2`): 首頁授權面板、程式碼區塊、證明區、輸入框、demo 圖框底。
- **Hairline** (`rule`): 列與列、區段與區段之間的 1px 線，以及截圖外框。
- **Hairline Strong** (`rule-hi`): 群組標題上緣、ghost 按鈕與徽章外框、步驟列表左軸、上下頁頂線。
- **Paper Ink** (`ink`): 標題、問題、粗體、主要文字。
- **Pencil** (`ink-2`): 內文、答案段落、導覽連結、「有條件」記號（17.7:1 與 9.6:1 對地面）。
- **Ledger Dim** (`lg-dim`): 內頁所有可讀的小字：證據行、實查日期、圖說、圖例、上下頁小標（5.8:1）。宣告於 `.lg` 範圍，不在 `:root`。
- **Graphite** (`quiet`): 3.7:1，只夠當線與非文字元素；見下方規則。
- **Invert / Invert Ink** (`invert`, `invert-ink`): 實心按鈕與圖釘的反白組合，數值同 `ink` 與 `bg`。

### Named Rules
**The One Pen Rule.** 只有一支彩色筆。青色標記「可操作」或「是」；不要引入第二個強調色，紅色不是強調而是否定。

**The Readable Gray Rule.** 任何要被閱讀的小字至少用 `lg-dim`（5.8:1）。`quiet`（3.7:1）不得再用於新文字。

## Typography

**Display Font:** Archivo Black（CJK 頁標題後接 Noto Sans TC 900）
**Body Font:** Inter（後接 Noto Sans TC、system-ui）
**Label/Mono Font:** JetBrains Mono

**Character:** 粗黑的展示字只出現在字標與頁標題，像蓋章；內文是安靜的 Inter／思源黑體；等寬字承載所有可以被查證的東西。

### Hierarchy
- **Display** (Archivo Black, clamp(52px,11vw,92px), 0.92): 首頁字標 `arkiv.`，僅此一處。
- **Ledger Title** (Archivo Black → Noto Sans TC 900, clamp(40px,6.4vw,64px), 1, `text-wrap:balance`): 內頁頁標題，後接青色句點。
- **Lede** (Inter 600, clamp(22px,4vw,34px), 1.3): 首頁字標下的一句回答。內頁版本為 clamp(18px,2.4vw,21px)／1.6、墨色、52ch。
- **Headline** (600, clamp(20px,3.2vw,26px)): 首頁區段標題。
- **Group** (700, clamp(21px,2.6vw,24px)): 內頁一串列之間的群組標題。
- **Question** (600, 17px, 1.5): 內頁左欄問題。
- **Body** (400, 16px, 1.75, 68ch): 內頁答案；首頁段落 15px／1.7、62–72ch。
- **Evidence** (JetBrains Mono, 12.5px, 1.6, tabular): 每列答案下的證據行。
- **Label Mono** (JetBrains Mono, 11–12px): 表頭（uppercase, .12em）、徽章、按鈕副標、文件索引小字。

### Named Rules
**The Stamp Rule.** Archivo Black 只用於字標與頁標題。它沒有中文字形，所以中文標題的字體堆疊必須是 `'Archivo Black','Noto Sans TC',sans-serif` 且字重 900，否則會掉到系統字 400。

**The Evidence-in-Mono Rule.** 版本號、檔名、指令、環境變數、實測數字用等寬字；散文不用。

**The Phrase Break Rule.** 中文標題與問題用 `word-break:keep-all`，可斷點由作者在詞組邊界手動插入 `<wbr>`（Row 元件的 `|`），中文詞不得被拆開。中文段落在原始碼不折行。

## Layout

單欄容器 960px（文件頁 760px），左右 24px 邊距。首頁是縱向區段堆疊，區段之間 72px 上下留白與 1px `rule` 底線（手機 56px）。

內頁是對照雙欄：左欄問題 1fr、右欄答案 2fr，欄距 48px，每列上方 1px 細線、內距 28px／32px。左欄問題 `position:sticky; top:76px`，讀答案時問題停在該列頂端。每列有 id，問題本身是自連結，URL hash 可直接分享到某列（`scroll-margin-top:72px`）。頁頭為標題＋右側「對照原始碼實查」日期，下接一句開場回答；頁尾是上一頁／下一頁兩格。

響應式只有一個斷點 760px（首頁另有 600px 調整）：內頁雙欄變成問題在上、答案在下，問題不再 sticky；頂部導覽在手機不 sticky（換行後會遮住太多畫面）；上下頁變單欄。

### Named Rules
**The Ledger Rule.** 內頁一律是問題／答案／證據的列，不是「標題＋三張卡片＋CTA」。新增內頁內容時加列，不加卡片。

## Elevation & Depth

扁平。深度靠三階表面明度與 1px 細線表達，不靠陰影。唯一的陰影在 demo 圖釘上，讓它浮在截圖之上而可辨識。

### Shadow Vocabulary
- **Pin lift** (`box-shadow: 0 2px 8px rgba(0,0,0,.55)`): 只用於疊在截圖上的編號圖釘。

導覽目前頁的青色底線與被分享列的青色頂線用 `inset` box-shadow 畫線，那是線條不是高度。

### Named Rules
**The Hairline Rule.** 分隔用 1px `rule`；需要更強的分隔（群組、軸線、上下頁）升到 `rule-hi`，不加陰影、不加底色塊。

## Shapes

直角為主的版面，元件小圓角：程式碼 4–5px、截圖裁切 6px、按鈕／輸入框／圖框／文件索引 10px、首頁產品截圖 12px、授權面板 14px、徽章與圖釘全圓。內頁的列與群組沒有框也沒有圓角，只有上緣細線。

## Components

### Buttons
- **Shape:** 圓角 10px。
- **Primary:** 反白實心（`invert` 底、`invert-ink` 字），600 15px，內距 13px 22px；可疊一行等寬小字副標（11.5px、62% 不透明）。
- **Hover:** 上移 1px、不透明度 .9，.12s ease。
- **Ghost:** 透明底、`ink` 字、1px `rule-hi` 框。

### Chips (Badges)
- **Style:** 等寬 12px、`ink-2` 字、1px `rule-hi` 框、全圓角，關鍵字以 `cyan` 粗體。只在首頁英雄區。

### Cards / Containers
- 首頁專用：授權面板（`surface` 底、1px `rule` 框、14px 圓角、32px 內距）、證明區（`surface-2` 底、左 2px `cyan` 邊線、右側 10px 圓角）。內頁不使用。

### Inputs / Fields
- **Style:** `surface-2` 底、1px `rule-hi` 框、10px 圓角、13px 16px、15px 字。
- **Focus:** 2px `cyan` 外框、偏移 1px。
- **Error:** 狀態文字 `no-red`；成功 `cyan`。不確定時不顯示成功。

### Navigation
- 頂部列 56px 高、`bg` 底、底線 `rule`，桌面 sticky。左邊字標（Archivo Black 20px 接青色句點），右邊連結 14px `ink-2`，hover／目前頁轉 `ink`，目前頁加 2px 青色內底線。

### Ledger Row（signature）
- 左欄：可選狀態記號或圖釘編號 ＋ 問題自連結（hover 底線 `rule-hi`，focus 2px 青色外框）。
- 右欄：答案（`ink-2`，粗體轉 `ink`），可選原尺寸 demo 裁切圖（6px 圓角、1px 框），最後一行等寬證據。
- 被分享（`:target`）時頂線轉青，並播一次 .9s 的青色淡入；`prefers-reduced-motion` 時關閉。

### Status Marks
- 20px 繪製 SVG、線寬 1.8、圓頭：做得到＝勾（`cyan`）、做不到＝叉（`no-red`）、有條件＝三角（`ink-2`）。各自帶 `aria-label`。需要時以圖例列（`lg-key`）說明。

### Demo Pins
- 截圖上的 26px 青色圓形編號（手機 20px），hover／對應列啟用時放大 1.25 並轉 `ink`，曲線 cubic-bezier(.16,1,.3,1)。對應列左欄是 24px 青框圓形編號，啟用時填滿。

### Step List
- 答案內的有序步驟：左側 1px `rule-hi` 縱軸，每步一顆 7px 空心青色節點，步驟號欄寬 4.5em。

## Do's and Don'ts

### Do:
- **Do** 用問題／答案／證據的列來寫內頁，每列有 id 並可用 hash 分享。
- **Do** 狀態用 Mark 元件的繪製 SVG（勾／叉／三角），配 `aria-label`。
- **Do** 內頁小字用 `lg-dim`；證據行用 JetBrains Mono 12.5px。
- **Do** 中文標題用 `'Archivo Black','Noto Sans TC',sans-serif` 900，並在詞組邊界標 `<wbr>`。
- **Do** 讓青色保持唯一強調；紅色只表示否定或錯誤。
- **Do** 動態只用在狀態回饋（按鈕、圖釘、被分享列），並尊重 `prefers-reduced-motion`。

### Don't:
- **Don't** 在內頁用卡片、色塊框或「標題＋三張卡片＋CTA」。
- **Don't** 用 `quiet`（#6a6a6d，3.7:1）排可讀文字。
- **Don't** 用 ✓✕ 等字元當狀態圖示；用繪製 SVG。
- **Don't** 在原始碼裡折行中文段落。
- **Don't** 加入陰影來表現層次；圖釘是唯一例外。
