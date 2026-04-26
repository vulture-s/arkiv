# Session Log — 2026-04-24: Privacy Audit + History Purge

## Trigger

User report from Pen (filmmaker beta-tester): previewing a locally-ingested
media file in the arkiv inspector showed Hevin's content (kindergarten
footage) instead of Pen's own file. Suggested that temporary files were
committed to git.

## Root cause

`server.py:1170` serves `proxies/{media_id}.mp4` unconditionally when
that file exists:

```python
proxy_path = config.PROXIES_DIR / f"{media_id}.mp4"
if proxy_path.exists():
    return FileResponse(path=str(proxy_path), ...)
```

The repo had `proxies/` (302 files, ~426 MB of Hevin's personal media)
checked into git — so every fresh clone arrived with the proxy folder
pre-populated. A new user ingesting their first local file received
`media_id=1` from the DB autoincrement, hit the stream endpoint, and
was handed `proxies/1.mp4` — Hevin's content.

`.gitignore` covered `media.db` but not `arkiv.db` (the actual runtime
DB name) and not `proxies/`.

## Scope expansion during audit

The audit found more than just proxies. Every item below was tracked in
git on `main` before this session:

| Path | Why it's a leak |
|------|-----------------|
| `proxies/` (302 files, 426 MB) | Hevin's personal media proxies |
| `arkiv.db` | Runtime DB (empty at HEAD, but tracked with older content in history) |
| `bench_guard_ab_texts{,_mac}.json` (~1 MB) | Full ASR transcripts of Hevin's audio including file titles like `030_健達出奇爛 SP.04...` |
| `bench_guard_ab_results{,_mac}.json` | Transcript summaries + filenames |
| `bench_qwen3_asr{,_aligned}_results.json` | Transcript previews |
| `bench_ingest.json` | 423 camera-original filenames (Sony A7S / FX30 / cinema cameras) — shoot dates encoded in names |
| `bench_guard_ab.py` | Hardcoded `/Users/hevinyeh/voice-corpus/kinderegg` |
| `bench_qwen3_asr.py` | Hardcoded `C:\Users\user\.gemini\antigravity\scratch\...` |
| `bench_qwen3_asr_aligned.py` | Hardcoded `C:\Users\user\.arkiv\test_*.wav` |
| `test_long_414s.wav` / `test_short_10s.wav` | Personal test audio |
| `docs/phase8-handover.md` | 7 references to `/Users/hevinyeh/Desktop/arkiv` |
| `.claude/handover*.md` (3 files) | Personal paths + private DB snapshots (e.g. "61 筆") |
| `screenshot.jpg` | UI screenshot with visible filenames (`A001_*`, `FX30.*`) and project path (`H:/Project_V1-LAN/.../iphone 16pro/`) |

No API keys, tokens, or `.env` files were tracked.

## Fixes applied (working-tree cleanup on `main`)

Five privacy commits, pushed to `main` via fast-forward merge from
`claude/new-session-0m51U`:

1. Untrack `proxies/` (302 files) + `arkiv.db`
2. Extend `.gitignore` (`*.db`, `arkiv.db`, `proxies/`)
3. Untrack all bench artefacts, personal test wavs; scrub
   `/Users/hevinyeh/Desktop/arkiv` from `docs/phase8-handover.md`
4. Untrack `.claude/` handover notes; add `.claude/` to gitignore;
   add `scripts/purge-history.sh`
5. Redact filenames + project path in `screenshot.jpg` (pixel-level
   overwrite with placeholders `clip_01..04`, `sample_clip.mov`,
   `D:/Projects/demo/sample_clip.mov`)

## Branch cleanup

- `claude/new-session-0m51U` → merged into `main`, then deleted
- `claude/continue-fixes-SY9Eh` → already fully in main, deleted
- `claude/add-claude-documentation-fJZg8` → renamed to
  `archive/pre-reset-phase5-7` to preserve the 22 unique commits from
  the pre-reset parallel history (they have no common ancestor with
  current `main`; the work was redone rather than cherry-picked)

## History rewrite (purge)

`scripts/purge-history.sh` runs `git filter-repo` against a mirror
clone to strip every known-private path from all commits + force-push
to GitHub. Verified afterwards via an independent mirror clone:

- `proxies/` in history: **0** occurrences ✓
- `arkiv.db` / test wavs / `.claude/handover*` in history: **0** ✓
- `bench_*` in history: **1** — this is `bench_stt.py`, which was
  deliberately kept (no personal paths, uses CLI args)
- `/Users/hevin*` in blobs: **0** ✓
- `hevinyeh` text: **4** — all in commit messages of the privacy
  commits (filter-repo `--replace-text` rewrites blobs, not commit
  messages, and the messages quoted the string to explain what was
  being scrubbed)

Repo size dropped from ~850 MB (mirror clone) to ~350 MB.

Tags were rewritten and force-pushed:
`v0.1.0`, `v0.2.0`, `v0.2.1`, `mac-snapshot-20260331`, `pc-snapshot-20260331`.

## Residual work

1. **Commit-message scrub (optional).** The 4 remaining `hevinyeh`
   references are inside commit messages of the privacy commits where
   the string was quoted to describe what was being removed. They are
   one step removed from the blob leaks (not a live path, just a
   description of what was purged), but if full erasure is wanted,
   follow up with `git filter-repo --replace-message`:

   ```bash
   cd ~/tmp-purge
   rm -rf arkiv-purge-2.git
   git clone --mirror https://github.com/vulture-s/arkiv.git arkiv-purge-2.git
   cd arkiv-purge-2.git
   cat > /tmp/msg-replacements.txt <<'EOF'
   /Users/hevinyeh/Desktop/arkiv==><repo>
   /Users/hevinyeh==><home>
   hevinyeh==><user>
   EOF
   git filter-repo --replace-message /tmp/msg-replacements.txt --force
   git remote add origin git@github.com:vulture-s/arkiv.git
   git push --force --all && git push --force --tags
   ```

2. **GitHub Support purge request.** Unreachable commits from the
   pre-rewrite history are kept in GitHub's reflog (~90 days). To
   expire them immediately, open a support ticket at
   <https://support.github.com/contact> requesting garbage-collection
   of unreachable objects on `vulture-s/arkiv`.

3. **Collaborator notification.** Anyone with a local clone MUST
   delete and re-clone — `git pull` on an old clone would merge the
   pre-purge history back and potentially re-introduce the private
   files. Unpushed work is still safe in their local `.git/`; they
   can cherry-pick it onto the new `main` after re-cloning.

4. **Forks.** GitHub forks cannot be force-updated by the source
   repo. Check <https://github.com/vulture-s/arkiv/network/members>
   and contact fork owners if the data must be purged from their
   copies.

## Preventative changes

- `.gitignore` now covers `*.db`, `arkiv.db`, `proxies/`, `bench_*.json`,
  personal bench scripts, test wavs, and `.claude/`. Future accidental
  commits of these patterns will be blocked.
- `scripts/purge-history.sh` is checked in for future incidents —
  takes a mirror clone + bundle backup + dry-run gate (`SKIP_PUSH=1`)
  + verification before push.

## Bug fix still pending

`server.py:1170` still trusts the existence of `proxies/{media_id}.mp4`
without validating that the proxy belongs to the current DB's record.
If another installation ever ends up with a cross-contaminated
`proxies/` directory (e.g. a user copies the directory between
machines), the same bug reappears.

Defensive fix candidates (not implemented this session):
- Hash the source file path into the proxy filename so DB-to-proxy
  mapping is collision-proof across installations
- Store the proxy's source path in DB and verify on serve
- Reject serving any proxy whose mtime predates the DB record's
  `ingested_at`

Pen's report was resolved by the data-side fix (clean `proxies/` for
new clones + history purge). The code-side defensive fix is tracked
for a follow-up session.

---

## Continuation — same day, post-purge

### Defensive fix landed (`fd2ed4b`)

Picked the first candidate from the list above: hash the absolute
source path into the proxy filename. Two different machines now
cannot produce the same proxy filename for the same `media_id`.

- `config.py`: new `proxy_path_for(media_id, abs_source_path)` helper
  → `PROXIES_DIR / f"{media_id}_{sha1(abs_source_path)[:10]}.mp4"`
- `ingest.py`: `generate_proxy()`, Phase 3 of `run_ingest`,
  `_regenerate_proxies()` all use the helper. The regeneration loop
  also deletes any legacy `{id}.mp4`-style file it finds (those may
  be cross-contaminated and cannot be trusted).
- `server.py`: `stream_media`, `proxy_status`, `proxy_build` now
  resolve via the helper; existence checks compare against the
  expected hashed filename rather than scanning the dir.

Regression tests added in `tests/test_server.py`:

- `test_proxy_filename_is_scoped_by_source_path` — same id with
  different source paths produces different filenames; deterministic
  for identical inputs.
- `test_stream_ignores_legacy_proxy_from_another_install` — pre-
  populates `proxies/1.mp4` with sentinel bytes "DO-NOT-SERVE", calls
  `GET /api/stream/1`, asserts response is **not** 200 and the bytes
  are not returned.

`pytest tests/test_server.py` → 13 passed (was 11). Two pre-existing
failures in `tests/test_phase8.py` (`_is_usable_frame` colour checks)
are unrelated to this change.

### Follow-up scripts checked in (`f0300d4`)

- `scripts/scrub-commit-messages.sh` — `git filter-repo
  --replace-message` driver to remove the residual `hevinyeh` strings
  that remain in commit messages of the privacy commits. The first
  purge was blob-only.
- `scripts/github-support-ticket-draft.md` — pre-filled ticket body
  for requesting GitHub to garbage-collect unreachable objects so the
  pre-rewrite SHAs can no longer be fetched within the reflog window.

### Hardening on `purge-history.sh`

`scripts/purge-history.sh` was sanitised so the script itself does
not contain the personal strings it was originally written to scrub.
The replacement table is now self-referential placeholders rather
than literal `/Users/<name>/...` paths. The script's destructive
behaviour and verification gates (dry-run, leak check before push)
are unchanged.

### State at end of session

| Item | Status |
|------|--------|
| Working-tree leaks on `main` | resolved (5 commits) |
| History purge (blobs) | done |
| Branch cleanup | done (3 deleted, 1 archived) |
| Server defensive fix | **done** (`fd2ed4b`) |
| Commit-message scrub | **script ready, awaiting owner run** |
| GitHub Support ticket | **draft ready, awaiting submission** |
| Collaborator re-clone | not yet — owner to coordinate |
| Fork owner notification | open (check forks list) |
