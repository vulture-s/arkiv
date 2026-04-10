# arkiv Phase 8 — Codex Handover 執行計畫

## Context

arkiv v0.2.0 已發布（Phase 1-7 完成），下一步是 Phase 8：解決最大技術債（絕對路徑鎖死）+ 智慧取幀/品質分析（競品對齊 + smart-edit 整合需求）。此計畫交付 Codex 自主執行，完成後回 Claude Code 審計。

**Repo**: `<repo>`  
**Python**: 3.9（禁用 match/case、complex walrus、`X | None` type union）  
**現有測試**: 23 tests in `tests/`  

---

## 執行順序與依賴

```
8.0a config.py: PROJECT_ROOT + .arkiv/ 路徑
  ↓
8.0b db.py: to_relative() / resolve_path() 雙向轉換
  ↓
8.0c db.py: migrate_to_relative() 遷移函式
  ↓
8.0d ingest.py: 新 ingest 存相對路徑
  ↓ （以下可平行）
8.0e server.py: API 邊界回傳絕對路徑
8.0f vectordb.py: ChromaDB 存相對、搜尋回傳時由 server 解析
8.0g frames.py: thumbnail 路徑轉相對（由 ingest 呼叫端處理）
8.0h watch.py: known set 用 resolve_path 比對
  ↓
8.0i ingest.py: --migrate-relative CLI 入口
  ↓
8.0-tests
  ════════ 8.0 COMPLETE ════════
  ↓
8.2a frames.py: 自適應取幀數量
8.2b vision.py: prompt 擴充 8 新欄位
8.2c db.py: schema 新增 9 欄（media + frames）
8.2d ingest.py: 傳遞新 vision 欄位到 DB
8.2e server.py: GET /api/media/{id}/scenes
8.2f server.py: editability_score 計算 + 回傳
8.2g index.html: AI 分析 + 編輯建議 UI
  ↓
8.2-tests
  ════════ 8.2 COMPLETE ════════
  ↓
8.3 ingest.py: --refresh 重新處理所有素材
  ↓
8.3-tests
```

---

## 8.0 — DIT 架構（絕對→相對路徑）

### 8.0a: config.py

**檔案**: `<repo>/config.py` (46 行)

在 `PROXIES_DIR` 之後（line 15 後）新增：

```python
# ── Project Root ────────────────────────────────────────────────────────
PROJECT_ROOT = Path(os.getenv("ARKIV_PROJECT_ROOT", str(BASE_DIR)))
```

**不動現有路徑邏輯**。`PROJECT_ROOT` 預設 = `BASE_DIR`（向後相容）。只在使用者設定 `ARKIV_PROJECT_ROOT` 或跑 migration 時生效。

### 8.0b: db.py — 路徑轉換層

**檔案**: `<repo>/db.py`

在 `from config import DB_PATH` 之後（line 5 後）新增：

```python
import config as _config

def to_relative(abs_path: str) -> str:
    """絕對路徑→相對路徑（對 PROJECT_ROOT）。冪等。"""
    if not abs_path:
        return abs_path
    try:
        return str(Path(abs_path).relative_to(_config.PROJECT_ROOT))
    except ValueError:
        return abs_path  # 已是相對或不在 PROJECT_ROOT 下

def resolve_path(rel_path: str) -> str:
    """相對路徑→絕對路徑。冪等。"""
    if not rel_path:
        return rel_path
    p = Path(rel_path)
    if p.is_absolute():
        return str(p)
    return str(_config.PROJECT_ROOT / p)
```

修改 `is_processed()`（line 99）：

```python
def is_processed(path: str) -> bool:
    rel = to_relative(str(path))
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM media WHERE path=? OR path=?",
            (str(path), rel)
        ).fetchone()
        return row is not None
```

### 8.0c: db.py — 遷移函式

新增函式（放在 `init_db()` 之後）：

```python
def migrate_to_relative():
    """將 media.path / thumbnail_path / frames.thumbnail_path 從絕對轉為相對。"""
    with get_conn() as conn:
        rows = conn.execute("SELECT id, path, thumbnail_path FROM media").fetchall()
        for row in rows:
            new_path = to_relative(row["path"]) if row["path"] else row["path"]
            new_thumb = to_relative(row["thumbnail_path"]) if row["thumbnail_path"] else row["thumbnail_path"]
            if new_path != row["path"] or new_thumb != row["thumbnail_path"]:
                conn.execute(
                    "UPDATE media SET path=?, thumbnail_path=? WHERE id=?",
                    (new_path, new_thumb, row["id"])
                )
        frows = conn.execute("SELECT id, thumbnail_path FROM frames WHERE thumbnail_path IS NOT NULL").fetchall()
        for fr in frows:
            new_tp = to_relative(fr["thumbnail_path"])
            if new_tp != fr["thumbnail_path"]:
                conn.execute("UPDATE frames SET thumbnail_path=? WHERE id=?", (new_tp, fr["id"]))
    print(f"[migrate] 完成。{len(rows)} media + {len(frows)} frames 路徑已轉為相對。")
```

### 8.0d: ingest.py — 存相對路徑

**檔案**: `<repo>/ingest.py` (663 行)

所有存入 DB 的路徑改用 `db.to_relative()`：
- `record["path"] = db.to_relative(str(path))`（原本 `str(path)`）
- `record["thumbnail_path"] = db.to_relative(...)` — 包 `frames.extract_thumbnail()` 回傳值
- frame_data 的 `fd["thumbnail_path"]` — 包 frames 回傳值

所有從 DB 讀出後需操作檔案的地方改用 `db.resolve_path()`：
- `_run_vision_only` 中讀 `f["thumbnail_path"]` 傳給 vision 前 resolve
- retranscribe / reingest 中讀 `rec["path"]` 前 resolve

### 8.0e: server.py — API 邊界解析

**檔案**: `<repo>/server.py` (968 行)

新增 helper（放在 imports 之後）：

```python
def _resolve_record(rec: dict) -> dict:
    """API 回傳前將相對路徑解析為絕對。"""
    if rec.get("path"):
        rec["path"] = db.resolve_path(rec["path"])
    if rec.get("thumbnail_path"):
        rec["thumbnail_path"] = db.resolve_path(rec["thumbnail_path"])
    return rec
```

套用位置：
- `list_media()`：每筆 record 套 `_resolve_record()`
- `get_media_detail()`：主 record + frames 的 `thumbnail_path`
- `stream_media()`：`file_path = Path(db.resolve_path(rec["path"]))`
- FCPXML export：`raw_path = db.resolve_path(rec.get("path", ""))`
- retranscribe / reingest：`media_path = db.resolve_path(rec.get("path", ""))`
- search results：在 enrichment 後套 `_resolve_record()`

**關鍵原則**：DB 存相對、API 回絕對。DaVinci plugin 不需改（它從 API 拿絕對路徑）。

### 8.0f: vectordb.py

**不改 vectordb.py**。它從 DB record 拿 path（已是相對），存入 ChromaDB metadata。搜尋結果由 server.py 的 `_resolve_record` 統一解析。下次 `embed.py --rebuild` 時自動同步。

### 8.0g: frames.py

**不改 frames.py**。它回傳絕對路徑（需要 `out.exists()` 檢查）。ingest.py 呼叫端負責 `to_relative()`。

### 8.0h: watch.py

`watch.py` 的 `known` set 從 DB 讀 `path`（現在是相對），比對時用 resolve：

```python
known = {db.resolve_path(r["path"]) for r in rows}
```

### 8.0i: CLI 遷移入口

在 `ingest.py` 的 `argparse` 加：

```python
parser.add_argument("--migrate-relative", action="store_true",
    help="將 DB 中所有絕對路徑轉為相對路徑（對 ARKIV_PROJECT_ROOT）")
```

在 `main()` 開頭：

```python
if args.migrate_relative:
    db.init_db()
    db.migrate_to_relative()
    return
```

---

## 8.2 — 智慧取幀 + 品質分析

### 8.2a: frames.py — 自適應取幀

**檔案**: `<repo>/frames.py`

修改 `extract_frames()`（line 33-50）的幀數邏輯：

```python
# 取代固定 3 幀
if duration_s < 2:
    n_frames = 1
elif duration_s <= 10:
    n_frames = 3
elif duration_s <= 60:
    n_frames = 5
else:
    n_frames = 5 + max(1, int((duration_s - 60) / 30))
```

修改 `_extract_fixed_persistent`：加 `n_frames` 參數（預設 3），用 `[i / (n_frames + 1) for i in range(1, n_frames + 1)]` 取代硬編碼 `[0.25, 0.5, 0.75]`。

修改 `_extract_scene_persistent`：加 `max_frames` 參數（預設 5），取代硬編碼 3。

### 8.2b: vision.py — 擴充 prompt

**檔案**: `<repo>/vision.py`

替換 `PROMPT`（line 10-14）為：

```python
PROMPT = (
    "請用繁體中文分析這個影片畫面，回傳嚴格的 JSON 格式（不要加 markdown 標記）：\n"
    "{\n"
    '  "description": "1-2句描述可見內容（地點、事件、人物）",\n'
    '  "tags": ["標籤1", "標籤2", "標籤3", "標籤4", "標籤5"],\n'
    '  "content_type": "A-Roll|B-Roll|Talking-Head|Product-Shot|Transition|Establishing|Undefined 擇一",\n'
    '  "focus_score": 1到5的整數,\n'
    '  "exposure": "dark|normal|over 擇一",\n'
    '  "stability": "穩定|輕微晃動|嚴重晃動 擇一",\n'
    '  "audio_quality": "清晰|嘈雜|靜音 擇一",\n'
    '  "atmosphere": "一個詞描述氛圍",\n'
    '  "energy": "高|中|低 擇一",\n'
    '  "edit_position": "開場|中段-轉場|中段-互動|收尾 擇一",\n'
    '  "edit_reason": "一句話說明建議用途"\n'
    "}\n"
    "規則：只描述清楚可見的內容，不要推測。所有欄位必填。"
)
```

修改 `_describe_one()` 的回傳（line 65）：從 parsed dict 提取所有新欄位，缺值時用 None。

修改 `frames_to_json()`（line 88-93）：序列化時包含新欄位。

### 8.2c: db.py — Schema 擴充

在 `init_db()` 的 migration list（line 70-92）後追加：

```python
# Phase 8.2: Smart Frame Analysis + Quality Assessment
("focus_score", "INTEGER"),
("exposure", "TEXT"),
("stability", "TEXT"),
("audio_quality", "TEXT"),
("atmosphere", "TEXT"),
("energy", "TEXT"),
("edit_position", "TEXT"),
("edit_reason", "TEXT"),
("editability_score", "REAL"),
```

frames table 也加對應欄位（另開一個 migration loop）：

```python
for col, typ in [
    ("content_type", "TEXT"),
    ("focus_score", "INTEGER"),
    ("exposure", "TEXT"),
    ("stability", "TEXT"),
    ("audio_quality", "TEXT"),
    ("atmosphere", "TEXT"),
    ("energy", "TEXT"),
    ("edit_position", "TEXT"),
    ("edit_reason", "TEXT"),
]:
    try:
        conn.execute(f"ALTER TABLE frames ADD COLUMN {col} {typ}")
    except Exception:
        pass
```

新增 `_ALLOWED_COLS` 包含所有新欄位名。

新增 `compute_editability()` 函式：

```python
def compute_editability(rec: dict) -> float:
    """0-100 分。基於 focus + exposure + stability + audio + rating。"""
    score = 50.0
    fs = rec.get("focus_score")
    if fs is not None:
        try:
            score += (int(fs) - 3) * 10
        except (ValueError, TypeError):
            pass
    if rec.get("exposure") == "normal":
        score += 10
    elif rec.get("exposure") in ("dark", "over"):
        score -= 10
    if rec.get("stability") == "穩定":
        score += 10
    elif rec.get("stability") == "嚴重晃動":
        score -= 15
    if rec.get("audio_quality") == "清晰":
        score += 10
    elif rec.get("audio_quality") == "嘈雜":
        score -= 5
    rat = rec.get("rating")
    if rat == "good":
        score += 10
    elif rat == "ng":
        score -= 15
    return max(0.0, min(100.0, round(score, 1)))
```

修改 `upsert_frame()` 接受新欄位並寫入。

### 8.2d: ingest.py — 傳遞新欄位

vision 結果 → frame_data 時加入所有新欄位。
`db.upsert_frame()` 呼叫傳入新欄位。
每支影片 frames 處理完後，取最佳 frame 計算 `editability_score` 寫入 media record。

### 8.2e: server.py — scenes endpoint

新增：

```python
@app.get("/api/media/{media_id}/scenes")
def get_media_scenes(media_id: int):
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "找不到")
    frames = db.get_frames(media_id)
    scenes = []
    for f in frames:
        scene = {
            "frame_index": f["frame_index"],
            "timestamp_s": f["timestamp_s"],
            "description": f.get("description", ""),
            "content_type": f.get("content_type"),
            "focus_score": f.get("focus_score"),
            "atmosphere": f.get("atmosphere"),
            "energy": f.get("energy"),
            "edit_position": f.get("edit_position"),
            "edit_reason": f.get("edit_reason"),
        }
        if f.get("thumbnail_path"):
            scene["thumbnail_url"] = f"/thumbnails/{Path(db.resolve_path(f['thumbnail_path'])).name}"
        scenes.append(scene)
    return {"media_id": media_id, "scenes": scenes, "total": len(scenes)}
```

### 8.2f: server.py — editability_score

在 `get_media_detail()` 中，回傳 `editability_score`（已在 DB 中）。若為 None 且有 frames，on-the-fly 計算：

```python
if rec.get("editability_score") is None:
    for f in (rec.get("frames") or []):
        if f.get("focus_score") is not None:
            rec["editability_score"] = db.compute_editability(f)
            break
```

### 8.2g: index.html — UI

在 Inspector 的 frames section 之後加入兩個區塊：

**「AI 分析」區塊**：顯示第一個 frame 的 `content_type`、`focus_score`（星星）、`exposure`、`stability`、`audio_quality`。

**「編輯建議」區塊**：顯示 `edit_position`、`edit_reason`、`atmosphere`、`energy`、`editability_score` badge（0-100 色碼）。

Media grid card 上加 editability score 小標籤。

---

## 8.3 — Re-ingest

現有 `--refresh` flag 已存在。確保 8.2 的改動在 refresh 流程中生效：
- 刪除舊 frames → 用新的自適應數量重新取幀
- 重跑 vision（擴充 prompt）→ 存新欄位
- 計算 editability_score

---

## 測試計畫

### 新增 `tests/test_phase8.py`

**8.0 測試（6 項）：**

| # | 測試 | 驗證 |
|---|------|------|
| 1 | `test_to_relative_idempotent` | 呼叫兩次結果相同；已是相對不變 |
| 2 | `test_resolve_path_idempotent` | 呼叫兩次結果相同；已是絕對不變 |
| 3 | `test_to_relative_outside_project_root` | 不在 PROJECT_ROOT 下的路徑原樣回傳 |
| 4 | `test_is_processed_both_forms` | 存相對路徑 → 傳絕對也能找到 |
| 5 | `test_migrate_to_relative` | 插入絕對路徑 → 跑 migrate → 變相對 |
| 6 | `test_resolve_record_in_api` | API 回傳包含絕對路徑 |

**8.2 測試（7 項）：**

| # | 測試 | 驗證 |
|---|------|------|
| 7 | `test_adaptive_frame_count_short` | 1.5s → 1 frame |
| 8 | `test_adaptive_frame_count_medium` | 30s → 5 frames |
| 9 | `test_adaptive_frame_count_long` | 120s → 7 frames |
| 10 | `test_schema_new_columns_exist` | init_db 後 focus_score 等欄位存在 |
| 11 | `test_upsert_frame_with_quality` | 新欄位寫入讀出正確 |
| 12 | `test_compute_editability` | 各種組合計算正確 |
| 13 | `test_scenes_endpoint` | API 回傳 scenes 結構 |

---

## 自審 Checklist（Codex 完成後必須逐項確認）

```
── 基礎 ──
[ ] python3 -m pytest — 原有 17+ passing tests 不退化
[ ] 新增 13 tests 全部 PASS
[ ] grep -rn "match " *.py — 無 match/case 語法
[ ] grep -rn " | None" *.py — 無 3.10+ type union（annotations 除外）

── 8.0 路徑 ──
[ ] config.py: PROJECT_ROOT 預設 = BASE_DIR（向後相容）
[ ] db.to_relative() 冪等（連呼兩次結果相同）
[ ] db.resolve_path() 冪等
[ ] db.is_processed() 同時接受絕對和相對
[ ] server.py: 所有 API response 的 path 都是絕對
[ ] server.py: stream endpoint 用 resolve_path 解析
[ ] server.py: FCPXML export 用 resolve_path 產生 file:// URI
[ ] vectordb.py: 沒有 import db（避免循環引用）
[ ] watch.py: known set 用 resolve_path 比對
[ ] resolve_plugin 不需改動（API 已回傳絕對路徑）

── 8.2 品質分析 ──
[ ] vision.py: prompt 包含 8 個新欄位
[ ] vision.py: _describe_one 回傳 dict 包含新欄位，缺值用 None
[ ] frames.py: 自適應取幀 — <2s→1, 2-10s→3, 10-60s→5, >60s→5+floor
[ ] db.py: media table migration 含 9 新欄（含 editability_score）
[ ] db.py: frames table migration 含 9 新欄
[ ] db.py: _ALLOWED_COLS 含所有新欄位名
[ ] db.py: compute_editability 回傳 0-100
[ ] db.py: upsert_frame 接受並儲存新欄位
[ ] ingest.py: vision 結果 → frame_data 傳遞新欄位
[ ] ingest.py: 每支影片處理完計算 editability_score
[ ] server.py: GET /api/media/{id}/scenes 存在且回傳正確結構
[ ] server.py: media detail 包含 editability_score
[ ] index.html: AI 分析區塊可見
[ ] index.html: 編輯建議區塊可見

── 8.3 Re-ingest ──
[ ] --refresh 觸發時刪除舊 frames + 重新取幀 + 重跑 vision
[ ] --migrate-relative 正常執行

── 整合 ──
[ ] smoke-test.sh 通過（如可執行）
[ ] 無硬編碼 IP / API key / 密碼
[ ] 無新增未使用的 import
```

---

## 風險與緩解

| 風險 | 緩解 |
|------|------|
| UNIQUE(path) 衝突：migration 後兩筆記錄指向同一相對路徑 | `migrate_to_relative()` 用 transaction + try/except 逐筆處理，衝突時 log warning 不 crash |
| 循環引用：vectordb import db | vectordb **不改**，路徑解析全在 server.py |
| Vision prompt 膨脹導致 JSON parse 失敗率上升 | `_describe_one()` 保留現有 fallback（free-text parse），新欄位缺值 = None，不阻塞 |
| Python 3.9 相容性 | 不用 `match/case`、`X \| None`。用 `Optional[X]` 和 `if/elif` |
| 現有 API consumer（DaVinci plugin）壞掉 | API 層統一 resolve → plugin 拿到的仍是絕對路徑，無感 |
| 前端 thumbnail URL 建構壞掉 | index.html 用 `.split('/').pop()` 取 filename，路徑格式無關 |

---

## 交付格式

Codex 完成後提交：
1. **Git commit(s)**：按功能拆分（8.0 一個、8.2 一個、8.3 一個、tests 一個）
2. **自審報告**：逐項填寫上方 checklist
3. **測試輸出**：`python3 -m pytest -v` 完整輸出

Claude Code 審計項目：
- 逐項驗證自審 checklist
- 讀 diff 確認無遺漏
- 跑 `python3 -m pytest` 確認
- 抽查 API response 路徑格式
