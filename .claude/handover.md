# Handover: Mac → PC（2026-04-01）

## 本次完成（Mac 端）

- **DB 對齊** ✅ — `~/.arkiv/media.db`（61 筆）複製到 `~/Desktop/arkiv/media.db`
- **Frames 重建** ✅ — 58 支影片 × 3 = 171 frames，`frames` table 已填充
- **Server 驗證** ✅ — uvicorn :8501 啟動，API `/api/media/{id}` 回傳 frames 正常
- **Web UI 驗證** ✅ — Media Pool 61 筆顯示正確，Frame Analysis 卡片正常
- **DaVinci Plugin** — 已安裝至 `~/Library/.../Scripts/Utility/arkiv_resolve.py`，未實測（需開 Resolve）

## PC 端待辦

### DaVinci Resolve Plugin 測試
1. 確認 `arkiv_resolve.py` 在 `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\`
2. 啟動 arkiv server（`python server.py` 或 `uvicorn server:app --port 8501`）
3. 開 DaVinci Resolve → Workspace → Scripts → arkiv_resolve
4. 測試：搜尋 → 選取 → Import to Media Pool
5. 測試：GOOD Only 篩選 → Import
6. 驗證：imported clips 在 timeline 可正常拖入播放

### Phase 5 剩餘任務

| Task | 狀態 | 說明 |
|------|------|------|
| 5.0a Config 統一 | 🟡 | `vision.py` L6-7、`frames.py` L7 仍硬編碼，需改 import config |
| 5.0b Vision JSON 輸出 | 🟡 | LLM prompt 仍要求自由文字，需改 JSON schema |
| 5.4 health.py 補 ExifTool | 🟡 | 缺 ExifTool 檢查 |
| 5.8 vulture-s/arkiv repo | ⬜ | 目前推在 ourladypeace2011-commits，需轉移或建新 repo |
| 5.A ExifTool metadata | ⬜ | ingest.py 加 `exiftool -json` 步驟 |
| 5.B 自適應取幀 | ⬜ | frames.py 改 4 級策略 |
| 5.C Vision 用途分類 | ⬜ | vision.py 加 content_type |
| 5.D 專有名詞提示 | ⬜ | transcribe.py 加 --initial-prompt |
| 5.E 過濾詞庫 | ⬜ | transcribe.py 加 filter dictionary |

### 已知問題
- **Server 重啟才讀到 frames** — frames 重建後需重啟 uvicorn 才能從 API 回傳（SQLite 連線快取）
- **Waveform 假資料** — `waveformBars()` 固定高度陣列
- **In/Out markers** — 靜態顯示，不可拖曳

## 跨平台陷阱
| 陷阱 | Mac | PC |
|------|-----|-----|
| Whisper | mlx-whisper | faster-whisper (CUDA) |
| Path separator | `/` | `\`（thumbUrl + shortenPath 已處理） |
| ffprobe encoding | UTF-8 | cp950（已用 `encoding='utf-8'` 修正） |
| CSS theming | CSS vars `:root` / `:root.dark` | 同 |

## PC 同步
```bash
cd <arkiv-repo>
git pull
# DB 不在 git 裡，PC 端應有自己的 media.db
# 若需重建 frames：
python -c "
import db, frames as frm
db.init_db()
for rec in db.get_all_records():
    if rec['ext'] in ('.mp4','.mov','.m4v','.mts') and rec.get('duration_s',0) > 0:
        mid = rec['id']
        if db.get_frames(mid): continue
        fd = frm.extract_frames(rec['path'], rec['duration_s'], rec['fps'] or 30)
        for f in fd: db.upsert_frame(mid, f['index'], f['timestamp_s'], f.get('thumbnail_path'))
        print(f'{rec[\"filename\"]}: {len(fd)} frames')
"
```

## Tags
- **Mac snapshot**: `mac-snapshot-20260331`
- **PC snapshot**: `pc-snapshot-20260331`
- **Current HEAD**: `c1f7a93`（Mac 領先 origin 3 commits，push 後對齊）
