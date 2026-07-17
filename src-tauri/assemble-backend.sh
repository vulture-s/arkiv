#!/usr/bin/env bash
# Assemble the self-contained backend bundle for the arkiv desktop app (Option 1).
#
# Produces src-tauri/backend/{python,site-packages,src}, which tauri.conf.json
# bundles as an app resource. Run this ONCE before `cargo tauri build` (and again
# whenever the deps or the SPA change).
#
# The backend is python-build-standalone (a portable, self-contained CPython) +
# the project's already-built site-packages + the arkiv Python source + the built
# SPA. It is deliberately NOT PyInstaller: the spike (2026-07-17) showed the
# native-heavy tree (torch / mlx / chromadb / mlx-whisper / silero-vad) imports
# and boots cleanly under a stock portable interpreter, sidestepping the
# hidden-import/dylib whack-a-mole PyInstaller would demand for this dep set.
#
# The staging dir is git-ignored (it is ~1.3 GB); this script rebuilds it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"   # src-tauri/
REPO="$(cd "$HERE/.." && pwd)"          # repo root
BACKEND="$HERE/backend"
VENV_SP="$REPO/.venv/lib/python3.12/site-packages"

PY_VERSION="3.12.13"
PBS_TAG="20260623"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/cpython-${PY_VERSION}%2B${PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz"

if [ ! -d "$VENV_SP" ]; then
  echo "ERROR: $VENV_SP not found — run install.sh (or build the .venv) first." >&2
  exit 1
fi

echo "[assemble] clean staging: $BACKEND (preserving tracked .gitkeep)"
rm -rf "$BACKEND/python" "$BACKEND/site-packages" "$BACKEND/src"
mkdir -p "$BACKEND/src"

echo "[assemble] portable python $PY_VERSION (python-build-standalone $PBS_TAG)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$PBS_URL" -o "$TMP/pbs.tar.gz"
tar xzf "$TMP/pbs.tar.gz" -C "$TMP"     # extracts ./python/
mv "$TMP/python" "$BACKEND/python"

echo "[assemble] site-packages (trim caches + chromadb transitives arkiv never imports at runtime)"
mkdir -p "$BACKEND/site-packages"
# arkiv embeds via ollama, not chromadb's built-in onnx/k8s embedding paths, so
# kubernetes/ and onnxruntime/ (~155 MB) are dead weight in the bundle. Drop
# bytecode caches too. If a future feature needs them, remove the excludes.
rsync -a --delete \
  --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude 'kubernetes/' --exclude 'onnxruntime/' \
  "$VENV_SP/" "$BACKEND/site-packages/"

echo "[assemble] arkiv source (runtime .py + routers + built SPA; no tests/docs/venv)"
# The server imports many top-level modules + the routers package, and serves the
# SPA from ./frontend/dist relative to its cwd. Copy generously (repo minus the
# heavy/irrelevant dirs) so no runtime import is missed.
rsync -a \
  --exclude '.git/' --exclude '.venv/' --exclude 'node_modules/' \
  --exclude 'src-tauri/' --exclude 'tests/' --exclude 'docs/' \
  --exclude 'frontend/node_modules/' --exclude 'frontend/src/' \
  --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '.arkiv/' --exclude 'chroma_db/' \
  "$REPO/" "$BACKEND/src/"

if [ ! -f "$BACKEND/src/frontend/dist/index.html" ]; then
  echo "WARN: frontend/dist/index.html missing — run 'cd frontend && npm run build' first," >&2
  echo "      or the packaged app will serve the fallback page." >&2
fi

echo "[assemble] done:"
du -sh "$BACKEND" "$BACKEND"/python "$BACKEND"/site-packages "$BACKEND"/src 2>/dev/null || true
echo "[assemble] next:  CI=true cargo tauri build   (from src-tauri/)"
echo "[assemble]        ^^^^^^^ CI=true makes tauri pass --skip-jenkins to bundle_dmg.sh,"
echo "[assemble]        skipping the Finder-prettifying AppleScript. Without it the dmg step"
echo "[assemble]        fails with AppleEvent timeout -1712 in any non-GUI / background build"
echo "[assemble]        (the .app still builds; only the cosmetic dmg layout needs a live Finder)."
