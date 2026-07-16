#!/usr/bin/env bash
# grok-consult.sh — thin, read-only wrapper around xAI's official Grok Build CLI,
# for use as an external second-opinion agent alongside Claude Code / Codex.
#
# Install the CLI once:  curl -fsSL https://x.ai/cli/install.sh | bash
# Then authenticate:     grok login          (or: grok login --device-code)
#
# This wrapper never edits files. It runs a single headless turn and prints
# Grok's answer to stdout. Modes only change the framing prompt; all are read-only.
#
# Usage:
#   scripts/grok-consult.sh [--mode consult|debate|review] [--model M] "your prompt"
#   echo "your prompt" | scripts/grok-consult.sh --mode review
#
# Exit codes: 0 ok · 3 grok binary missing · 4 not authenticated · other = grok's code.

set -euo pipefail

MODE="consult"
MODEL=""
PROMPT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --mode)  MODE="${2:?--mode needs a value}"; shift 2 ;;
    --model) MODEL="${2:?--model needs a value}"; shift 2 ;;
    -h|--help)
      sed -n '2,20p' "$0"; exit 0 ;;
    --) shift; PROMPT="$*"; break ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *)  PROMPT="$*"; break ;;
  esac
done

# Resolve the grok binary even when the login-shell PATH isn't inherited
# (non-interactive shells don't source ~/.zshrc, where the installer adds it).
GROK=""
for c in "$(command -v grok 2>/dev/null || true)" "$HOME/.grok/bin/grok"; do
  if [ -n "$c" ] && [ -x "$c" ]; then GROK="$c"; break; fi
done
if [ -z "$GROK" ]; then
  echo "grok CLI not found. Install: curl -fsSL https://x.ai/cli/install.sh | bash" >&2
  exit 3
fi

# Read prompt from stdin when not passed as an argument.
if [ -z "$PROMPT" ]; then
  if [ ! -t 0 ]; then PROMPT="$(cat)"; fi
fi
if [ -z "${PROMPT// }" ]; then
  echo "no prompt given (pass as argument or pipe via stdin)" >&2
  exit 2
fi

case "$MODE" in
  consult) FRAME="Act as an independent expert collaborating with another AI agent. Give your own answer, state your assumptions, and name the single strongest uncertainty. Do not edit files." ;;
  debate)  FRAME="Act as a rigorous, truth-seeking counterpart. Engage the strongest version of the argument, revise when warranted, and isolate the remaining disagreement. Do not edit files." ;;
  review)  FRAME="Review the supplied plan, code, or diff. Report concrete findings with evidence and priorities. Do not edit files." ;;
  *) echo "unknown mode: $MODE (consult|debate|review)" >&2; exit 2 ;;
esac

set +e
OUT="$(
  "$GROK" \
    -p "$FRAME

Task:
$PROMPT" \
    --cwd "$PWD" \
    --output-format json \
    --permission-mode plan \
    --disable-web-search \
    ${MODEL:+--model "$MODEL"} 2>/tmp/grok-consult.err
)"
RC=$?
set -e

if echo "$OUT$( cat /tmp/grok-consult.err 2>/dev/null )" | grep -qiE 'not signed in|not authenticated|grok login'; then
  echo "Grok is not authenticated. Run:  grok login   (or: grok login --device-code)" >&2
  exit 4
fi

if [ $RC -ne 0 ]; then
  cat /tmp/grok-consult.err >&2
  exit $RC
fi

# Prefer the .text field of the JSON envelope; fall back to raw output.
if command -v python3 >/dev/null 2>&1; then
  printf '%s' "$OUT" | python3 -c 'import sys,json
raw=sys.stdin.read().strip()
try:
    obj=json.loads(raw)
    print(obj.get("text", raw))
except Exception:
    print(raw)'
else
  printf '%s\n' "$OUT"
fi
