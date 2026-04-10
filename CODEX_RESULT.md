# CODEX RESULT — Phase 8

## 完成了什麼

- [x] **Phase 8.0 — DIT 路徑架構**
  - `config.py` 新增 `PROJECT_ROOT`
  - `db.py` 新增 `to_relative()` / `resolve_path()` / `migrate_to_relative()`
  - `db.is_processed()` 同時接受絕對與相對路徑
  - `ingest.py` 改為 DB 存相對路徑，讀檔前 resolve
  - `server.py` API 邊界統一回傳絕對路徑
  - `watch.py` known set 比對改走 `resolve_path()`
- [x] **Phase 8.2 — 智慧取幀 + 品質分析**
  - `frames.py` 新增自適應取幀數量
  - `vision.py` prompt / JSON schema 擴充 8+1 個品質欄位
  - `db.py` schema 擴充 media + frames 欄位，新增 `compute_editability()`
  - `ingest.py` / `retry-vision` 寫入新 frame quality 欄位與 `editability_score`
  - `server.py` 新增 `/api/media/{id}/scenes`
  - `index.html` 新增「AI 分析」與「編輯建議」區塊，grid card 顯示 editability badge
- [x] **Phase 8.3 — refresh / migration 流程**
  - `ingest.py --migrate-relative`
  - `--refresh` 流程沿用新 frame schema 與 editability 計算
- [x] **測試**
  - 新增 `tests/test_phase8.py`（13 tests）
  - 對齊既有測試到目前 production 行為：`_postprocess()` 4-tuple、export 錯誤訊息中文化

## 修改檔案

- `config.py`
- `db.py`
- `frames.py`
- `vision.py`
- `ingest.py`
- `server.py`
- `watch.py`
- `index.html`
- `tests/test_phase8.py`
- `tests/test_server.py`
- `tests/test_transcribe_guards.py`

## 自審 Checklist

### 基礎

- [x] `python3 -m pytest tests/ -v` 全綠
- [x] 新增 Phase 8 測試全過
- [x] `rg` 檢查本次修改檔案無 `match/case`
- [x] `rg` 檢查本次修改檔案無 `| None` / `list[dict]` / `set[str]`

### 8.0 路徑

- [x] `PROJECT_ROOT` 預設 = `BASE_DIR`
- [x] `db.to_relative()` 冪等
- [x] `db.resolve_path()` 冪等
- [x] `db.is_processed()` 同時接受絕對與相對
- [x] API 回傳的 `path` / `thumbnail_path` 已 resolve 成絕對路徑
- [x] `stream` / `retranscribe` / `reingest` / FCPXML export 都先 `resolve_path()`
- [x] `vectordb.py` 未改動，避免循環引用
- [x] `watch.py` 已用 `resolve_path()` 比對 known set

### 8.2 品質分析

- [x] `vision.py` prompt 含新增欄位
- [x] `_describe_one()` 回傳 dict 包含新增欄位，缺值為 `None`
- [x] `frames.py` 自適應取幀規則已落地
- [x] `db.py` media / frames migration 已擴充
- [x] `_ALLOWED_COLS` 已包含新 media 欄位
- [x] `compute_editability()` 回傳 0-100
- [x] `upsert_frame()` 接受並儲存新欄位
- [x] ingest / retry vision 會把新欄位寫進 DB
- [x] `server.py` 新增 scenes endpoint
- [x] media detail 包含 `editability_score`
- [x] `index.html` AI 分析區塊可見
- [x] `index.html` 編輯建議區塊可見

### 8.3 Re-ingest

- [x] `--refresh` 流程會重建 frames 並寫入新欄位
- [x] `--migrate-relative` 可執行

### 整合

- [x] `py_compile` 通過
- [x] `index.html` 用 Python `HTMLParser` 成功解析
- [ ] `smoke-test.sh` 通過

## 測試結果

### 1. Python compile

```bash
env PYTHONPYCACHEPREFIX=/tmp/arkiv-pyc python3 -m py_compile config.py db.py frames.py vision.py ingest.py server.py watch.py tests/test_phase8.py tests/test_server.py tests/test_transcribe_guards.py
```

```text
[exit code 0]
```

### 2. Phase 8 + 全測試

```bash
env PYTHONPYCACHEPREFIX=/tmp/arkiv-pyc python3 -m pytest tests/ -v
```

```text
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0 -- /Library/Developer/CommandLineTools/usr/bin/python3
rootdir: <repo>
configfile: pytest.ini
plugins: anyio-4.12.1
collecting ... collected 36 items

tests/test_db.py::test_init_db_is_idempotent PASSED                      [  2%]
tests/test_db.py::test_upsert_updates_existing_path_and_is_processed PASSED [  5%]
tests/test_db.py::test_get_media_list_and_count_support_pagination_and_filters PASSED [  8%]
tests/test_db.py::test_get_record_stats_rating_and_tags_flow PASSED      [ 11%]
tests/test_db.py::test_get_media_filtered_applies_sort_and_combined_filters PASSED [ 13%]
tests/test_phase8.py::test_to_relative_idempotent PASSED                 [ 16%]
tests/test_phase8.py::test_resolve_path_idempotent PASSED                [ 19%]
tests/test_phase8.py::test_to_relative_outside_project_root PASSED       [ 22%]
tests/test_phase8.py::test_is_processed_both_forms PASSED                [ 25%]
tests/test_phase8.py::test_migrate_to_relative PASSED                    [ 27%]
tests/test_phase8.py::test_resolve_record_in_api PASSED                  [ 30%]
tests/test_phase8.py::test_adaptive_frame_count_short PASSED             [ 33%]
tests/test_phase8.py::test_adaptive_frame_count_medium PASSED            [ 36%]
tests/test_phase8.py::test_adaptive_frame_count_long PASSED              [ 38%]
tests/test_phase8.py::test_schema_new_columns_exist PASSED               [ 41%]
tests/test_phase8.py::test_upsert_frame_with_quality PASSED              [ 44%]
tests/test_phase8.py::test_compute_editability PASSED                    [ 47%]
tests/test_phase8.py::test_scenes_endpoint PASSED                        [ 50%]
tests/test_server.py::test_list_media_supports_empty_paginated_and_filtered_results PASSED [ 52%]
tests/test_server.py::test_media_detail_returns_200_and_404 PASSED       [ 55%]
tests/test_server.py::test_rating_update_set_clear_and_missing_record PASSED [ 58%]
tests/test_server.py::test_tag_stats_and_tag_catalog_endpoints PASSED    [ 61%]
tests/test_server.py::test_export_endpoints_cover_supported_formats_and_current_json_gap PASSED [ 63%]
tests/test_transcribe_guards.py::test_is_repetitive_detects_looping_text PASSED [ 66%]
tests/test_transcribe_guards.py::test_is_repetitive_keeps_normal_chinese_sentence PASSED [ 69%]
tests/test_transcribe_guards.py::test_char_loop_helpers_handle_chinese_patterns PASSED [ 72%]
tests/test_transcribe_guards.py::test_postprocess_returns_empty_when_average_no_speech_too_high PASSED [ 75%]
tests/test_transcribe_guards.py::test_postprocess_filters_bad_segments_but_keeps_threshold_boundary PASSED [ 77%]
tests/test_transcribe_guards.py::test_postprocess_rejects_repetitive_text PASSED [ 80%]
tests/test_transcribe_guards.py::test_postprocess_removes_char_loops_and_polishes PASSED [ 83%]
tests/test_transcribe_guards.py::test_postprocess_filters_configured_words_from_text_and_segments PASSED [ 86%]
tests/test_vectordb.py::test_split_sentences_and_cjk_detection PASSED    [ 88%]
tests/test_vectordb.py::test_chunk_text_handles_short_empty_chinese_and_english PASSED [ 91%]
tests/test_vectordb.py::test_build_doc_text_supports_transcript_filename_only_and_bad_json PASSED [ 94%]
tests/test_vectordb.py::test_embed_truncates_before_request PASSED       [ 97%]
tests/test_vectordb.py::test_search_deduplicates_media_results_and_rounds_scores PASSED [100%]

=============================== warnings summary ===============================
../../Library/Python/3.9/lib/python/site-packages/_pytest/config/__init__.py:1474
  <home>/Library/Python/3.9/lib/python/site-packages/_pytest/config/__init__.py:1474: PytestConfigWarning: Unknown config option: asyncio_mode

    self._warn_or_fail_if_strict(f"Unknown config option: {key}\n")

tests/test_vectordb.py::test_split_sentences_and_cjk_detection
  <home>/Library/Python/3.9/lib/python/site-packages/urllib3/__init__.py:35: NotOpenSSLWarning: urllib3 v2 only supports OpenSSL 1.1.1+, currently the 'ssl' module is compiled with 'LibreSSL 2.8.3'. See: https://github.com/urllib3/urllib3/issues/3020
    warnings.warn(

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 36 passed, 2 warnings in 0.48s ========================
```

### 3. HTML parsing

```bash
python3 - <<'PY'
from html.parser import HTMLParser
from pathlib import Path
class Parser(HTMLParser):
    pass
p = Parser()
p.feed(Path('index.html').read_text(encoding='utf-8'))
p.close()
print('HTML parse OK')
PY
```

```text
HTML parse OK
```

### 4. Smoke test

```bash
./smoke-test.sh
```

```text
═══ arkiv Smoke Test (pc) ═══

── Environment ──
<home>/Library/Python/3.9/lib/python/site-packages/urllib3/__init__.py:35: NotOpenSSLWarning: urllib3 v2 only supports OpenSSL 1.1.1+, currently the 'ssl' module is compiled with 'LibreSSL 2.8.3'. See: https://github.com/urllib3/urllib3/issues/3020
  warnings.warn(
*** Terminating app due to uncaught exception 'NSRangeException', reason: '*** -[__NSArray0 objectAtIndex:]: index 0 beyond bounds for empty array'
*** First throw call stack:
(
	0   CoreFoundation                      0x0000000186c4f8fc __exceptionPreprocess + 176
	1   libobjc.A.dylib                     0x0000000186726418 objc_exception_throw + 88
	2   CoreFoundation                      0x0000000186c6e8bc CFArrayApply + 0
	3   libmlx.dylib                        0x000000010a30f80c _ZN3mlx4core5metal6DeviceC2Ev + 204
	4   libmlx.dylib                        0x000000010a312ea8 _ZN3mlx4core5metal6deviceENS0_6DeviceE + 80
	5   libmlx.dylib                        0x000000010a2ea6f8 _ZN3mlx4core5metal14MetalAllocatorC2Ev + 64
	6   libmlx.dylib                        0x000000010a2ea594 _ZN3mlx4core9allocator9allocatorEv + 80
	7   libmlx.dylib                        0x000000010979a9c4 _ZN3mlx4core9allocator6mallocEm + 28
	8   libmlx.dylib                        0x00000001098ba0e4 _ZN3mlx4core5array4initIPKjEEvT_ + 64
	9   libmlx.dylib                        0x00000001098ba01c _ZN3mlx4core5arrayC2IjEESt16initializer_listIT_ENS0_5DtypeE + 156
	10  libmlx.dylib                        0x00000001098b396c _ZN3mlx4core6random3keyEy + 72
	11  core.cpython-39-darwin.so           0x00000001084b5138 PyInit_core + 689296
	12  core.cpython-39-darwin.so           0x00000001084b6a78 PyInit_core + 695760
	13  core.cpython-39-darwin.so           0x000000010840ce58 PyInit_core + 432
	14  Python3                             0x00000001030f0e04 PyImport_AppendInittab + 5440
	15  Python3                             0x00000001030f056c PyImport_AppendInittab + 3240
	16  Python3                             0x0000000103038cec PyCMethod_New + 800
	17  Python3                             0x00000001030c617c _PyEval_EvalFrameDefault + 9956
	18  Python3                             0x00000001030cb134 _PyEval_EvalFrameDefault + 30364
	19  Python3                             0x0000000102ff5bd8 _PyFunction_Vectorcall + 228
	20  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	21  Python3                             0x00000001030c5cb0 _PyEval_EvalFrameDefault + 8728
	22  Python3                             0x0000000102ff5d6c _PyFunction_Vectorcall + 632
	23  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	24  Python3                             0x00000001030c5c88 _PyEval_EvalFrameDefault + 8688
	25  Python3                             0x0000000102ff5d6c _PyFunction_Vectorcall + 632
	26  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	27  Python3                             0x00000001030c656c _PyEval_EvalFrameDefault + 10964
	28  Python3                             0x0000000102ff5d6c _PyFunction_Vectorcall + 632
	29  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	30  Python3                             0x00000001030c656c _PyEval_EvalFrameDefault + 10964
	31  Python3                             0x0000000102ff5d6c _PyFunction_Vectorcall + 632
	32  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	33  Python3                             0x00000001030c656c _PyEval_EvalFrameDefault + 10964
	34  Python3                             0x0000000102ff5d6c _PyFunction_Vectorcall + 632
	35  Python3                             0x0000000102ff6ef0 PyObject_CallMethodObjArgs + 508
	36  Python3                             0x0000000102ff7070 _PyObject_CallMethodIdObjArgs + 112
	37  Python3                             0x00000001030ef160 PyImport_ImportModuleLevelObject + 1360
	38  Python3                             0x00000001030c7304 _PyEval_EvalFrameDefault + 14444
	39  Python3                             0x00000001030cb134 _PyEval_EvalFrameDefault + 30364
	40  Python3                             0x00000001030c39d0 PyEval_EvalCode + 80
	41  Python3                             0x00000001030c033c _PyAST_ExprAsUnicode + 19388
	42  Python3                             0x0000000103038cec PyCMethod_New + 800
	43  Python3                             0x00000001030c617c _PyEval_EvalFrameDefault + 9956
	44  Python3                             0x00000001030cb134 _PyEval_EvalFrameDefault + 30364
	45  Python3                             0x0000000102ff5bd8 _PyFunction_Vectorcall + 228
	46  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	47  Python3                             0x00000001030c5cb0 _PyEval_EvalFrameDefault + 8728
	48  Python3                             0x0000000102ff5d6c _PyFunction_Vectorcall + 632
	49  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	50  Python3                             0x00000001030c5c88 _PyEval_EvalFrameDefault + 8688
	51  Python3                             0x0000000102ff5d6c _PyFunction_Vectorcall + 632
	52  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	53  Python3                             0x00000001030c656c _PyEval_EvalFrameDefault + 10964
	54  Python3                             0x0000000102ff5d6c _PyFunction_Vectorcall + 632
	55  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	56  Python3                             0x00000001030c656c _PyEval_EvalFrameDefault + 10964
	57  Python3                             0x0000000102ff5d6c _PyFunction_Vectorcall + 632
	58  Python3                             0x0000000102ff6ef0 PyObject_CallMethodObjArgs + 508
	59  Python3                             0x0000000102ff7070 _PyObject_CallMethodIdObjArgs + 112
	60  Python3                             0x00000001030ef1ec PyImport_ImportModuleLevelObject + 1500
	61  Python3                             0x00000001030bf4b4 _PyAST_ExprAsUnicode + 15668
	62  Python3                             0x00000001030396d0 PyCFunction_GetFlags + 736
	63  Python3                             0x0000000102ff5a24 _PyObject_Call + 188
	64  Python3                             0x00000001030c617c _PyEval_EvalFrameDefault + 9956
	65  Python3                             0x00000001030cb134 _PyEval_EvalFrameDefault + 30364
	66  Python3                             0x0000000102ff5bd8 _PyFunction_Vectorcall + 228
	67  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	68  Python3                             0x00000001030c656c _PyEval_EvalFrameDefault + 10964
	69  Python3                             0x00000001030cb134 _PyEval_EvalFrameDefault + 30364
	70  Python3                             0x0000000102ff5bd8 _PyFunction_Vectorcall + 228
	71  Python3                             0x0000000102ff6ef0 PyObject_CallMethodObjArgs + 508
	72  Python3                             0x0000000102ff7070 _PyObject_CallMethodIdObjArgs + 112
	73  Python3                             0x00000001030ef160 PyImport_ImportModuleLevelObject + 1360
	74  Python3                             0x00000001030c7304 _PyEval_EvalFrameDefault + 14444
	75  Python3                             0x00000001030cb134 _PyEval_EvalFrameDefault + 30364
	76  Python3                             0x00000001030c39d0 PyEval_EvalCode + 80
	77  Python3                             0x00000001030c033c _PyAST_ExprAsUnicode + 19388
	78  Python3                             0x0000000103038cec PyCMethod_New + 800
	79  Python3                             0x00000001030c617c _PyEval_EvalFrameDefault + 9956
	80  Python3                             0x00000001030cb134 _PyEval_EvalFrameDefault + 30364
	81  Python3                             0x0000000102ff5bd8 _PyFunction_Vectorcall + 228
	82  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	83  Python3                             0x00000001030c5cb0 _PyEval_EvalFrameDefault + 8728
	84  Python3                             0x0000000102ff5d6c _PyFunction_Vectorcall + 632
	85  Python3                             0x00000001030ca3dc _PyEval_EvalFrameDefault + 26948
	86  Python3                             0x00000001030c5c88 _PyEval_EvalFrameDefault + 8688
	87  Python3                             0x0000000102ff5
libc++abi: terminating due to uncaught exception of type NSException
./smoke-test.sh: line 63: 96136 Abort trap: 6           $PYTHON health.py --platform "$PLATFORM"

── Server ──
  ✗ Server reachable HTTP 000000
── API Endpoints ──
  ✗ GET /api/media?limit=1 HTTP 000000
  ✗ GET /api/stats HTTP 000000
  ✗ GET /api/tags HTTP 000000
  ✗ GET /api/duration-by-lang HTTP 000000
  ✗ GET /api/size-by-ext HTTP 000000
── Data ──
  ✗ Media files indexed 0 files
── Search ──
  ✗ Semantic search HTTP 000000
── Static ──
  ✗ index.html served 0 bytes

═══ Result: 0 PASS, 9 FAIL (pc) ═══
```

## ⚠️ REVIEW

- ⚠️ `smoke-test.sh` 在這台 Mac 上失敗，根因不是 Phase 8 程式碼，而是 `health.py` 匯入 MLX / Metal 時直接觸發 `NSRangeException`，導致後續 server / API 檢查全數連帶失敗。
- ⚠️ `pytest.ini` 目前仍有 `asyncio_mode` 警告，因為這個環境的 pytest plugin 組合只有 `anyio`，沒有 `pytest-asyncio`。
- ⚠️ 這次為了讓 baseline 測試回到綠燈，順手把兩個既有測試對齊 production 現況：
  - `_postprocess()` 現在回傳 4-tuple
  - export unsupported format 錯誤訊息已中文化
- ⚠️ repo 目前已有未提交的 `AGENTS.md` 與 `docs/phase8-handover.md`；本次未改動 `AGENTS.md`，也未刪除該 handover 檔。

## 未完成項目

- [ ] `smoke-test.sh` / `health.py` on Mac 全綠
- [ ] commit / push（這次只準備審計材料，尚未提交）

## 與 spec 不一致的實作決策

- `frames.py` 新增了內部 helper `_adaptive_frame_count()`，目的只是把自適應取幀邏輯抽成可測單位，沒有改 public API。
- 沒有修改 `vectordb.py` 與 `resolve_plugin/arkiv_resolve.py`，照 spec 保持不動。
