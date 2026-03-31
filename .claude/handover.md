# Handover: arkiv PC 端功能修復 + Frame Analysis

## Current State (2026-03-31, PC → Mac)

### 本次修復
- **Thumbnail 路徑修復** ✅ — `thumbUrl()` 加 `replace(/\\/g,'/')` 處理 Windows 反斜線
- **Theme 暗亮模式重構** ✅ — 全套 CSS 改用 CSS custom properties (`var(--surface)` 等)
  - `:root` = light, `:root.dark` = dark, `toggleTheme()` 切 `<html>` class 即全局生效
  - 移除所有 `.dark .xxx` 重複選擇器
  - JS inline style 從 hardcoded hex 改用 `var()`
  - localStorage 記住 theme 選擇
- **Frame Analysis 功能** ✅ — 完整實作
  - `db.py`: 新增 `frames` table (media_id, frame_index, timestamp_s, thumbnail_path, description, tags)
  - `frames.py`: frame thumbnails 持久化到 `thumbnails/{stem}_frame{i}.jpg`，回傳 timestamp
  - `ingest.py`: frame 資料寫入 `frames` table
  - `server.py`: detail API 回傳 `frames` 陣列 + `edl-markers` export 格式
  - 前端: frame 卡片帶 thumbnail + 時間碼，點擊跳轉串流播放器
- **EDL Export 合併** ✅ — toolbar Markers checkbox 控制是否包含 frame markers
- **路徑縮短** ✅ — Inspector 路徑顯示用 `{~}` 縮短中間路徑，hover 顯示完整
- **UI Scale** ✅ — 改為 4 段圓點選擇器 (100/120/140/160%)
- **Toolbar** ✅ — 標題精簡 `ARKIV`，flex-wrap 避免擠壓

### 已知限制
- **波形是假資料** — `waveformBars()` 固定高度陣列
- **In/Out marker** — 靜態顯示，不可拖曳
- **Vision 描述** — PC 端 Ollama llava 未測試，frame description 目前為空
- **Theme light mode** — 大部分 OK，少數 JS 生成的 inline color 可能仍需微調

### DB Schema 變更
```sql
CREATE TABLE frames (
    id             INTEGER PRIMARY KEY,
    media_id       INTEGER REFERENCES media(id) ON DELETE CASCADE,
    frame_index    INTEGER NOT NULL,
    timestamp_s    REAL NOT NULL,
    thumbnail_path TEXT,
    description    TEXT,
    tags           TEXT,
    UNIQUE(media_id, frame_index)
);
```
Mac 端 pull 後需要重新 ingest 或跑一次 frame 生成腳本來填充 frames table。

### 跨平台陷阱清單（更新）
| 陷阱 | Mac | PC |
|------|-----|-----|
| Whisper | mlx-whisper | faster-whisper (CUDA) |
| Path separator | `/` | `\`（thumbUrl + shortenPath 已處理） |
| ffprobe encoding | UTF-8 | cp950（已用 `encoding='utf-8'` 修正） |
| Tauri inject | `var` not `const` | 同 |
| CSS theming | CSS vars `:root` / `:root.dark` | 同 |

## Key Files Changed
| File | What |
|------|------|
| index.html | CSS vars theme + Frame Analysis UI + scale dots + path shorten |
| db.py | frames table + CRUD |
| frames.py | persistent frame thumbnails with timestamps |
| ingest.py | frame data → frames table |
| server.py | frames in detail API + edl-markers export |

## Mac 同步
```bash
cd ~/.arkiv
git pull
# 重新生成 frames（Mac 端 DB 不在 git 裡）
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

## Tag & Snapshot
- **Tag**: `pc-snapshot-20260331`
- **Repo**: `github.com/ourladypeace2011-commits/arkiv`
