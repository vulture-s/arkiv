# arkiv

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](https://python.org)
[![Tauri](https://img.shields.io/badge/Tauri-Desktop_App-FFC131.svg)](https://tauri.app)

**本地優先的媒體素材管理器，支援語義搜尋。**

使用 AI 轉錄與向量搜尋，瀏覽、搜尋、評級、標記你的影音素材。DaVinci Resolve 風格的深色介面。

> 🌐 [English](README.md) | **繁體中文**

---

## 架構

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│  index.html │◄──►│  server.py   │◄──►│   db.py      │
│  (Tailwind) │    │  (FastAPI)   │    │  (SQLite)    │
└─────────────┘    └──────┬───────┘    └─────────────┘
                          │
                   ┌──────┴───────┐
                   │  embed.py    │◄──► ChromaDB
                   │  (Ollama)    │     (nomic-embed-text)
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
  │（擷取幀）│  │(qwen3-vl:8b) │
  └─────────┘  └──────────────┘
```

## 截圖

![ARKIV UI](screenshot.jpg)

## 功能特色

- **語義搜尋** — 用自然語言查詢（中文／英文／日文）
- **AI 轉錄** — Whisper large-v3-turbo + Silero VAD + LLM 潤稿（Apple Silicon MLX / NVIDIA CUDA）
- **四層反幻覺防護** — VAD 靜音過濾 → no_speech 門檻 → 空白/重複過濾 → LLM 校正
- **幀分析** — qwen3-vl:8b 視覺描述，含品牌/物件辨識
- **兩階段管線** — 先轉錄、卸載 LLM、再視覺分析（避免 12GB 顯卡 VRAM 衝突）
- **評級系統** — GOOD / NG / 待審，含備註 + Resolve 片段上色
- **標籤系統** — 自動（AI）+ 手動標籤，附自動補全
- **DaVinci Resolve 風格 UI** — 深色主題、三欄式佈局、膠卷條、波形圖
- **匯出** — SRT、VTT、TXT、EDL（DF/NDF 時間碼）、FCPXML 1.8（FCPX + DaVinci 相容）
- **Tauri 原生應用** — 桌面應用程式，支援原生檔案/資料夾對話框
- **DaVinci Resolve 外掛** — 搜尋、匯入（含片段顏色）、新增幀標記

## 快速開始

### 前置需求
- Python 3.9+
- FFmpeg 6.0+
- Ollama 搭配 `nomic-embed-text` 模型

### 安裝

```bash
git clone https://github.com/vulture-s/arkiv.git
cd arkiv
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows (PowerShell)
pip install -r requirements.txt

# 安裝 Whisper 後端（擇一）：
pip install mlx-whisper          # macOS Apple Silicon
pip install faster-whisper torch  # NVIDIA GPU
pip install faster-whisper        # 純 CPU

# 下載 Ollama 模型
ollama pull nomic-embed-text
ollama pull qwen3-vl:8b    # 視覺幀描述
ollama pull qwen2.5:14b    # LLM 轉錄潤稿

# 檢查環境
python health.py
```

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
python ingest.py --dir ./media --limit 10   # 只處理前 10 個檔案
python ingest.py --dir ./media --skip-vision # 跳過 AI 幀描述
python ingest.py --dir ./media --refresh     # 重新處理已索引的檔案

# 索引選項
python embed.py --rebuild                    # 刪除並重建索引

# 自動監看資料夾
python watch.py /path/to/footage
python watch.py ~/Movies/rushes --interval 10

# API 搜尋（需要 server 運行中）
# Linux / macOS / Git Bash
curl "http://localhost:8501/api/media?q=關鍵字&limit=5"
# Windows PowerShell
Invoke-RestMethod "http://localhost:8501/api/media?q=關鍵字&limit=5"
```

</details>

### Docker

```bash
docker compose up -d
# 開啟 http://localhost:8501
```

## 設定

複製 `.env.example` 為 `.env` 並自訂：

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `ARKIV_DB_PATH` | `./media.db` | SQLite 資料庫路徑 |
| `ARKIV_CHROMA_PATH` | `./chroma_db` | ChromaDB 向量庫 |
| `ARKIV_THUMBNAILS_DIR` | `./thumbnails` | 縮圖輸出目錄 |
| `ARKIV_OLLAMA_URL` | `http://localhost:11434` | Ollama API 端點 |
| `ARKIV_EMBED_MODEL` | `nomic-embed-text` | 嵌入模型 |
| `ARKIV_VISION_MODEL` | `qwen3-vl:8b` | 視覺模型（幀描述） |
| `ARKIV_WHISPER_MODEL` | `mlx-community/whisper-large-v3-turbo` (macOS) / `large-v3-turbo` (其他) | Whisper 模型 |
| `ARKIV_EXIFTOOL_PATH` | *（空 — 自動偵測）* | exiftool 路徑（選用） |
| `ARKIV_HOST` | `0.0.0.0` | 伺服器綁定位址 |
| `ARKIV_PORT` | `8501` | 伺服器埠號 |

## 技術架構

| 層級 | 技術 |
|------|------|
| 前端 | Tailwind CSS + 原生 JS |
| 後端 | FastAPI + Uvicorn |
| 資料庫 | SQLite（詮釋資料）+ ChromaDB（向量） |
| 嵌入 | Ollama nomic-embed-text（768d, cosine） |
| 轉錄 | mlx-whisper / faster-whisper (large-v3-turbo) |
| VAD | Silero VAD（Whisper 前的靜音過濾） |
| LLM 潤稿 | Ollama qwen2.5:14b（標點 + 錯字校正） |
| 視覺 | Ollama qwen3-vl:8b（品牌/物件辨識） |
| 媒體 | FFmpeg（探測、縮圖、幀擷取） |
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
影片：`.mp4`、`.mov`、`.mkv`、`.avi`、`.webm`、`.m4v`、`.mts`
音訊：`.wav`、`.mp3`、`.m4a`、`.aac`、`.flac`、`.ogg`

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
| nomic-embed-text | 必要 | 必要 | 必要 | |
| qwen3-vl:8b | 選用 | 選用 | 選用 | 幀描述用 |
| qwen2.5:14b | 選用 | 選用 | 選用 | 轉錄潤稿用 |
| ExifTool | 選用 | 選用 | 選用 | 豐富詮釋資料 |
| faster-whisper | 必要 | 選用 | 必要 | CUDA/CPU whisper |
| mlx-whisper | — | 必要 | — | 僅 Apple Silicon |
| NVIDIA GPU | 選用 | — | — | |
| Apple Silicon | — | 必要 | — | |
| fastapi + uvicorn | 必要 | 必要 | 必要 | |

### 最新結果 (v0.2.0)

| 平台 | 環境健檢 | 冒煙測試 | 日期 |
|------|----------|----------|------|
| macOS M2 Max | 18/19 PASS, 0 FAIL, 1 SKIP | 9/9 PASS | 2026-04-03 |
| Windows 11 (RTX 4070) | 17/18 PASS, 0 FAIL, 1 SKIP | 9/9 PASS | 2026-04-02 |
| Linux (Docker) | 14/17 PASS, 0 FAIL, 3 SKIP | 9/9 PASS | 2026-04-01 |

## 授權

MIT
