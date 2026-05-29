# OVERNIGHT RESUME — 2026-05-28 → PC live verification

Mini coding leg **DONE** (M2 Max CC agent, 2026-05-29). The faster-whisper backend
is implemented + unit-tested + committed to main, **NOT tagged**. One leg remains:
**live CUDA ingest→embed→chat on PC**, then tag `v0.6.1`.

> **Fresh-device bootstrap (read before the PC leg).** arkiv is a `requirements.txt`
> + venv project — it has **no `pyproject.toml`**, so `uv sync` fails. Install with:
> `python -m venv .venv && pip install -r requirements.txt -r requirements-dev.txt`
> (PC also needs `pip install faster-whisper torch` for the CUDA path). If the system
> Python is bleeding-edge (e.g. 3.14), pin a stable one (3.11–3.12) — chromadb/torch
> have no wheels for the newest releases yet. Smoke baseline on a fresh clone is
> **`pytest tests/test_mhl.py tests/test_offload.py` → 7 passed, 1 skipped** (the
> `test_native_c4_reference_matches_chain` skip is by-design: its reference fixture
> lives in the gitignored `tests/fixtures/`). The full per-device procedure is
> codified in hevin-ai-os `references/sop/arkiv-device-deploy.md`.

## Task that was done: faster-whisper transcription backend (fixes non-Mac ingest)

**Spec**: `docs/transcribe-fasterwhisper-handover.md`.
**Shipped on**: main (see the `fix(transcribe): faster-whisper backend` commit).

### What was delivered (Mac side)
- `transcribe.py`:
  - `_non_mac_backend()` — resolves the non-Mac backend; defaults to `faster-whisper`, `ARKIV_TRANSCRIBE_BACKEND=whisperx` forces the legacy path.
  - `warm_up()` non-Mac branch loads `faster_whisper.WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")` (lazy import, so the module still imports on a box without faster-whisper).
  - `_transcribe_faster_whisper(wav, language)` — maps the whisper-guard layer (`beam_size` / `condition_on_previous_text` / `compression_ratio_threshold` / `log_prob_threshold` / `initial_prompt` / `word_timestamps`) onto `WhisperModel.transcribe()`, builds segment dicts **including the guard keys** (`no_speech_prob` / `avg_logprob` / `compression_ratio`) so `_postprocess`'s anti-hallucination guards work like the whisperx path, and maps word `probability → score`.
  - `transcribe()` non-Mac dispatch runs arkiv `_vad_filter` first (mirrors the MLX path: `None` → empty contract, temp-file cleanup) then `_transcribe_faster_whisper`.
  - `_transcribe_whisperx` left intact, reachable via the env override.
- `tests/test_transcribe_faster_whisper.py` — 7 tests, mock `WhisperModel` via monkeypatched `_fw_model` (no real model load): contract shape, word score mapping, guard-option mapping, empty segments, no-words segment, dispatcher routing, VAD-no-speech.
- `CHANGELOG.md` — `v0.6.1` "Fixed" entry. **No git tag.**

### Verification done (Mac M2 Max)
- `pytest tests/test_transcribe_faster_whisper.py -v` → **7 passed**.
- full suite → **190 passed / 3 skipped / 0 failed** (baseline was 183/3, +7 new). Zero regressions.
- ⚠️ **Mocked only.** This Mac has no faster-whisper (it's a `platform_system!="Darwin"` dep) and no CUDA, so the real model path was never exercised. We've been bitten twice by mocked-on-Mac passing while live-on-PC broke (v0.5.1 NumPy crash, this whisperx drift) — do NOT trust the green tests as ship signal.

## PC leg (pending — do this when PC is back)
1. `git pull` (faster-whisper 1.2.1 should already be installed on PC; if not, it's in requirements.txt for non-Darwin).
2. Live run, isolated paths:
   ```
   ARKIV_DB_PATH=<temp> ARKIV_CHROMA_PATH=<temp> ARKIV_THUMBNAILS_DIR=<temp> \
     python ingest.py --dir <2 clips>      # real transcripts produced, NO error
   python embed.py                          # build index
   # start server → chat compilation returns the clips
   ```
3. If clean: `git tag v0.6.1 && git push --tags` + GitHub Release. **Then delete this file.**
4. If broken: capture the real error, fix in `transcribe.py`, re-verify. The likely risk areas are the exact `WhisperModel.transcribe()` kwarg names on the installed faster-whisper version and the `Segment`/`Word` attribute names — confirm against the installed package, not from memory.

When PC has verified + tagged, delete this file (`OVERNIGHT_RESUME.md`).
