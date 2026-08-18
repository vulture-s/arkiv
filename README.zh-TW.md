# arkiv

[![License: PolyForm Perimeter](https://img.shields.io/badge/License-PolyForm--Perimeter--1.0.1-orange.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](https://python.org)
[![Tauri](https://img.shields.io/badge/Tauri-Desktop_App-FFC131.svg)](https://tauri.app)

**DIT 工作流的 source-available AI 素材標註層 — Resolve 原生、CJK 優先。**

> 🌐 [English](README.md) | **繁體中文**

arkiv 介於素材硬碟與 DaVinci Resolve 之間：自動 ingest footage、附上 AI 標註（逐字稿、視覺標籤、氛圍、能量、剪輯位置），並用任何語言（中文、日文、英文）的語義搜尋找回 clip。Resolve plugin 讓你搜尋、帶 clip color 匯入、加 frame marker，不用離開 NLE。

為 solo DIT 與小團隊設計，資料自己持有：本地優先、自架、source-available（PolyForm Perimeter），零雲端依賴。

---

## 為什麼需要 arkiv

- **素材太多，找不到那顆鏡頭** → 用自然語言搜（「五月所有黃昏空景」），中日英都行，搜的是畫面內容和逐字稿，不是檔名。
- **AI 剪輯工具只看得懂「有人在講話」的素材** → arkiv 對每支 clip 做視覺分析 + 轉錄，大量 B-roll、無語音空景一樣可搜可管。
- **素材庫要能餵給下游任何剪法** → 手動剪、自動剪、腳本剪都接得上：Resolve 原生 plugin、EDL / FCPXML 匯出、API / MCP 介面。

> **授權一句話**：arkiv **任何用途免費，包含商業工作**（source-available），做出來的東西 **100% 是你的**。唯一不准的是把 arkiv 變成跟它競爭的產品。
>
> **實戰驗證**：已在 **1,506 支真實產品拍攝素材**（其中 1,161 支無對白 B-roll）、單張 RTX 4070 上完整索引跑通。

## 截圖

![ARKIV UI](screenshot.jpg)

<details>
<summary>系統架構與資料流（給 contributor / fork 作者）</summary>

### 架構

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│  index.html │◄──►│  server.py   │◄──►│   db.py      │
│  (Tailwind) │    │  (FastAPI)   │    │  (SQLite)    │
└─────────────┘    └──────┬───────┘    └─────────────┘
                          │
                   ┌──────┴───────┐
                   │  embed.py    │◄──► ChromaDB
                   │  (Ollama)    │     (bge-m3)
                   └──────────────┘

  ═══════════════ 匯入管線（兩階段）═══════════════

  階段 1：探測 + 轉錄 + LLM 潤稿
  ┌───────────┐ ┌─────────────┐ ┌──────────────┐
  │ ingest.py │→│transcribe.py│→│ qwen2.5:14b  │
  │ (FFmpeg)  │ │(Whisper+VAD)│ │（LLM 潤稿）  │
  └───────────┘ └─────────────┘ └──────────────┘
       │              ↑
       │         Silero VAD
       │       （靜音過濾）
       ▼
  階段 2：視覺分析（卸載 LLM 後釋放 VRAM）
  ┌─────────┐  ┌──────────────┐
  │frames.py│→ │  vision.py   │
  │（擷取幀）│  │(qwen2.5vl:7b) │
  └─────────┘  └──────────────┘
```

→ **完整 pipeline（4 階段、儲存路徑、exit code、maintenance modes）**：[docs/pipeline.zh-TW.md](docs/pipeline.zh-TW.md) · 架構總覽 [ARCHITECTURE.md](ARCHITECTURE.md)

</details>

## 功能總覽

**找素材**
- 語義搜尋（中／英／日）——搜的是畫面內容 + 逐字稿，不是檔名
- 素材庫 Chat：搜清單、對上一輪結果續篩、找相似鏡頭、統計問答（帶對話記憶）
- 評級（GOOD / NG / 待審）+ 自動與手動標籤，附自動補全

**AI 標註**
- Whisper large-v3-turbo 轉錄 + **四層反幻覺防護**（VAD 靜音過濾 → no_speech 門檻 → 空白/重複過濾 → LLM 校正）
- 幀視覺分析（含品牌／物件辨識）；360 素材（Insta360 / GoPro Max）自動重投影後索引
- 中文逐字稿存檔即轉台灣繁體（含既有素材的批次轉換工具）
- 相機 metadata 全吃：EXIF + Sony XAVC sidecar，FX 系列機型不會掉

**DIT 現場**
- 記憶卡轉存：多目的地平行 copy + 逐檔 hash 驗證 + 可斷點續傳，**絕不刪來源卡**
- ASC MHL v2 雜湊清單（已與 ASC 官方 reference impl 互通驗證）
- 攝影日報 CSV、插卡自動轉存監看、瀏覽器 DIT 控制台（`/dit`）

**進剪輯**
- DaVinci Resolve plugin：站內搜尋、帶 clip color 匯入、加 frame marker
- 匯出 SRT / VTT / TXT / EDL（DF/NDF）/ FCPXML 1.8、metadata CSV 直餵 Resolve
- 全部功能同時有 Web UI、CLI 與 API，共用同一個庫

<details>
<summary>完整功能清單（含進階選項）</summary>

- **語義搜尋** — 用自然語言查詢（中文／英文／日文）
- **素材庫 Chat RAG** — 5-intent 助手支援素材清單搜尋、延伸篩選、相似鏡頭、統計與一般問答，並保留對話記憶
- **AI 轉錄** — Whisper large-v3-turbo + Silero VAD + LLM 潤稿（Apple Silicon MLX / NVIDIA CUDA）
- **四層反幻覺防護** — VAD 靜音過濾 → no_speech 門檻 → 空白/重複過濾 → LLM 校正
- **幀分析** — qwen2.5vl:7b 視覺描述，含品牌/物件辨識
- **兩階段管線** — 先轉錄、卸載 LLM、再視覺分析（避免 12GB 顯卡 VRAM 衝突）
- **評級系統** — GOOD / NG / 待審，含備註 + Resolve 片段上色
- **標籤系統** — 自動（AI）+ 手動標籤，附自動補全
- **DaVinci Resolve 風格 UI** — 深色主題、三欄式佈局、膠卷條、波形圖
- **匯出** — SRT、VTT、TXT、EDL（DF/NDF 時間碼）、FCPXML 1.8（FCPX + DaVinci 相容）
- **DaVinci Resolve 詮釋資料 CSV 匯出** — `/api/export/metadata-csv` 端點輸出片段詮釋資料（Camera／Lens／ISO／Shutter／Aperture／GPS／CreateDate），可直接餵 Resolve 的「檔案 → 從 CSV 匯入詮釋資料」。外掛匯入後自動提示
- **ExifTool 整合** — 每支片段自動擷取 12 個欄位（Make／Model／LensModel／GPS／ColorSpace／ISO／Shutter／Aperture／FocalLength／CreateDate）。支援 sidecar：Sony XAVC `.XML`、iPhone Keys group、Blackmagic Cam app 廠商專屬鏡頭標籤。Windows 自動偵測 exiftool 二進位位置（winget／scoop／chocolatey／Program Files）
- **EDL reel 名** — 採 ExifTool ReelName，缺失時 fallback 到檔名 stem（8 字元 CMX3600 規格相容、控制字元已過濾）
- **HEVC／ProRes 瀏覽器代理** — 瀏覽器播放時依需求自動產生 H.264 代理
- **Tauri 原生應用** — 桌面應用程式，支援原生檔案/資料夾對話框
- **DaVinci Resolve 外掛** — 搜尋、匯入（含片段顏色）、新增幀標記
- **ASC MHL v2 雜湊清單** — `mhl.py create` / `verify` CLI 產出真正的 `urn:ASC:MHL:v2.0` 格式，支援 `xxh3` / `md5` / `sha1` / `sha256` / `c4`，含 directory + structure root hash、鏈式 `ascmhl_chain.xml`。已跟 ASC 官方 reference impl 1.2 互通驗證 — 可直接接 Silverstack / MediaVerify / Hedge / YoYotta 工作流
- **多目的地 offload** — `offload.py --src <SD> --dst <A> --dst <B>` chunked 平行 copy + 每檔 hash 驗證 + mismatch 3× retry + atomic rename + sidecar 感知（XAVC / ARRI / RED / iPhone Live Photo）。可恢復的 JSON state file — copy 一半 kill 掉，pending 檔案下次接著跑。每個 dst 結尾 emit MHL v2
- **攝影日報 CSV** — `camera_report.py` 產 20 欄 DIT 規格 CSV（Reel / TC / Camera / Lens / ISO / Shutter / Aperture / WB / FPS / Codec / ...），可直接餵 Resolve 的「檔案 → 從 CSV 匯入詮釋資料」。Day-summary footer 統計片段數 + 時長（依攝影機 / 依記憶卡）
- **DIT 轉存 UI（`/dit`）** — 瀏覽器控制台做記憶卡→備份轉存：預覽目的端排版、執行時**即時逐檔進度串流**、多目的地 + `xxh3` 校驗 + ASC MHL v2。**絕不刪來源卡**
- **轉存歸檔規則** — `offload.py --organize "{date}/{camera}/{reel}"` 把素材歸進 日期/攝影機/Reel 樹狀（token：`{date}/{camera}/{reel}/{stem}/{ext}`，檔名安全、防路徑逃逸）；留空則鏡射原結構
- **記憶卡監看** — `offload.py --watch` 插卡自動轉存（偵測 DCIM / 媒體卷宗），含重插 / 掛載抖動防護，晃動的卡不會重複複製
- **360 重投影** — 雙魚眼 `.insv` / `.360` 在 vision tagging 前重投影成**等距柱狀（equirectangular）**（FFmpeg `v360`），讓原始魚眼藏住的畫面文字與事件變得可搜尋
- **Vision 失敗容錯** — `ingest.py --max-failures N` / `--skip-failed` 容忍長時間無人值守時的零星幀 vision 失敗；失敗幀留空，之後可用 `--vision-only` 續跑（整個 Ollama 掛掉仍會快速停止）

</details>

## DaVinci Resolve 整合

arkiv 不只是「另一個素材管理器」——它直接活在你的 NLE 裡：

- **Resolve plugin**（`resolve_plugin/`）：在 Resolve 內搜尋 arkiv 素材庫、把結果**帶 clip color 匯入**時間軸、對片段加 frame marker，全程不離開 NLE。
- **評級直通**：arkiv 的 GOOD / NG / 待審會變成 Resolve 的片段顏色。
- **metadata CSV**：`/api/export/metadata-csv` 產出可直接餵 Resolve「檔案 → 從 CSV 匯入詮釋資料」的欄位（Camera／Lens／ISO／Shutter／Aperture／GPS／CreateDate），plugin 匯入後會自動提示。
- **時間軸交換**：EDL（DF/NDF 時間碼）與 FCPXML 1.8 匯出，FCPX 與 DaVinci 皆相容。

> macOS 上 Resolve 需要 python.org 官方 Python 3.10 Framework（見下方前置需求）。

## API / MCP

arkiv 的功能全部有 REST API（`/api/*`，scope-based Bearer token）與 read-only MCP server，
Web UI 只是其中一個使用者 —— 你可以用腳本、自動化流程、或接 Claude／OpenClaw 來查素材庫。

→ **[API 驗證、token scopes、素材庫 Chat（RAG）問答完整說明：docs/api.zh-TW.md](docs/api.zh-TW.md)**

## 快速開始

### 下載應用程式（macOS, Apple Silicon）

不想碰 Python 環境的最快路徑。`.dmg` 已打包 Python 後端與 ML 套件（torch、mlx-whisper、chromadb…），可略過下面的 venv/pip。但你**仍需 FFmpeg 與 Ollama**（負責抽幀、嵌入、視覺）：

```bash
brew install ffmpeg ollama
ollama pull bge-m3 && ollama pull qwen2.5vl:7b && ollama pull qwen2.5:14b
```

到 [最新 release](https://github.com/vulture-s/arkiv/releases/latest) 下載 **`arkiv_<version>_aarch64.dmg`**，打開後把 **arkiv** 拖進「應用程式」。首次啟動因未簽名會被 macOS Gatekeeper 擋 —— **右鍵 → 打開** 一次即可（或執行 `xattr -dr com.apple.quarantine /Applications/arkiv.app`），之後正常開啟。

> Intel Mac / Windows 目前沒有預建 app（bundle 內是 aarch64 Python + mlx-whisper）。請改用下面的原始碼安裝。

---

以下皆為**從原始碼**安裝與執行 —— 開發用，或跑在 Linux / Windows。

### 前置需求

| 依賴 | macOS (brew) | Linux (apt) | Windows |
|---|---|---|---|
| Python 3.9+ | `brew install python` | `sudo apt install python3 python3-venv` | [python.org](https://python.org) |
| FFmpeg 6.0+ | `brew install ffmpeg` | `sudo apt install ffmpeg` | [ffmpeg.org](https://ffmpeg.org/download.html) |
| Ollama | `brew install ollama` | [ollama.com/download](https://ollama.com/download) | [ollama.com/download](https://ollama.com/download) |

> **DaVinci Resolve Plugin 額外需求 (macOS)**：Resolve 需要 [python.org 官方 Python 3.10 Framework 安裝檔 (.pkg)](https://www.python.org/downloads/release/python-31011/) — Homebrew Python 不被識別。安裝路徑：`/Library/Frameworks/Python.framework/Versions/3.10/`。安裝後重啟 Resolve，Console 左下角應顯示 Py3，scripts 透過 Workspace > Scripts 載入。

### 安裝 — macOS (brew + pip)

```bash
brew install python ffmpeg ollama
git clone https://github.com/vulture-s/arkiv.git
cd arkiv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install mlx-whisper          # Apple Silicon (Metal GPU)
ollama pull bge-m3 && ollama pull qwen2.5vl:7b && ollama pull qwen2.5:14b
python health.py
```

### 安裝 — Linux (pip)

```bash
sudo apt install python3 python3-venv ffmpeg
git clone https://github.com/vulture-s/arkiv.git
cd arkiv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install faster-whisper torch  # NVIDIA CUDA GPU
# pip install faster-whisper      # CPU 後備
ollama pull bge-m3 && ollama pull qwen2.5vl:7b && ollama pull qwen2.5:14b
python health.py
```

### 安裝 — Windows (pip, PowerShell)

```powershell
# 先手動安裝 Python 3.9+、FFmpeg、Ollama，然後：
git clone https://github.com/vulture-s/arkiv.git
cd arkiv
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
pip install faster-whisper torch  # NVIDIA CUDA GPU
# pip install faster-whisper      # CPU 後備
ollama pull bge-m3; ollama pull qwen2.5vl:7b; ollama pull qwen2.5:14b
$env:PYTHONUTF8=1; python health.py
```

### 安裝 — Docker (跨平台)

```bash
git clone https://github.com/vulture-s/arkiv.git
cd arkiv
docker compose up -d
# 開啟 http://localhost:8501
```

> 模型會在 Ollama container 首次啟動時自動下載（可能需要幾分鐘）。

<details>
<summary>從舊版升級（v0.3.0 → v0.3.1 的儲存 layout 遷移）</summary>

v0.3.1 改了預設儲存 layout（產出檔案落 `BASE_DIR/.arkiv/`）。從那之前的版本升級才需要這步：

```bash
cd ~/.arkiv && git pull && python ingest.py --migrate-storage
```

完整 SOP（backup、rollback、per-project layout）：[docs/pipeline.zh-TW.md](docs/pipeline.zh-TW.md) · [CHANGELOG](CHANGELOG.md)

</details>

### 方式 A：Web UI — 在瀏覽器中瀏覽、搜尋、評級、標記

```bash
# macOS / Linux
uvicorn server:app --host 0.0.0.0 --port 8501

# Windows (PowerShell) — CJK 搜尋需要 UTF-8
$env:PYTHONUTF8=1; uvicorn server:app --host 0.0.0.0 --port 8501

# 開啟 http://localhost:8501 → 點 + 匯入媒體
```

### 方式 B：純 CLI — 不開瀏覽器也能匯入和搜尋

> 兩種方式共用同一個資料庫。你可以混合使用 — 用 CLI 匯入，再用 Web UI 瀏覽，反之亦然。
>
> **注意：** 請勿同時執行 CLI 和 Web UI 的匯入。SQLite 不支援並行寫入 — 請一次執行一個。

```bash
# 第 1 步 — 匯入你的媒體
python ingest.py --dir /path/to/media

# 第 2 步 — 建立搜尋索引
python embed.py

# 第 3 步 — 搜尋
python embed.py --search "戶外訪談"
```

<details>
<summary>進階 CLI 選項</summary>

```bash
# 匯入選項
python ingest.py --dir ./media --limit 10        # 只處理前 10 個檔案
python ingest.py --dir ./media --skip-vision     # 跳過 AI 幀描述
python ingest.py --dir ./media --refresh         # 重新處理已索引的檔案（會重抽幀）
python ingest.py --dir ./media --skip-failed     # 容忍零星幀 vision 失敗（無人值守過夜跑）
python ingest.py --dir ./media --max-failures 20 # 累計 20 個幀失敗才停 vision
python ingest.py --vision-only --dir ./media     # 續跑：只對留空的幀重跑 vision

# 索引選項
python embed.py --rebuild                    # 刪除並重建索引

# DIT 轉存（記憶卡 → 備份；絕不刪來源）
python offload.py --src /Volumes/CARD --dst /Volumes/Backup1 --dst /Volumes/Backup2
python offload.py --src /Volumes/CARD --dst /Volumes/Backup --organize "{date}/{camera}/{reel}"
python offload.py --watch --dst /Volumes/Backup # 插卡自動轉存

# 自動監看資料夾（匯入）
python watch.py /path/to/footage
python watch.py ~/Movies/rushes --interval 10

# API 搜尋（需要 server 運行中）
# Linux / macOS / Git Bash
curl "http://localhost:8501/api/media?q=關鍵字&limit=5"
# Windows PowerShell
Invoke-RestMethod "http://localhost:8501/api/media?q=關鍵字&limit=5"
```

</details>

## 設定

<details>
<summary>環境變數全表（`.env`）—— 預設值就能跑，要調再看</summary>

複製 `.env.example` 為 `.env` 並自訂：

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `ARKIV_DB_PATH` | `./media.db` | SQLite 資料庫路徑 |
| `ARKIV_CHROMA_PATH` | `./chroma_db` | ChromaDB 向量庫 |
| `ARKIV_THUMBNAILS_DIR` | `./thumbnails` | 縮圖輸出目錄 |
| `ARKIV_OLLAMA_URL` | `http://localhost:11434` | Ollama API 端點 |
| `ARKIV_EMBED_MODEL` | `bge-m3` | 嵌入模型 —— **建索引後請勿更換**（見下方說明） |
| `ARKIV_VISION_MODEL` | `qwen2.5vl:7b` | 視覺模型（幀描述）。**預設刻意用 2.5-VL 而非 qwen3-vl:8b**：Qwen3-VL 在 Ollama 下的視覺路徑約慢 10×（實測 ~60s/幀 vs ~8s/幀），2000 幀就是 30 小時 vs 3.5 小時、標註品質相當。要更高上限可設 `ARKIV_OLLAMA_VISION_MODEL=qwen3-vl:8b` |
| `ARKIV_CHAT_MODEL` | `qwen2.5:14b` | Chat 模型 —— 回答與（預設）意圖分類 |
| `ARKIV_INTENT_MODEL` | *(= `ARKIV_CHAT_MODEL`)* | 選用的較快意圖分類模型；必須已安裝 |
| `ARKIV_WHISPER_MODEL` | `mlx-community/whisper-large-v3-turbo` (macOS) / `large-v3-turbo` (其他) | Whisper 模型 |
| `ARKIV_CUSTOM_VOCABULARY` | *（空）* | 逗號分隔的熱詞（人名／術語），餵進 Whisper `initial_prompt` |
| `ARKIV_VOCABULARY_FILE` | *（空 → 有則用 `.arkiv/vocabulary.txt`）* | 換行分隔的詞庫檔（一行一詞、`#` 註解）；與上者合併 |
| `ARKIV_EXIFTOOL_PATH` | *（空 — 自動偵測）* | exiftool 路徑（選用） |
| `ARKIV_FFMPEG_PATH` | *（空 — 自動偵測）* | ffmpeg 路徑（選用；headless Windows 上 PATH 只有 WinGet alias shim 時可指定真實路徑） |
| `ARKIV_FFPROBE_PATH` | *（空 — 自動偵測）* | ffprobe 路徑（選用；同上） |
| `ARKIV_HOST` | `0.0.0.0` | 伺服器綁定位址 |
| `ARKIV_PORT` | `8501` | 伺服器埠號 |

> **嵌入模型與索引綁定。** 向量庫是用單一嵌入模型（`bge-m3`，1024 維）建立的。索引建好後若更改 `ARKIV_EMBED_MODEL`，新查詢向量會跟既有向量不相容 —— 搜尋結果會靜默劣化。要換模型必須重建整個索引。
>
> **Chat 硬體門檻：** `qwen2.5:14b` 約需 9 GB，且與嵌入模型同時運行，請在 Ollama 主機預留約 12–16 GB 可用 RAM/VRAM。記憶體較緊的機器可設 `ARKIV_CHAT_MODEL=qwen2.5:7b`（約 4.7 GB）當較輕的預設。

</details>

## 技術架構

| 層級 | 技術 |
|------|------|
| 前端 | Tailwind CSS + 原生 JS |
| 後端 | FastAPI + Uvicorn |
| 資料庫 | SQLite（詮釋資料）+ ChromaDB（向量） |
| 嵌入 | Ollama bge-m3（1024d, cosine） |
| 轉錄 | mlx-whisper / faster-whisper (large-v3-turbo) |
| VAD | Silero VAD（Whisper 前的靜音過濾） |
| LLM 潤稿 + Chat | Ollama qwen2.5:14b（轉錄潤稿 + 5-intent chat RAG） |
| 視覺 | Ollama qwen2.5vl:7b（品牌/物件辨識） |
| 媒體 | FFmpeg（探測、縮圖、幀擷取） |
| 詮釋資料 | ExifTool（12 欄位、sidecar-aware、跨平台自動偵測） |
| 匯出 | SRT、VTT、TXT、EDL（DF/NDF）、FCPXML 1.8 |
| 桌面 | Tauri（原生應用程式包裝） |
| NLE 外掛 | DaVinci Resolve（匯入 + 片段上色 + 標記） |

## 常見問題

**Q：該用哪個 Whisper 後端？**
- macOS Apple Silicon：`mlx-whisper`（最快，使用 Metal GPU）
- NVIDIA GPU：`faster-whisper` + `torch`（CUDA 加速）
- 純 CPU：`faster-whisper`（較慢但到處都能跑）

**Q：需要 Ollama 嗎？**
需要，語義搜尋（嵌入）和選用的幀描述都需要。啟動 arkiv 前先執行 `ollama serve`。

**Q：怎麼新增媒體？**
在媒體庫側邊欄點 `+` 按鈕，或從 CLI 執行 `python ingest.py --dir /path/to/media`。

**Q：不用 Docker 可以嗎？**
可以 — 原生 Python 安裝是主要的工作流程。Docker 是選用的部署方式。

**Q：支援哪些檔案格式？**
影片：`.mp4`、`.mov`、`.mkv`、`.avi`、`.webm`、`.m4v`、`.mts`、`.mxf`（Sony FX6／FX9／Venice 的 XAVC）
360：`.insv`（Insta360）、`.360`（GoPro Max）— 雙魚眼會在 vision tagging 前重投影成等距柱狀（equirectangular）（單鏡頭 360 素材則以原狀索引）
音訊：`.wav`、`.mp3`、`.m4a`、`.aac`、`.flac`、`.ogg`
相機 metadata（機型/鏡頭/timecode）同時讀內嵌 EXIF **與** Sony XAVC NRT sidecar XML — FX30／FX 系列素材機型不會掉。

## 冒煙測試

執行內建的冒煙測試來驗證你的環境：

```bash
# PC (Windows/macOS)
bash smoke-test.sh --platform pc

# Docker
docker exec arkiv-arkiv-1 bash smoke-test.sh --platform docker
```

測試分為兩個階段：**環境健檢**（Health Check）和 **API 冒煙測試**（Smoke Test）。

### SKIP 的意思

SKIP 項目是**選用的相依套件** — 不影響功能。通過的結果是 **0 FAIL**，不論 SKIP 數量。

| 檢查項目 | PC (Windows) | PC (macOS) | Docker | 備註 |
|----------|:---:|:---:|:---:|------|
| Python >= 3.9 | 必要 | 必要 | 必要 | |
| FFmpeg / ffprobe | 必要 | 必要 | 必要 | |
| Ollama server | 必要 | 必要 | 必要 | |
| bge-m3 | 必要 | 必要 | 必要 | |
| qwen2.5vl:7b | 選用 | 選用 | 選用 | 幀描述用 |
| qwen2.5:14b | 選用 | 選用 | 選用 | 轉錄潤稿 + chat（`/api/chat` 必需） |
| ExifTool | 選用 | 選用 | 選用 | 豐富詮釋資料 |
| faster-whisper | 必要 | 選用 | 必要 | CUDA/CPU whisper |
| mlx-whisper | — | 必要 | — | 僅 Apple Silicon |
| NVIDIA GPU | 選用 | — | — | |
| Apple Silicon | — | 必要 | — | |
| fastapi + uvicorn | 必要 | 必要 | 必要 | |

### 最新結果

| 平台 | 環境健檢 | 冒煙測試 | 日期 |
|------|----------|----------|------|
| Windows 11 (RTX 4070) | 19/19 PASS, 0 FAIL, 0 SKIP | 9/9 PASS | 2026-05-22 |
| Linux (Docker) | 14/17 PASS, 0 FAIL, 3 SKIP | 9/9 PASS | 2026-05-22 |

## 開發者資訊

[ARCHITECTURE.md](ARCHITECTURE.md)（架構總覽） · [docs/api.zh-TW.md](docs/api.zh-TW.md)（API + Chat） · [docs/pipeline.zh-TW.md](docs/pipeline.zh-TW.md)（完整 pipeline） · [CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md)

## 授權

PolyForm Perimeter License 1.0.1 — 見 [LICENSE](LICENSE)。

arkiv **任何用途皆免費，包含商業工作**：接案、工作室內部使用、有補助的製作都可以。你用它剪出的影片、時間軸、匯出檔都是你的，授權不加任何限制。唯一不允許的是**提供他人跟 arkiv 競爭的產品** —— 把它 fork 成對手素材管理工具、包成索引服務賣給第三方、或重新實作成替代品，即使免費提供也一樣。

界線在於**你的客戶買的是什麼**：買你用 arkiv 做出來的成品，是一般的工具使用；買 arkiv 的功能本身（當成產品、服務、函式庫或外掛），就構成競爭。

## Pro 附加元件

免費核心支援 **3 個專案**。選購的 **Pro 附加元件**（另一個閉源元件）解鎖無限專案與跨專案聚合（跨專案搜尋與精選集），**NT$3,000 一次買斷、終身有效**。無訂閱、無啟用伺服器、不對外回報。條款：[docs/pro-addon-license.md](docs/pro-addon-license.md)。

> 尚未開賣 —— 購買流程還在建。條款先公開，讓你在那之前就能讀。

## 公益方案

arkiv 任何用途都免費，所以公益工作使用核心不需要我們同意。這個方案給的是 **Pro 附加元件，免費且終身**，對象是有公共價值的影像工作：

- 公共議題紀錄片
- 非營利／公益團體的影像
- 地方記憶、口述歷史、檔案保存
- 公共教育與公共媒體

素材的知識層，不該只有大製作用得起。

**申請方式**：開一個 [GitHub Issue](https://github.com/vulture-s/arkiv/issues) 標題加上 `[public-interest]`，或 IG 私訊 [@vulture.s](https://www.instagram.com/vulture.s/)，簡述你的專案。逐案人工審，不做自動資格表。資格例示、申請範例與逐案裁量說明見 [PUBLIC-INTEREST.md](PUBLIC-INTEREST.md)。

## 聯絡與追蹤

- 開發實錄與 demo：Threads / Instagram [@vulture.s](https://www.instagram.com/vulture.s/)
- 問題回報與功能許願：[GitHub Issues](https://github.com/vulture-s/arkiv/issues)
- 商業合作／導入諮詢：IG 私訊，或開 Issue 標題加 `[biz]`
