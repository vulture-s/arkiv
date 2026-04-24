#!/usr/bin/env bash
#
# scrub-commit-messages.sh — remove leftover "hevinyeh" / "/Users/hevinyeh"
# strings that remain in commit messages after the initial history purge
# (purge-history.sh only scrubs blob content, not commit messages).
#
# *** DESTRUCTIVE — rewrites history, changes all SHAs, force-pushes. ***
# Run the same follow-up tasks listed at the end of purge-history.sh
# (GitHub Support ticket, collaborator re-clone) after this completes.
#
# Usage:
#   cd ~/tmp-purge          # or any scratch directory
#   /path/to/arkiv/scripts/scrub-commit-messages.sh
#
# Env vars:
#   ARKIV_REMOTE — override remote URL
#                  (default: git@github.com:vulture-s/arkiv.git)
#   WORKDIR      — override mirror clone path
#                  (default: arkiv-msg-scrub.git)
#   SKIP_PUSH=1  — stop before force-push for manual inspection

set -euo pipefail

REMOTE="${ARKIV_REMOTE:-git@github.com:vulture-s/arkiv.git}"
WORKDIR="${WORKDIR:-arkiv-msg-scrub.git}"

if ! command -v git-filter-repo >/dev/null 2>&1; then
    echo "error: git-filter-repo not installed" >&2
    echo "  brew install git-filter-repo" >&2
    exit 1
fi

if [[ -e "$WORKDIR" ]]; then
    echo "error: $WORKDIR already exists; remove it or set WORKDIR=..." >&2
    exit 1
fi

echo "==> mirror-clone $REMOTE -> $WORKDIR"
git clone --mirror "$REMOTE" "$WORKDIR"

echo "==> safety bundle"
( cd "$WORKDIR" && git bundle create ../arkiv-pre-msg-scrub.bundle --all )

cd "$WORKDIR"

REPLACE_FILE="$(mktemp)"
cat > "$REPLACE_FILE" <<'EOF'
/Users/hevinyeh/Desktop/arkiv==><repo>
/Users/hevinyeh==><home>
hevinyeh==><user>
EOF

echo "==> rewrite commit messages"
git filter-repo --replace-message "$REPLACE_FILE" --force
rm -f "$REPLACE_FILE"

echo "==> verify"
if git log --all --format="%B" | grep -m1 "hevinyeh" >/dev/null; then
    echo "error: 'hevinyeh' still appears in a commit message" >&2
    exit 2
fi
echo "  ok: no 'hevinyeh' text in any commit message"

if [[ "${SKIP_PUSH:-0}" == "1" ]]; then
    echo "==> SKIP_PUSH=1 — stopping before force-push"
    echo "    inspect $WORKDIR, then:"
    echo "      cd $WORKDIR"
    echo "      git remote add origin $REMOTE"
    echo "      git push --force --all && git push --force --tags"
    exit 0
fi

echo "==> force-push"
git remote add origin "$REMOTE"
git push --force --all
git push --force --tags

echo
echo "==> done. Same follow-ups as purge-history.sh apply (GitHub Support"
echo "    purge request, collaborator re-clone, open PR rebase, fork"
echo "    owner notification)."
