#!/usr/bin/env bash
#
# purge-history.sh — rewrite arkiv git history to remove private data
# that was committed by mistake (proxies/, benchmark transcripts,
# personal paths, etc.).
#
# *** DESTRUCTIVE ***
# This rewrites every commit on every branch and every tag. All commit
# SHAs change. The script force-pushes to GitHub at the end.
#
# Before running:
#   1. Install git-filter-repo:
#        brew install git-filter-repo         # macOS
#        pip install git-filter-repo          # or via pip
#   2. Notify every collaborator to stop pushing and prepare to re-clone.
#   3. Make sure no open PRs have unmerged work you need to preserve
#      (PR branches will need to be rebased + force-pushed afterwards).
#   4. Run from an empty scratch directory — the script creates a fresh
#      mirror clone; it will NOT touch your working copy.
#
# Usage:
#   cd ~/tmp-scratch
#   /path/to/arkiv/scripts/purge-history.sh
#
# Env vars:
#   ARKIV_REMOTE  — override the remote URL (default: vulture-s/arkiv)
#   WORKDIR       — override the mirror clone path (default: arkiv-purge.git)
#   SKIP_PUSH=1   — run the rewrite and verify, but don't push (dry run)

set -euo pipefail

REMOTE="${ARKIV_REMOTE:-https://github.com/vulture-s/arkiv.git}"
WORKDIR="${WORKDIR:-arkiv-purge.git}"

if ! command -v git-filter-repo >/dev/null 2>&1; then
    echo "error: git-filter-repo not installed" >&2
    echo "  brew install git-filter-repo   # macOS" >&2
    echo "  pip install git-filter-repo    # via pip" >&2
    exit 1
fi

if [[ -e "$WORKDIR" ]]; then
    echo "error: $WORKDIR already exists; remove it or set WORKDIR=..." >&2
    exit 1
fi

echo "==> mirror-clone $REMOTE -> $WORKDIR"
git clone --mirror "$REMOTE" "$WORKDIR"

echo "==> write safety bundle to $(pwd)/arkiv-pre-purge.bundle"
( cd "$WORKDIR" && git bundle create ../arkiv-pre-purge.bundle --all )

cd "$WORKDIR"

echo "==> strip private paths from every commit"
git filter-repo \
    --path proxies \
    --path arkiv.db \
    --path media.db-shm \
    --path media.db-wal \
    --path test_long_414s.wav \
    --path test_short_10s.wav \
    --path bench_guard_ab.py \
    --path bench_qwen3_asr.py \
    --path bench_qwen3_asr_aligned.py \
    --path bench_guard_ab_results.json \
    --path bench_guard_ab_results_mac.json \
    --path bench_guard_ab_texts.json \
    --path bench_guard_ab_texts_mac.json \
    --path bench_ingest.json \
    --path bench_qwen3_asr_aligned_results.json \
    --path bench_qwen3_asr_results.json \
    --path .claude/session-log-2026-04-18.md \
    --path .claude/handover.md \
    --path .claude/handover-current-status.md \
    --path .claude/handover-whisperx.md \
    --invert-paths

echo "==> scrub personal strings from blobs that remain"
REPLACE_FILE="$(mktemp)"
cat > "$REPLACE_FILE" <<'EOF'
<repo>==><repo>
<home>==><home>
<user>==><user>
EOF
git filter-repo --replace-text "$REPLACE_FILE" --force
rm -f "$REPLACE_FILE"

echo "==> verify: no private paths remain in history"
LEAKED=$(git log --all --pretty=format: --name-only \
    | grep -E '^(proxies/|arkiv\.db$|bench_(guard|qwen3|ingest)|test_(long|short)_.*\.wav$|\.claude/(handover|session-log))' \
    | head -5 || true)
if [[ -n "$LEAKED" ]]; then
    echo "error: history still contains:" >&2
    echo "$LEAKED" >&2
    exit 2
fi

echo "==> verify: no '<user>' text in history"
if git log --all -p | grep -m1 "<user>" >/dev/null; then
    echo "error: '<user>' still appears somewhere in rewritten history" >&2
    echo "run:  git log --all -p | grep -n <user> | head" >&2
    exit 3
fi

if [[ "${SKIP_PUSH:-0}" == "1" ]]; then
    echo "==> SKIP_PUSH=1 set — stopping before push"
    echo "    inspect $WORKDIR manually, then push when ready:"
    echo "      cd $WORKDIR"
    echo "      git remote add origin $REMOTE"
    echo "      git push --force --all && git push --force --tags"
    exit 0
fi

echo "==> force-push rewritten history to $REMOTE"
git remote add origin "$REMOTE"
git push --force --all
git push --force --tags

cat <<'POSTMSG'

==> rewrite complete. Manual follow-ups:

  1. Open a GitHub support ticket to purge reflog / cached commits:
       https://support.github.com/contact
     Request immediate garbage-collection of unreachable commits on
     vulture-s/arkiv so the old SHAs can no longer be fetched.

  2. Tell every collaborator to DELETE their local clone and re-clone
     from scratch. A plain `git pull` will merge the old history back.

  3. Rebase every open PR branch onto the new main, then force-push
     each PR branch.

  4. Fork owners cannot be forced to update. List forks at
       https://github.com/vulture-s/arkiv/network/members
     and contact them if the data must be purged from their copies.

  5. Keep the safety bundle (arkiv-pre-purge.bundle) until you are
     certain everything still works; then delete it.
POSTMSG
