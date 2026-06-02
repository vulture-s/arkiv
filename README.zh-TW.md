# arkiv

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](https://python.org)
[![Tauri](https://img.shields.io/badge/Tauri-Desktop_App-FFC131.svg)](https://tauri.app)

**DIT 工作流的開源 AI 素材標註層 — Resolve 原生、CJK 優先。**

> 🌐 [English](README.md) | **繁體中文**

arkiv 介於素材硬碟與 DaVinci Resolve 之間：自動 ingest footage、附上 AI 標註（逐字稿、視覺標籤、氛圍、能量、剪輯位置），並用任何語言（中文、日文、英文）的語義搜尋找回 clip。Resolve plugin 讓你搜尋、帶 clip color 匯入、加 frame marker，不用離開 NLE。

為 solo DIT 與小團隊設計，資料自己持有：本地優先、自架、MIT license、零雲端依賴。

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
  │（擷取幀）│  │(qwen3-vl:8b) │
  └─────────┘  └──────────────┘
```

→ **完整 pipeline（4 階段、儲存路徑、exit code、maintenance modes）**：[docs/pipeline.zh-TW.md](docs/pipeline.zh-TW.md)

## 截圖

![ARKIV UI](screenshot.jpg)

## 功能特色

- **語義搜尋** — 用自然語言查詢（中文／英文／日文）
- **素材庫 Chat RAG** — 5-intent 助手支援素材清單搜尋、延伸篩選、相似鏡頭、統計與一般問答，並保留對話記憶
- **AI 轉錄** — Whisper large-v3-turbo + Silero VAD + LLM 潤稿（Apple Silicon MLX / NVIDIA CUDA）
- **四層反幻覺防護** — VAD 靜音過濾 → no_speech 門檻 → 空白/重複過濾 → LLM 校正
- **幀分析** — qwen3-vl:8b 視覺描述，含品牌/物件辨識
- **兩階段管線** — 先轉錄、卸載 LLM、再視覺分析（避免 12GB 顯卡 VRAM 衝突）
- **評級系統** — GOOD / NG / 待審，含備註 + Resolve 片段上色
- **標籤系統** — 自動（AI）+ 手動標籤，附自動補全
- **DaVinci Resolve 風格 UI** — 深色主題、三欄式佈局、膠卷條、波形圖
- **匯出** — SRT、VTT、TXT、EDL（DF/NDF 時間碼）、FCPXML 1.8（FCPX + DaVinci 相容）
- **DaVinci Resolve 詮釋資料 CSV 匯出** — `/api/export/metadata-csv` 端點輸出片段詮釋資料（Camera／Lens／ISO／Shutter／Aperture／GPS／CreateDate），可直接餵 Resolve 的「檔案 → 從 CSV 匯入詮釋資料」。外掛匯入後自動提示
- **ExifTool 整合** — 每支片段自動擷取 12 個欄位（Make／Model／LensModel／GPS／ColorSpace／ISO／Shutter／Aperture／FocalLength／CreateDate）。支援 sidecar：Sony XAVC `.XML`、iPhone Keys group、Blackmagic Cam app 廠商專屬鏡頭標籤。Windows 自動偵測 exiftool 二進位位置（winget／scoop／chocolatey／Program Files）
- **EDL reel 名** — 採 ExifTool ReelName，缺失時 fallback 到檔名 stem（8 字元 CMX3600 規格相容、控制字元已過濾）
- **HEVC／ProRes 瀏覽器代理** — 瀏覽器播放時依需求自動產生 H.264 代理（Phase 7.7g）
- **Tauri 原生應用** — 桌面應用程式，支援原生檔案/資料夾對話框（Windows panic hook 將 Rust crash 寫到 stderr）
- **DaVinci Resolve 外掛** — 搜尋、匯入（含片段顏色）、新增幀標記
- **ASC MHL v2 雜湊清單** — `mhl.py create` / `verify` CLI 產出真正的 `urn:ASC:MHL:v2.0` 格式，支援 `xxh3` / `md5` / `sha1` / `sha256` / `c4`，含 directory + structure root hash、鏈式 `ascmhl_chain.xml`。已跟 ASC 官方 reference impl 1.2 互通驗證 — 可直接接 Silverstack / MediaVerify / Hedge / YoYotta 工作流
- **多目的地 offload** — `offload.py --src <SD> --dst <A> --dst <B>` chunked 平行 copy + 每檔 hash 驗證 + mismatch 3× retry + atomic rename + sidecar 感知（XAVC / ARRI / RED / iPhone Live Photo）。可恢復的 JSON state file — copy 一半 kill 掉，pending 檔案下次接著跑。每個 dst 結尾 emit MHL v2
- **攝影日報 CSV** — `camera_report.py` 產 20 欄 DIT 規格 CSV（Reel / TC / Camera / Lens / ISO / Shutter / Aperture / WB / FPS / Codec / ...），可直接餵 Resolve 的「檔案 → 從 CSV 匯入詮釋資料」。Day-summary footer 統計片段數 + 時長（依攝影機 / 依記憶卡）

## API 驗證

所有 `/api/*` 端點都需要帶有正確 scope 的 Bearer token。這種以 scope 為基礎的 token 可以把整個機器群組拆開管理：只讀審片機可用 `videos_read` 或 `media_read`，匯入機可用 `ingest_write`，管理機可用 `admin`。

第一次啟動時先做 bootstrap：

```bash
export ARKIV_ADMIN_BOOTSTRAP_TOKEN=$(openssl rand -base64 32)
python server.py
```

第一次啟動時，server 會用這個 env var 建立一組 `admin` token。先用它建立各機器專用 token，之後再移除該 env 並撤銷 bootstrap token。

直接用 CLI 建立與管理 token：

```bash
python arkiv_token.py create --name "PC-dev" --scopes videos_read,videos_write --ip-allowlist 127.0.0.1/32,100.64.0.0/10 --expires-in 90
python arkiv_token.py list
python arkiv_token.py show <token-id>
python arkiv_token.py revoke <token-id>
```

在請求中使用 token：

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8501/api/media
```

可用 scopes：`videos_read`、`videos_write`、`media_read`、`collections_read`、`collections_write`、`projects_read`、`projects_write`、`ingest_write`、`chat_read`、`chat_write`、`admin`

### Chat API — 素材庫 RAG 問答

你可以用自然語言詢問素材庫。分類器會把每個 prompt 交給五個 handler 其中之一：

| Intent | 範例 | 做什麼 |
|--------|------|--------|
| `compilation` | 「給我五月所有黃昏鏡頭」 | 語意搜尋 → 排序後的 scene 清單 |
| `refinement` | 「只要室內的」 | 在對話中對*上一輪結果*再篩選 |
| `similarity` | 「找跟 scene 42 類似的」 | 對參考鏡頭做向量最近鄰 |
| `analytics` | 「這個月總共拍了幾小時？」 | 對素材庫做統計查詢 |
| `general` | 「你能幫我做什麼？」 | 純 LLM 問答，不查庫 |

對話歷史（最近 10 則）會帶入每次後續回應，所以 `refinement` 是對上一輪傳回的結果做篩選。

**模型需求**：chat 用 `ARKIV_CHAT_MODEL`（預設 `qwen2.5:14b`）同時處理*意圖分類與回答* —— 一個 `ollama pull qwen2.5:14b` 就夠。只有當較小模型（例如 `qwen2.5:7b-instruct`）確實已裝在 Ollama 主機上時，才設 `ARKIV_INTENT_MODEL`。模型缺失時 `/api/chat` 會回清楚的「請 ollama pull …」訊息而非 500。

**前置條件 —— 先 ingest + 建索引**：chat 查的是*已建索引*的素材庫，不是獨立聊天機器人。先 ingest 素材（Step 1）+ 跑 `python embed.py` 建索引（Step 2）再用 chat。`compilation` / `refinement` / `similarity` 需要向量索引；`analytics` 只要 ingest 過；`general` 是唯一空庫也能用的 intent。空庫 / 未建索引時 chat 不會報錯，只會回「找到 0 個」。

```bash
# 建立對話
curl -X POST http://localhost:8501/api/chat \
  -H "Authorization: Bearer $ARKIV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "給我所有黃昏鏡頭"}'
# → {"conversation_id":"…", "assistant_text":"…", "scene_ids":[…], "intent":"compilation", …}

# 延續同一個對話 —— refinement 會對上一輪結果操作
curl -X POST http://localhost:8501/api/chat \
  -H "Authorization: Bearer $ARKIV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "只要室內的", "conversation_id": "abc123"}'

# 把對話限定在特定 project
curl -X POST http://localhost:8501/api/chat -H "Authorization: Bearer $ARKIV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "寬景鏡頭", "project_scope": ["client-acme"]}'
```

用 `GET /api/chat/history/{conversation_id}` 讀回歷史、`GET /api/chat/conversations` 列出對話（都需要 `chat_read`）。

## 快速開始

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
ollama pull bge-m3 && ollama pull qwen3-vl:8b && ollama pull qwen2.5:14b
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
ollama pull bge-m3 && ollama pull qwen3-vl:8b && ollama pull qwen2.5:14b
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
ollama pull bge-m3; ollama pull qwen3-vl:8b; ollama pull qwen2.5:14b
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

### 從 v0.3.0 升級到 v0.3.1

v0.3.1 改了預設儲存 layout（產出檔案落 `BASE_DIR/.arkiv/` — 見 Phase 8.0c）。一鍵 migration：

```bash
cd ~/.arkiv && git pull && python ingest.py --migrate-storage
```

完整 SOP（backup、rollback、per-project layout）：[docs/pipeline.zh-TW.md#v030--v031-升級](docs/pipeline.zh-TW.md#v030--v031-升級) · [CHANGELOG v0.3.1](CHANGELOG.md)

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

## 設定

複製 `.env.example` 為 `.env` 並自訂：

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `ARKIV_DB_PATH` | `./media.db` | SQLite 資料庫路徑 |
| `ARKIV_CHROMA_PATH` | `./chroma_db` | ChromaDB 向量庫 |
| `ARKIV_THUMBNAILS_DIR` | `./thumbnails` | 縮圖輸出目錄 |
| `ARKIV_OLLAMA_URL` | `http://localhost:11434` | Ollama API 端點 |
| `ARKIV_EMBED_MODEL` | `bge-m3` | 嵌入模型 —— **建索引後請勿更換**（見下方說明） |
| `ARKIV_VISION_MODEL` | `qwen3-vl:8b` | 視覺模型（幀描述） |
| `ARKIV_CHAT_MODEL` | `qwen2.5:14b` | Chat 模型 —— 回答與（預設）意圖分類 |
| `ARKIV_INTENT_MODEL` | *(= `ARKIV_CHAT_MODEL`)* | 選用的較快意圖分類模型；必須已安裝 |
| `ARKIV_WHISPER_MODEL` | `mlx-community/whisper-large-v3-turbo` (macOS) / `large-v3-turbo` (其他) | Whisper 模型 |
| `ARKIV_EXIFTOOL_PATH` | *（空 — 自動偵測）* | exiftool 路徑（選用） |
| `ARKIV_HOST` | `0.0.0.0` | 伺服器綁定位址 |
| `ARKIV_PORT` | `8501` | 伺服器埠號 |

> **嵌入模型與索引綁定。** 向量庫是用單一嵌入模型（`bge-m3`，1024 維）建立的。索引建好後若更改 `ARKIV_EMBED_MODEL`，新查詢向量會跟既有向量不相容 —— 搜尋結果會靜默劣化。要換模型必須重建整個索引。
>
> **Chat 硬體門檻：** `qwen2.5:14b` 約需 9 GB，且與嵌入模型同時運行，請在 Ollama 主機預留約 12–16 GB 可用 RAM/VRAM。記憶體較緊的機器可設 `ARKIV_CHAT_MODEL=qwen2.5:7b`（約 4.7 GB）當較輕的預設。

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
| 視覺 | Ollama qwen3-vl:8b（品牌/物件辨識） |
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
| bge-m3 | 必要 | 必要 | 必要 | |
| qwen3-vl:8b | 選用 | 選用 | 選用 | 幀描述用 |
| qwen2.5:14b | 選用 | 選用 | 選用 | 轉錄潤稿 + chat（`/api/chat` 必需） |
| ExifTool | 選用 | 選用 | 選用 | 豐富詮釋資料 |
| faster-whisper | 必要 | 選用 | 必要 | CUDA/CPU whisper |
| mlx-whisper | — | 必要 | — | 僅 Apple Silicon |
| NVIDIA GPU | 選用 | — | — | |
| Apple Silicon | — | 必要 | — | |
| fastapi + uvicorn | 必要 | 必要 | 必要 | |

### 最新結果 (v0.3.0)

| 平台 | 環境健檢 | 冒煙測試 | 日期 |
|------|----------|----------|------|
| macOS M2 Max | TBD | TBD | 2026-05-22 |
| Windows 11 (RTX 4070) | 19/19 PASS, 0 FAIL, 0 SKIP | 9/9 PASS | 2026-05-22 |
| Linux (Docker) | 14/17 PASS, 0 FAIL, 3 SKIP | 9/9 PASS | 2026-05-22 |

## 授權

MIT
