# Anti-Hallucination Guard — 四層防幻覺架構

> arkiv 的 Whisper 語音轉譯品質保證系統。
> 原始碼：`transcribe.py` → `_postprocess()`

---

## 為什麼需要

Whisper 在以下情境會產生幻覺（hallucination）：

| 情境 | 幻覺類型 | 範例 |
|------|---------|------|
| 靜音 / 環境音 | 無中生有 | 純風聲 → 「謝謝大家收聽」 |
| 低信噪比 | 亂猜詞 | 遠處雜訊 → 一串不相關的字 |
| 重複音節 | 無限循環 | 「好好好好好好好好好好...」 |
| 音樂段落 | 幻聽歌詞 | 純演奏 → 生成不存在的歌詞 |

不處理這些幻覺，會污染語意搜尋索引（ChromaDB），導致搜尋結果不可靠。

---

## 四層架構

```
Whisper 原始輸出
    │
    ▼
┌─────────────────────────────────┐
│  Guard 1: 全段靜音偵測           │
│  avg(no_speech_prob) > 0.6      │
│  → 整段丟棄，回傳空字串          │
└───────────────┬─────────────────┘
                │ 通過
                ▼
┌─────────────────────────────────┐
│  Guard 2: 逐段品質過濾           │
│  per-segment filters:           │
│  • no_speech_prob > 0.8 → 丟棄  │
│  • avg_logprob < -1.5  → 丟棄   │
│  • compression_ratio > 3.0 → 丟棄│
│  → 只保留高品質 segments         │
└───────────────┬─────────────────┘
                │ 通過
                ▼
┌─────────────────────────────────┐
│  Guard 3: 文本層級重複偵測       │
│  N-gram window=6, threshold=0.35│
│  unique_chunks / total < 0.35   │
│  → 整段判定為幻覺，回傳空字串    │
└───────────────┬─────────────────┘
                │ 通過
                ▼
┌─────────────────────────────────┐
│  Guard 4: 字元循環移除           │
│  regex: (.{2,4})\1{2,}         │
│  → 保留第一次出現，移除重複      │
└───────────────┬─────────────────┘
                │ 通過
                ▼
┌─────────────────────────────────┐
│  LLM 校正（qwen2.5:14b）        │
│  • 補標點符號                    │
│  • 修同音字                      │
│  • 保持口語化，不改書面語         │
│  • 長度安全閥：0.5x < output < 2x│
└───────────────┬─────────────────┘
                │
                ▼
           最終逐字稿
```

---

## 各層參數與設計理由

### Guard 1 — 全段靜音偵測

```python
avg_no_speech = sum(s.no_speech_prob for s in segments) / len(segments)
if avg_no_speech > 0.6:
    return ""
```

**閾值 0.6 的理由**：Whisper 的 `no_speech_prob` 在純靜音時通常 > 0.9，在有微弱環境音時約 0.5-0.7。0.6 是保守閾值，寧可漏放不要誤殺。

### Guard 2 — 逐段品質過濾

```python
for segment in segments:
    if segment.no_speech_prob > 0.8:  continue  # 靜音
    if segment.avg_logprob < -1.5:    continue  # 低信心
    if segment.compression_ratio > 3.0: continue # 重複
    good_segments.append(segment)
```

**三個指標的意義**：
| 指標 | 正常範圍 | 幻覺範圍 | 閾值 |
|------|---------|---------|------|
| `no_speech_prob` | 0.0 - 0.3 | 0.8 - 1.0 | > 0.8 |
| `avg_logprob` | -0.3 - -0.8 | < -1.5 | < -1.5 |
| `compression_ratio` | 1.0 - 2.0 | > 3.0 | > 3.0 |

### Guard 3 — N-gram 重複偵測

```python
chunks = [text[i:i+6] for i in range(0, len(text)-6, 6)]
unique_ratio = len(set(chunks)) / len(chunks)
if unique_ratio < 0.35:
    return ""  # 判定為幻覺
```

**場景**：Whisper 偶爾會生成文法正確但不斷循環的段落。Guard 2 無法偵測（每段的 logprob/compression 可能正常），需文本層級檢查。

### Guard 4 — 字元循環移除

```python
text = re.sub(r'(.{2,4})\1{2,}', r'\1', text)
```

**場景**：比 Guard 3 更細粒度，處理局部循環（例如「好好好好好好」→「好」）。保留第一次出現以維持語意。

### LLM 校正 — 最後一道

```python
if len(text) > 10:
    polished = ollama.generate("qwen2.5:14b", polish_prompt)
    if 0.5 < len(polished) / len(text) < 2.0:  # 長度安全閥
        return polished
```

**安全閥**：如果 LLM 輸出長度偏離原文太多（< 50% 或 > 200%），判定 LLM 本身幻覺，回退原文。

---

## 實測效果（2026-04-02）

### Guard 對轉譯品質的影響

| 模式 | 短片 (10.5s) 輸出 | 長片 (414s) 字數 |
|------|-------------------|-----------------|
| Raw（無 Guard） | 無標點，口語直出 | v3: 1,793 / turbo: 1,764 |
| Full Pipeline（Guard + LLM） | 有標點，可讀性高 | v3: 1,914 / turbo: 1,795 |

### Guard 的價值

| 效果 | 說明 |
|------|------|
| ✅ **幻覺過濾** | 靜音段、環境音段不進入搜尋索引 |
| ✅ **標點補全** | LLM 校正後可讀性大幅提升 |
| ✅ **循環移除** | 防止重複文字膨脹索引 |
| 🟡 **錯字修正** | 同音字有效（「蕭希」→「小熙」），缺語境的誤辨無效（「針」未被修正） |
| ❌ **速度代價** | 長片 LLM 校正占整體 70% 時間 |

### 未來改進方向

1. **Custom Vocabulary**（Phase 9）：Whisper `--initial-prompt` 加入人名/品牌 hotwords，從源頭減少誤辨
2. **Filter Dictionary**（Phase 9）：後處理移除語氣詞（「就是」「對」「嗯」）
3. **Qwen3-ASR**：如果 Whisper turbo 的中文準確度不足，換模型比加 Guard 更根本

---

## 相關檔案

| 檔案 | 說明 |
|------|------|
| `transcribe.py` | Guard 實作（`_postprocess` + `_is_repetitive` + `_has_char_loops` + `_llm_polish`） |
| `config.py` | 模型設定（`WHISPER_MODEL` + `OLLAMA_URL`） |
| `docs/benchmark-model-upgrade-20260402.md` | 模型升級實測數據 |
