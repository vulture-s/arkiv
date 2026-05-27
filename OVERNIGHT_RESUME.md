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

**B.4a dispatch** — `codex login` pending (last gap).

### Mac pre-flight chain audit (Hevin 拍板)

> 另一邊在 ssh mini relay 之前沒有全盤評估本機安裝的依賴 — 列入審計檢討

跨 surface dispatch 前 missing-dep 三層連環中：

| 層 | 狀態 | 怎麼解 |
|----|------|--------|
| `codex` CLI | ✅ 已 `brew install codex` (0.134.0) + ripgrep | done |
| `codex` CLI 語法 | ✅ 新版改 `codex cloud exec --env <ENV_ID> "<prompt>"`，舊 `task --background` 已 deprecated | 早上 dispatch 用新語法 |
| `codex login` | 🚫 Not logged in，OAuth (ChatGPT) 要互動 browser | 早上 `codex login` 走一次 OAuth flow |
| arkiv remote auth | ✅ 改 SSH (`git@github.com:vulture-s/arkiv.git`) | done |
| Mac `.venv` + deps | ✅ 已建 + pip install ~600 MB | done |
| Mac statusline | ✅ 對齊 M2 Max preset `ship-feat` + settings.json `statusLine` 字段補上 | done |

每一層都應該 pre-flight 一次再開鏈，不是邊走邊撞。已寫進 hevin-ai-os `references/dev-logs/daily/2026-05-27.md §audit`。

---

## Morning resume steps

1. **One-time auth** (Mac 或 PC，任一台沒登過的)：
   ```
   codex login                        # browser OAuth
   codex cloud list                   # 找 ENV_ID（之前 PC dispatch 用的那個 env）
   ```
2. `cd ~/code/arkiv && git pull origin main` (pick up `75e4a8a` + `934ef1d`)
3. Verify B.0：`.venv/bin/pytest tests/test_llm_router.py -v` → 6/6 PASS (Mac 已驗，PC 拉新 commit 後重驗即可)
4. **Dispatch B.4a**（新語法）：
   ```
   codex cloud exec --env <ENV_ID> "$(cat <<'EOF'
   Implement Feature B Iteration 2 sub-dispatch a (B.4a) per
   docs/chat-rag-4a-handover.md in this arkiv repo. 完整 spec 在
   handover doc, 含 hard constraints + verify steps + commit msg.
   EOF
   )"
   ```
   會印 task-id（類似 `task-mpnXXXXX-xxxxxx`），記下來。
5. 等 Codex 返回 (est 30-60 min，exit 0)：拉它寫的 diff (`codex cloud apply <task-id>` 或 `git pull` 看 working tree)
6. Mac/PC verify：`.venv/bin/pytest tests/test_chat.py -v` 應該全 PASS + `.venv/bin/pytest tests/ -v` 9 pre-existing fail（或 Mac 上的 6 個 platform fail）不變
7. Commit per handover-suggested msg + push
8. Then B.4b → B.4c sequentially per `docs/chat-rag-4b-handover.md` 和 `4c-handover.md`

## Files state

- Working tree clean on Mac (B.0 8-file stage all committed in `75e4a8a`).
- arkiv `.venv` created on Mac, pip installed full `requirements.txt` + `requirements-dev.txt` (~600 MB; includes mlx-whisper, chromadb, silero-vad, torch). Mac can run full suite locally now.
- arkiv remote `origin` switched HTTPS → SSH this session.

---

**STOP condition**: 5hr session limit + codex CLI gap = clean stop after B.0 ship.
