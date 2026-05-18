# A2 — Chrome 播 trimmed proxy（yuv420p / High L4.0）

**AC**：Windows Chrome 手動播驗 1efb315（原 hash 312b627）— yuv420p / profile high / level 4.0
**Branch**：`verify-a2-proxy-playback` ← `origin/main @ 1a91413`

## 修了什麼

`1efb315 fix(proxy): force yuv420p + tighter GOP for browser-playable proxies`

Root cause：`libx264` 預設沿用輸入 pixel format。ProRes / HEVC 10-bit 源產出 yuv422p / yuv444p / yuv420p10le，Chrome HTML5 decoder 全拒播。

`ingest.py:230-240` 強制 codec 參數：
```
-c:v libx264 -preset fast -crf 28
-profile:v high -level:v 4.0
-pix_fmt yuv420p
-g 30
-vf scale=-2:720
-c:a aac -b:a 128k
-movflags +faststart
```

`-g 30` = GOP 1s（取代 libx264 default 250），<8s clip 原本只有單一 keyframe seek 不順。
`+faststart` 把 moov atom 搬前面，瀏覽器邊下邊播。
`2d13a3f` 把 `server._build_proxies` 改成 lazy import `ingest.generate_proxy`，UI「建 proxy」按鈕跟 CLI 共用同一 source of truth。

## 啟動（PowerShell）

```powershell
cd C:\Users\user\.arkiv-worktrees\verify-a2-proxy-playback
python -m uvicorn server:app --host 127.0.0.1 --port 8501
```

## 準備測試素材

需要至少一個會觸發 proxy 的源檔：**ProRes / HEVC / 10-bit / 非 yuv420p**。確認手邊有再開測。

若已 ingest 過但 proxy 是舊的（無 yuv420p 強制）：
```powershell
python ingest.py --regenerate-proxies
```

若無 ingest 過：
```powershell
python ingest.py "<path-to-test-clip>"
```

## 驗證步驟

### Step 1 — ffprobe 驗 codec 參數

```powershell
$proxy = "C:\Users\user\.arkiv\proxies\<media_id>_<hash>.mp4"  # 替換實際檔名
ffprobe -v error -select_streams v:0 `
  -show_entries stream=codec_name,profile,level,pix_fmt,r_frame_rate `
  -of default=nw=1 $proxy
```

**預期輸出**：
```
codec_name=h264
profile=High
level=40              # = level 4.0
pix_fmt=yuv420p
r_frame_rate=<source fps>
```

✅ 三項都對 → 通過 Step 1。

### Step 2 — Chrome 實播驗證

開 Chrome → `http://localhost:8501` → 點該 clip → player 區應自動載入 proxy。

**預期**：
- ✅ 影片播放、有畫面、有音
- ✅ 可拖 timeline seek
- ✅ DevTools Network：`/api/stream/<id>` 200 + `video/mp4`
- ✅ DevTools Console 無 `MEDIA_ELEMENT_ERROR` / `DEMUXER_ERROR`

### Step 3 — Trimmed playback（In/Out marker）

1. Inspector waveform 拖 In marker（左）→ Out marker（右）設一個 trim 區段
2. 播放確認區段範圍正確顯示 IN / OUT 時間 label
3. （可選）按 Export CSV / Export EDL 看 trim 是否帶進輸出（A2 主要驗 codec，這步是 bonus）

## 過關判定

| 項目 | 通過 | 失敗 |
|------|------|------|
| ffprobe pix_fmt | `yuv420p` | 其他值（422 / 444 / 420p10le）|
| ffprobe profile | `High` | Main / Baseline / High 10 |
| ffprobe level | `40`（=4.0）| 41+ / 其他 |
| Chrome 實播 | 動 | DEMUXER_ERROR / 黑畫面 |

任一失敗 → 記下：源檔 codec（`ffprobe` 源檔）+ proxy 實際 codec + Chrome error → 寫進 CODEX_RESULT.md「未過項」

## 完工後

```powershell
cd C:\Users\user\.arkiv
git worktree remove ../.arkiv-worktrees/verify-a2-proxy-playback
git branch -D verify-a2-proxy-playback
```
