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
  `consult` (default), `debate`, `review`. Exit codes: `0` ok, `3` CLI missing,
  `4` not authenticated.

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
