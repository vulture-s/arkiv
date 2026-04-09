# Codex Handover: WhisperX 整合 + word-level timestamps

## 目標
將 arkiv 的 CUDA 轉錄路徑從 faster-whisper 切換為 WhisperX，取得 word-level timestamps（±20ms 精度），存入新欄位 `words_json`。同時修復 VAD 拼接導致的時間戳偏移 bug 和 retranscribe endpoint 的 unpack bug。

## 環境限制

- **Python 版本**：必須用 `py -3.12`。shell 的 `python3` 指向 Python 3.14（無 torch）
- **GPU**：RTX 4070，torch 2.11.0+cu128
- **只改 CUDA 路徑**：所有改動都在 `_USE_MLX == False` 分支。Mac 的 mlx-whisper 路徑完全不動

## 前置步驟（安裝 WhisperX）— 已在本機完成

```bash
# 已執行，Codex 不需要重複
py -3.12 -m pip install whisperx
# ⚠️ whisperx 會把 torch 降級為 CPU 版，必須重裝 CUDA 版：
py -3.12 -m pip install --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

**Codex 不需要執行安裝步驟**。Codex 只改 code，驗證在本機進行。

## 改動清單（5 個檔案）

### 1. `transcribe.py`

#### 1a. `_transcribe_faster()` → `_transcribe_whisperx()`

替換 `_transcribe_faster()` 函式（lines 159-191）為：

```python
def _transcribe_whisperx(wav: str, language: str) -> tuple:
    """Transcribe using WhisperX (CUDA) with forced alignment."""
    import whisperx

    device = "cuda"
    audio = whisperx.load_audio(wav)

    # Step 1: Transcribe (WhisperX internally uses faster-whisper)
    model = whisperx.load_model(WHISPER_MODEL, device, compute_type="float16",
                                 language=language)
    initial_prompt = _build_initial_prompt()
    result = model.transcribe(audio, batch_size=16,
                               language=language,
                               initial_prompt=initial_prompt if initial_prompt else None)

    # Step 2: Forced alignment (wav2vec2) — this is what gives word-level precision
    align_model, align_meta = whisperx.load_align_model(
        language_code=language, device=device
    )
    result = whisperx.align(
        result["segments"], align_model, align_meta, audio, device,
        return_char_alignments=False
    )

    # Step 3: Extract segments + words
    segments = []
    all_words = []
    for seg in result["segments"]:
        segments.append({
            "text": seg.get("text", "").strip(),
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "no_speech_prob": seg.get("no_speech_prob", 0),
            "avg_logprob": seg.get("avg_logprob", 0),
            "compression_ratio": seg.get("compression_ratio", 1),
        })
        for w in seg.get("words", []):
            if "start" in w and "end" in w:
                all_words.append({
                    "word": w["word"].strip(),
                    "start": round(w["start"], 3),
                    "end": round(w["end"], 3),
                    "score": round(w.get("score", 0), 3),
                })

    text = " ".join(s["text"] for s in segments).strip()
    return _postprocess(text, language, segments, language, words=all_words)
```

#### 1b. 修改 `warm_up()` non-MLX 分支（lines 86-92）

```python
    else:
        import whisperx
        # Pre-load WhisperX model (uses faster-whisper internally)
        _whisperx_model = whisperx.load_model(WHISPER_MODEL, "cuda", compute_type="float16")
        print(f"  [whisperx on cuda]", flush=True)
```

注意：需要在 module level 加 `_whisperx_model = None` 全域變數。然後 `_transcribe_whisperx` 裡可以復用這個 cached model 而不是每次重新 load。

#### 1c. 修改 `_postprocess()` 簽名（line 194）

```python
# 舊:
def _postprocess(text: str, lang: str, segments: list, language: str) -> tuple:

# 新:
def _postprocess(text: str, lang: str, segments: list, language: str, words: list = None) -> tuple:
```

在 return 的地方（目前 line 262）：
```python
# 舊:
    return filtered_text, lang, timed_segments

# 新:
    return filtered_text, lang, timed_segments, words or []
```

同時更新所有其他 return 點：
- Line 198 (avg_no_speech guard): `return "", lang, [], []`
- Line 229 (no good segments): `return "", lang, [], []`
- Line 235 (repetitive): `return "", lang, [], []`

#### 1d. 修改 `transcribe()` 主函式（lines 98-127）

CUDA 路徑不再呼叫 `_vad_filter()`（WhisperX 自帶 VAD），MLX 路徑保持不變：

```python
def transcribe(media_path: str, language: str = DEFAULT_LANGUAGE) -> tuple:
    """
    Transcribe audio from a media file.
    Returns (transcript_text, language, segments_list, words_list).
    segments_list: [{"start": float, "end": float, "text": str}, ...]
    words_list: [{"word": str, "start": float, "end": float, "score": float}, ...]
    Returns ("", "", [], []) if no speech detected.
    """
    global _whisper_loaded, _fw_model
    _whisper_loaded = True

    wav = _to_wav(media_path)
    if not wav:
        return "", "", [], []

    try:
        if _USE_MLX:
            # Mac path: keep existing VAD + mlx-whisper flow
            vad_wav = _vad_filter(wav)
            if vad_wav is None:
                Path(wav).unlink(missing_ok=True)
                return "", "", [], []
            result = _transcribe_mlx(vad_wav, language)
            if vad_wav != wav:
                Path(vad_wav).unlink(missing_ok=True)
            return result
        else:
            # PC/CUDA path: WhisperX handles VAD internally
            return _transcribe_whisperx(wav, language)
    finally:
        Path(wav).unlink(missing_ok=True)
```

#### 1e. 修改 `_transcribe_mlx()` return（line 156）

```python
# 舊:
    return _postprocess(text, lang, raw_segments, language)

# 新:
    return _postprocess(text, lang, raw_segments, language, words=[])
```

#### 1f. 保留 `_vad_filter()` 不刪除

MLX 路徑還在用它。不要刪除。

---

### 2. `db.py`

#### 2a. 新增 migration 欄位（在 line 89 的 `("segments_json", "TEXT")` 之後）

```python
            # Phase 10: WhisperX word-level timestamps for Remotion
            ("words_json", "TEXT"),
```

#### 2b. 更新 `_ALLOWED_COLS`（line 103-111）

加入 `"words_json"` 到 set 中。

---

### 3. `ingest.py`

#### 3a. 修改 line 213

```python
# 舊:
        text, lang, segments = tr.transcribe(str(path))

# 新:
        text, lang, segments, words = tr.transcribe(str(path))
```

#### 3b. 在 line 220 之後新增

```python
        if words:
            record["words_json"] = json.dumps(words, ensure_ascii=False)
        else:
            record["words_json"] = None
```

---

### 4. `server.py`

#### 4a. 修復 retranscribe endpoint（lines 294-301）

```python
    try:
        import transcribe as tr
        text, lang, segments, words = tr.transcribe(media_path, language=body.language)
        import json as _json
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE media SET transcript=?, lang=?, segments_json=?, words_json=? WHERE id=?",
                (text, body.language,
                 _json.dumps(segments, ensure_ascii=False) if segments else None,
                 _json.dumps(words, ensure_ascii=False) if words else None,
                 media_id)
            )
        return {"ok": True, "transcript_length": len(text), "language": body.language}
```

#### 4b. 新增 Remotion props endpoint

在 retranscribe endpoint 之後加入：

```python
@app.get("/api/media/{media_id}/remotion-props")
def get_remotion_props(media_id: int):
    """Export word-level timestamps as Remotion CellPhoneReel props."""
    rec = db.get_record_by_id(media_id)
    if not rec:
        raise HTTPException(404, "Not found")
    import json as _json
    words = _json.loads(rec.get("words_json") or "[]")
    return {
        "captions": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in words],
        "duration": rec.get("duration_s", 0),
        "filename": rec.get("filename", ""),
    }
```

---

### 5. `health.py`

#### 5a. 修改 Whisper 檢查（lines 117-143）

在 `has_faster` 檢查之後加入 `has_whisperx` 檢查：

```python
    has_whisperx = False
    try:
        import whisperx  # noqa: F401
        has_whisperx = True
    except ImportError:
        pass
```

修改 Windows/Linux PC 區塊（line 141-143）：
```python
    else:
        # Windows/Linux PC: whisperx preferred (wraps faster-whisper + alignment)
        check("whisperx (CUDA alignment)", has_whisperx, "" if has_whisperx else "(pip install whisperx)")
        check("faster-whisper (dependency)", has_faster, "" if has_faster else "(installed via whisperx)")
        check("any whisper backend", has_whisperx or has_faster, "(need at least one)")
```

---

### 6. `requirements.txt`

在 `faster-whisper>=1.0` 行之後加入：

```
whisperx>=3.1           # CUDA word-level alignment (non-Darwin)
```

---

## 不要改的東西

- `_vad_filter()` 函式 — MLX 路徑還在用
- `_transcribe_mlx()` — Mac 路徑
- `_build_initial_prompt()` — 共用
- `_llm_polish()` — 共用
- Guard 1-4 邏輯 — 操作 segment-level
- `segments_json` 的 `{start, end, text}` 格式 — 所有消費者依賴這個
- `LIGHT_COLS` — words_json 不需要在 list query 回傳
- SRT/VTT export 邏輯
- FCPXML export 邏輯
- ChromaDB embedding 邏輯
- `index.html` / Tauri 前端

## 驗證步驟

完成改動後，依序執行：

```bash
cd C:\Users\user\.arkiv

# 1. Health check
py -3.12 health.py

# 2. 單檔轉錄測試（用任意短影片）
py -3.12 -c "
import transcribe as tr
tr.warm_up()
text, lang, segs, words = tr.transcribe('找一支有語音的短影片路徑')
print(f'Text: {text[:100]}...')
print(f'Segments: {len(segs)}')
print(f'Words: {len(words)}')
if words:
    print(f'Sample: {words[:3]}')
    assert all('start' in w and 'end' in w and 'word' in w for w in words)
assert all('start' in s and 'end' in s and 'text' in s for s in segs)
print('PASS')
"

# 3. Ingest 測試
py -3.12 ingest.py --dir 找一個有影片的目錄 --limit 1

# 4. DB 驗證
py -3.12 -c "
import db; db.init_db()
import sqlite3
conn = sqlite3.connect('media.db')
cur = conn.execute('PRAGMA table_info(media)')
cols = [row[1] for row in cur.fetchall()]
assert 'words_json' in cols, f'words_json not in columns: {cols}'
print('Schema OK')
# Check a record
row = conn.execute('SELECT words_json FROM media WHERE words_json IS NOT NULL LIMIT 1').fetchone()
if row:
    import json
    words = json.loads(row[0])
    print(f'words_json has {len(words)} entries')
    print(f'First: {words[0]}')
print('PASS')
"

# 5. Server API 測試
py -3.12 -m uvicorn server:app --port 8501 &
# 等 server 啟動後：
curl http://localhost:8501/health
curl http://localhost:8501/api/media/1/remotion-props
curl http://localhost:8501/api/media/1/export/srt
```

## 交付標準

1. `py -3.12 health.py` 全 PASS
2. 單檔轉錄返回 4-tuple，words 非空，每個 word 有 start/end/word/score
3. segments 保持 `{start, end, text}` 格式
4. `words_json` 欄位存在且有資料
5. `/api/media/{id}/remotion-props` 返回正確 JSON
6. `/api/media/{id}/retranscribe` 不再 crash
7. SRT export 仍正常
8. Mac 路徑不受影響（`_USE_MLX` 分支未動）
