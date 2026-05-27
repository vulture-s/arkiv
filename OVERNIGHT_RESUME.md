# OVERNIGHT_RESUME — Feature B Iter 1 ship + Iter 2 BLOCKED

**Session**: Mac CC (vulturemacmini), 2026-05-27 night → 2026-05-28 early AM
**Last commit on main**: `75e4a8a refactor(llm): extract llm.py abstraction (drop-in)` (pushed origin/main)

---

## What shipped

**B.0 LLM router refactor** — DONE, pushed to `origin/main`
- Codex Job 2a `task-mpnz4mle-y58xyc` (PC, 12 min, exit 0) produced 8-file stage
- Mac verify: `pytest tests/test_llm_router.py -v` → **6/6 PASS**
- Mac verify: `pytest tests/ -v` → **161 passed / 6 failed / 2 skipped** (total 169)
- Commit `75e4a8a` push success `1aefbc5..75e4a8a` via SSH (HTTPS auth missing, switched remote `origin` to `git@github.com:vulture-s/arkiv.git`)

### Test deviation vs handover baseline

Handover doc expected `160 passed / 9 pre-existing fails` (PC baseline). Mac got `161 passed / 6 fails`. Counts: 169 = 160+9 = 161+6+2 ✅ same total.

All 6 fails are in `test_mhl.py` (1) + `test_offload.py` (5). Error pattern: `git show feat/13.1-mhl-v2:mhl.py` returns 128 with Windows hardcoded `safe.directory=C:/Users/user/.arkiv` leaking into subprocess invocation on Mac. **Zero overlap with B.0 refactor scope** (vision/transcribe/vectordb/config/llm — all unaffected). Classified as platform-specific pre-existing failures, not regression.

If you want to reverse: `git revert 75e4a8a && git push origin main`.

## Small cleanup nit (not blocking)

B.0 diff left two unused imports:
- `vision.py` line ~6: `import requests` (now unused after vision call moved to `llm.vision`)
- `vectordb.py` line ~7: `import requests` (now unused; `OLLAMA_EMBED_URL` module var also dead)

Can clean in B.4c or a separate micro-commit.

---

## What's BLOCKED

**B.4a dispatch** — codex CLI not installed on Mac (`which codex` → not found, checked `/usr/local/bin`, `/opt/homebrew/bin`, `~/.local/bin`, `npm -g`, `pipx`). Cannot dispatch overnight from this machine.

### Audit point (raised by Hevin)

> 另一邊在 ssh mini relay 之前沒有全盤評估本機安裝的依賴 — 列入審計檢討

This session reproduced the same anti-pattern: I committed to "Mac → dispatch B.4a" before verifying `codex` exists locally. The dep-inventory pre-flight check needs to be a hard step before any cross-surface dispatch promise. See hevin-ai-os 2026-05-27 dev-log §audit for the cross-surface audit entry.

---

## Morning resume steps (from PC where codex CLI lives)

1. `cd ~/code/arkiv && git pull origin main` (pick up `75e4a8a`)
2. Verify B.0 on PC: `pytest tests/test_llm_router.py -v` → expect 6/6 PASS
3. Dispatch B.4a:
   ```
   codex task --background --write
   ```
   Prompt:
   > Implement Feature B Iteration 2 sub-dispatch a (B.4a) per `docs/chat-rag-4a-handover.md` in this arkiv repo. 完整 spec 在 handover doc, 含 hard constraints + verify steps + commit msg.
4. After Codex returns (exit 0, est ~30-60 min): Mac or PC verify (`pytest tests/test_chat.py -v` + `pytest tests/ -v` no regression), commit with handover-suggested msg, push.
5. Then B.4b → B.4c sequentially per `docs/chat-rag-4b-handover.md` and `4c-handover.md`.

## Files state

- Working tree clean on Mac (B.0 8-file stage all committed in `75e4a8a`).
- arkiv `.venv` created on Mac, pip installed full `requirements.txt` + `requirements-dev.txt` (~600 MB; includes mlx-whisper, chromadb, silero-vad, torch). Mac can run full suite locally now.
- arkiv remote `origin` switched HTTPS → SSH this session.

---

**STOP condition**: 5hr session limit + codex CLI gap = clean stop after B.0 ship.
