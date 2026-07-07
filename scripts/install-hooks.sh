#!/usr/bin/env bash
# arkiv — enable the versioned git hooks in .githooks/
# Run once per clone:  bash scripts/install-hooks.sh
#
# Git does not auto-enable repo-tracked hooks (.git/hooks is local + untracked),
# so each contributor points core.hooksPath at the versioned .githooks/ directory.
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true

echo "✓ core.hooksPath → .githooks (hooks enabled for this clone)"
echo "  active hooks: $(ls -1 .githooks 2>/dev/null | tr '\n' ' ')"
