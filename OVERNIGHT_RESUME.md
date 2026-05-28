# OVERNIGHT RESUME — 2026-05-28 → mini relay

PC is offline. One task queued for the overnight relay agent on mini.

## Task: faster-whisper transcription backend (fixes non-Mac ingest)

**Spec**: `docs/transcribe-fasterwhisper-handover.md` (read it fully first).
**Base**: main `f40eb5a` (Bug A already fixed; `git pull` first).

### TL;DR
The user-flow smoke found PC/Linux/CUDA **ingest is broken at transcription** (whisperx 3.8.5 API drift + torchcodec DLL failure). Fix = route the non-Mac backend to **faster-whisper** (already installed 1.2.1, no torchcodec, clean API map). Add `_transcribe_faster_whisper` + a `warm_up` branch + dispatcher routing + a mocked unit test.

### HARD constraints
1. **No package version changes** (no pip install/upgrade/downgrade — faster-whisper 1.2.1 is already present).
2. **File whitelist**: `transcribe.py`, `config.py` (only if needed), `tests/test_transcribe_faster_whisper.py`, `CHANGELOG.md`. Nothing else. Do NOT touch the vault.
3. **⚠️ mini is a Mac (MLX path) — it CANNOT live-test the faster-whisper CUDA path.** Deliver code + UNIT tests (mock `WhisperModel`) + commit. **DO NOT tag / release.** State clearly in your result note that **live CUDA ingest→embed→chat verification is still pending on PC** (we've been bitten twice by mocked-on-Mac passing while live-on-PC broke).

### Done =
- `pytest tests/test_transcribe_faster_whisper.py -v` green + full suite no new fails.
- committed to main, CHANGELOG `v0.6.1` entry added, **NOT tagged**.
- a short resume/result note for PC to pick up (what was done, what live verification remains).

When the chain is complete, delete this file (`OVERNIGHT_RESUME.md`).
