# Handover: faster-whisper transcription backend (fixes non-Mac ingest)

**Date**: 2026-05-28
**For**: overnight relay agent on mini (Mac M2 Pro). PC is offline.
**Base**: arkiv main `f40eb5a` (Bug A already fixed).

---

## Why this exists

The user-flow smoke (ingest → embed → chat) found that **PC/Linux/CUDA ingest is broken at transcription**. Three layers:

- **A — FIXED** (commit `f40eb5a`): `_current_whisper_guard_settings()` returned the backend *sub-dict* instead of the full layer → `KeyError: 'whisperx'`.
- **B**: the installed **whisperx 3.8.5**'s `FasterWhisperPipeline.transcribe()` no longer accepts `beam_size` / `condition_on_previous_text` / `compression_ratio_threshold` / `log_prob_threshold` / `initial_prompt` (they moved to `load_model(asr_options=...)`).
- **C**: whisperx 3.8.5 pulls torch 2.11 + **torchcodec 0.7**, whose `libtorchcodec` DLLs fail to load on the Windows box (FFmpeg version mismatch).

## Decision — Path Z: route the non-Mac backend to faster-whisper

`requirements.txt` declares **faster-whisper** (not whisperx) as the non-Mac backend, and **faster-whisper 1.2.1 is already installed** and does NOT use torchcodec. Its `WhisperModel.transcribe()` accepts `beam_size` / `condition_on_previous_text` / `compression_ratio_threshold` / `log_prob_threshold` / `initial_prompt` / `word_timestamps` directly — a clean map to the whisper-guard layer config. **This sidesteps B and C with zero package changes.**

## HARD constraints

1. **NO package version changes.** Do not `pip install` / upgrade / downgrade whisperx / torch / torchcodec / faster-whisper / ctranslate2. faster-whisper 1.2.1 + ctranslate2 4.7.1 are already present.
2. **File whitelist**: `transcribe.py`, `config.py` (only if you add a `faster_whisper` config key), `tests/test_transcribe_faster_whisper.py` (new), `CHANGELOG.md`. Do NOT touch anything else. Do NOT touch the vault / dev-logs / todo.
3. **⚠️ This machine is a Mac (MLX path, `_USE_MLX=True`) and CANNOT live-test the faster-whisper CUDA path.** Deliver **code + UNIT tests (mock `WhisperModel`) + commit**. **DO NOT tag or release.** PC must run the live ingest→embed→chat verification on CUDA before shipping. (We have already shipped twice where mocked-on-Mac passed but live-on-PC was broken — be explicit in your result note that live CUDA verification is still pending.)
4. **Do not commit secrets / `.data` / large binaries.** Codex does not commit — if you are Codex, leave commits to the orchestrator per existing protocol; if you are the overnight CC agent, commit per below.

## Implementation

### 1. `warm_up()` — faster-whisper branch (non-Mac)
Replace the `else:` (whisperx) branch so non-Mac loads faster-whisper:
```python
from faster_whisper import WhisperModel
_fw_model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
print("  [faster-whisper on cuda]", flush=True)
```
(`WHISPER_MODEL` is already `large-v3-turbo` on non-Mac. `_fw_model` global already exists.)

### 2. New `_transcribe_faster_whisper(wav, language) -> tuple`
```python
def _transcribe_faster_whisper(wav: str, language: str) -> tuple:
    global _fw_model
    if _fw_model is None:
        warm_up()
    layer = _current_whisper_guard_settings()  # full layer (post Bug A)
    opts = {
        "language": language,
        "beam_size": layer["beam_size"],
        "condition_on_previous_text": layer["condition_on_previous_text"],
        "word_timestamps": True,
    }
    _optional_option(opts, "compression_ratio_threshold", layer["compression_ratio_threshold"])
    _optional_option(opts, "log_prob_threshold", layer["whisperx"]["log_prob_threshold"])
    ip = _build_initial_prompt()
    if ip:
        opts["initial_prompt"] = ip
    segments, info = _fw_model.transcribe(wav, **opts)
    seg_list, words = [], []
    text_parts = []
    for s in segments:                       # generator — must iterate
        text_parts.append(s.text)
        seg_list.append({"start": s.start, "end": s.end, "text": s.text})
        for w in (s.words or []):
            words.append({"word": w.word, "start": w.start, "end": w.end, "score": w.probability})
    text = "".join(text_parts).strip()
    lang = (info.language if info else None) or language
    return _postprocess(text, lang, seg_list, language, words=words)
```
Verify the exact `_postprocess(...)` signature in this file and match it (the mlx/whisperx callers show the contract).

### 3. `transcribe()` dispatcher
Non-Mac `else:` branch → run `_vad_filter(wav)` first (same as the mlx path, reuse the existing helper) then call `_transcribe_faster_whisper(vad_wav, language)`. Mirror the mlx branch's VAD `None`-handling + temp-file cleanup. (Alternatively pass faster-whisper `vad_filter=True` — but prefer reusing arkiv `_vad_filter` so behavior matches the mlx path.)

### 4. `_transcribe_whisperx`
Leave it in the file (unused, harmless) — don't break imports. Optional: gate it behind `ARKIV_TRANSCRIBE_BACKEND=whisperx` if you want it selectable, but default non-Mac = faster-whisper.

### 5. Unit test `tests/test_transcribe_faster_whisper.py`
Mock `faster_whisper.WhisperModel` (or monkeypatch the module-level `_fw_model`) to return fake `segments` (objects/namedtuples with `.text/.start/.end/.words`, each word `.word/.start/.end/.probability`) + a fake `info` with `.language`. Assert `_transcribe_faster_whisper` returns the `(text, lang, segments_list, words_list)` contract and that word `score` maps from `probability`. **No real model load in unit tests.**

### 6. CHANGELOG
Add a `v0.6.1` "Fixed" entry (non-Mac transcription via faster-whisper). **Do NOT git tag** — PC ships it after live verification.

## Verification

- **mini (you)**: `TMP=/c/tmp pytest tests/test_transcribe_faster_whisper.py -v` green + full suite no new failures (note: the 9 known Windows/POSIX fails are PC-only; on Mac the set differs). Commit. Write a short result/resume note. **No tag, no release.**
- **PC (pending, when back)**: live `ARKIV_DB_PATH=<temp> ARKIV_CHROMA_PATH=<temp> ARKIV_THUMBNAILS_DIR=<temp> python ingest.py --dir <2 clips>` → real transcripts produced, **no error** → `embed.py` → start server → chat compilation returns the clips. THEN tag `v0.6.1` + GitHub Release.

## Reference
- Smoke that found this: vault dev-log `references/dev-logs/daily/2026-05-28.md` §8.
- faster-whisper 1.2.1 `WhisperModel.transcribe(...)` — installed package is the source of truth for the signature.
- Existing return contract: see `_transcribe_mlx` / `_postprocess` in this file.
