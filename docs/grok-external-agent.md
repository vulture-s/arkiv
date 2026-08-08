# Grok as an external collaborator agent

This wires **Grok** (xAI's official Grok Build CLI) into arkiv as a read-only
second-opinion agent, alongside the existing Codex rescue path. It is Approach A
from the Grok-Build-Connector evaluation: skip the third-party connector skill and
call the official CLI directly through a thin wrapper + a harness-native subagent.

## Why not the Grok-Build-Connector repo

[`Toolsai/Grok-Build-Connector`](https://github.com/Toolsai/Grok-Build-Connector)
is a real, security-clean Agent Skill, but it is hard-wired to Codex (its Live UI
requires the Codex in-app browser and it mentions Claude zero times), ships as a
single unlicensed commit, and its only non-portable value is the Live UI. The
underlying dependency — the official `grok` CLI — is the actual integration point,
and calling it directly is ~60 lines we control instead of an unlicensed dependency.

The connector's CLI flags (`--output-format json`, `--permission-mode plan`,
`--cwd`, `--no-auto-update`, `--device-auth`) were all verified as real against
`grok 0.2.101` — including the hidden/aliased ones.

## One-time setup

```bash
# 1. Install the official CLI (adds ~/.grok/bin to PATH via your shell rc)
curl -fsSL https://x.ai/cli/install.sh | bash

# 2. Authenticate (opens a browser; use --device-code on headless/remote machines)
grok login              # or: grok login --device-code

# 3. Confirm
grok models             # should list models without "not authenticated"
```

Grok 4.5 is free in Grok Build for a **limited time** per xAI; availability and
limits are xAI's to change.

## What's in the repo

- **`scripts/grok-consult.sh`** — committed, reproducible wrapper. Read-only by
  design (`--permission-mode plan`, never `--allow-writes`). Resolves the `grok`
  binary even when a non-interactive shell hasn't sourced the login PATH. Modes:
  `consult` (default), `debate`, `review`. Exit codes: `0` ok, `2` bad usage,
  `3` CLI missing, `4` not authenticated. Its stderr scratch file comes from
  `mktemp` rather than a fixed `/tmp` name, so two consultations running at once
  can't cross-report each other's failures.

  ```bash
  scripts/grok-consult.sh --mode consult "Is a single-writer SQLite fine for the ingest queue?"
  echo "review this plan: ..." | scripts/grok-consult.sh --mode review
  ```

- **`.claude/agents/grok-consult.md`** — harness-native Claude Code subagent that
  forwards to the wrapper. Runs inside the Claude Code framework (auto-notifies on
  completion — unlike a raw background `codex` task that can silently die), so the
  main thread can hand off with `Use the grok-consult subagent to ...`.

  **Note:** `.claude/` is gitignored in this repo, so the agent definition is not
  committed. Recreate it locally by copying the template below into
  `.claude/agents/grok-consult.md`.

## Agent definition template

```markdown
---
name: grok-consult
description: Use when the main thread wants an independent second opinion, a devil's-advocate debate, or a read-only review from Grok as an external collaborator agent. Read-only. Requires the grok CLI installed and authenticated.
model: sonnet
tools: Bash
---

You are a thin forwarding wrapper around the local Grok Build CLI.
Use exactly one Bash call to invoke `scripts/grok-consult.sh` from the repo root,
pick --mode consult|debate|review from the request, pass the question as the final
quoted argument, and return the script's stdout as-is. Read-only — never writes.
If it exits 3 (CLI missing) or 4 (not authenticated), relay that verbatim.
Model agreement is not verification; the main thread must still verify claims.
```

## Guardrails

- **Read-only.** The wrapper never edits files. If Grok should implement something,
  the main thread does that explicitly in an isolated worktree — not this path.
- **Not verification.** Grok agreeing with Claude is a signal, not proof. Verify
  important conclusions independently (arkiv's Verification Gate still applies).
- **Web search disabled** in the wrapper (`--disable-web-search`) to keep
  consultations grounded in the provided context; drop that flag if you want Grok
  to browse.

## How the wrapper decides what happened

Worth writing down, because the obvious two approaches are both wrong.

**The exit code can't be the signal.** Verified against `grok 0.2.101`: an
unauthenticated `grok` prints `You are not authenticated.` to **stdout**, leaves
stderr empty, and exits **0**. Trusting the exit code would hand that banner back
as if it were Grok's answer.

**Pattern-matching the output can't be the signal either.** The first version of
this wrapper grepped stdout+stderr for `not signed in|not authenticated|grok login`.
But stdout is *Grok's own answer* — so asking it to review a login flow, or to
explain a `not authenticated` error, tripped the check and the wrapper reported a
login failure and discarded a reply that had already been paid for. The check also
ran ahead of the exit-code branch, so even a fully successful call was affected.

**What it does instead**: classify on whether an answer envelope came back.
`--output-format json` makes a successful turn return `{"text": ...}`; the
unauthenticated banner is plain prose. A leading `{` separates the two without
needing a JSON parser, so the discriminator still holds on a box without `python3`
(which is only used to unwrap `.text` for readability). Auth strings are consulted
**only when there is no envelope** — at which point the output really is a status
message rather than an answer.

## Verification status

- ✅ `grok 0.2.101` installed; all connector flags confirmed against real `--help`.
- ✅ **Live round-trip verified 2026-08-09** — `grok -p "…" --output-format json
  --permission-mode plan --disable-web-search` returned a real
  `{"text": "OK", "stopReason": "EndTurn", …}` envelope, rc 0, empty stderr.
  (The envelope also reports `total_cost_usd`; that call cost ~$0.031, so this is a
  metered path, not a free one — worth knowing before wiring it into a loop.)
- ✅ `tests/test_grok_consult.py` — 12 tests, no credentials and no network: every
  branch runs against a stub `grok` on a scrubbed `PATH`/`HOME`. Includes a pin that
  the read-only guarantee (`--permission-mode plan`, never `--allow-writes`) is
  still in the file, and a regression test for the auth-misclassification above.
  Both fixes were mutation-checked — the two new guards fail against the previous
  version of the script, so they are not decoration.
- ⚠️ **Plan mode is trusted, not proven.** "Never writes" rests entirely on the
  external CLI honouring `--permission-mode plan`, one flag deep, and the wrapper
  passes `--cwd "$PWD"` (the repo). Nothing here would catch xAI changing that
  flag's meaning. Treat the guarantee as a vendor contract, not an enforced one.
