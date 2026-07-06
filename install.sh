#!/usr/bin/env bash
# arkiv — macOS One-Click Installer
# Usage: curl -fsSL <url> | bash
#    or: bash install.sh
set -e

INSTALL_DIR="${ARKIV_DIR:-$HOME/.arkiv}"
VENV_DIR="$INSTALL_DIR/.venv"
PORT=${ARKIV_PORT:-8501}

RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; BOLD='\033[1m'; NC='\033[0m'

# Build the Svelte SPA (frontend/dist) that server.py serves at /. npm missing is
# a loud warning, NOT fatal: the server still starts and the API works — but the
# web UI is the built SPA now (the legacy Tailwind page was retired in the Svelte
# cutover), so without a build "/" only shows a "run npm run build" hint until you
# build it. Never aborts the installer (guarded so `set -e` can't trip on a build
# failure).
build_frontend() {
    local fe="$1"
    if ! command -v npm &>/dev/null; then
        echo -e "  ${YELLOW}⚠${NC}  npm not found — skipping UI build (the web UI won't load until you build it)."
        echo -e "      Install Node (brew install node), then: cd $fe && npm ci && npm run build"
        return 0
    fi
    echo "  Building Svelte UI..."
    if ( cd "$fe" && npm ci --no-audit --no-fund && npm run build ) >/dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} UI built ($fe/dist)"
    else
        echo -e "  ${YELLOW}⚠${NC}  UI build failed — the web UI won't load until this succeeds. Re-run: cd $fe && npm ci && npm run build"
    fi
    return 0
}

echo -e "${BOLD}═══ arkiv installer ═══${NC}"
echo ""

# ── 1. Check prerequisites ──
echo "Checking prerequisites..."

# Homebrew
if ! command -v brew &>/dev/null; then
    echo -e "${YELLOW}Installing Homebrew...${NC}"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
echo -e "  ${GREEN}✓${NC} Homebrew"

# Python 3.10+
PY_BIN="python3"
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo -e "${YELLOW}Installing Python 3.12...${NC}"
    brew install python@3.12
    # python@3.12 is keg-only — `python3` still resolves to the old interpreter,
    # so the venv must be built from the freshly-installed binary explicitly,
    # otherwise it lands on the very version we just rejected (M2).
    PY_BIN="$(brew --prefix python@3.12)/bin/python3.12"
    PY_VERSION="3.12"
fi
echo -e "  ${GREEN}✓${NC} Python $PY_VERSION ($PY_BIN)"

# FFmpeg
if ! command -v ffmpeg &>/dev/null; then
    echo -e "${YELLOW}Installing FFmpeg...${NC}"
    brew install ffmpeg
fi
echo -e "  ${GREEN}✓${NC} FFmpeg $(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')"

# Ollama
if ! command -v ollama &>/dev/null; then
    echo -e "${YELLOW}Installing Ollama...${NC}"
    brew install ollama
fi
echo -e "  ${GREEN}✓${NC} Ollama"

echo ""

# ── 2. Get arkiv source ──
REPO="https://github.com/vulture-s/arkiv.git"

SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "$SRC" = "$INSTALL_DIR" ]; then
    # Already running from install dir (git clone directly to ~/.arkiv)
    echo "Setting up arkiv at $INSTALL_DIR..."
elif [ -f "$SRC/server.py" ]; then
    # Running from a different local directory — copy files.
    # Copy ALL top-level Python modules with a glob, not a hand-maintained list:
    # the old explicit list went stale (it predated auth.py / chat.py / admin.py /
    # federation.py / projects.py / smart_collections.py / tag_quality.py / …,
    # all imported by server.py) so this path produced an install that crashed on
    # first run with ImportError. A glob can't drift.
    echo "Setting up arkiv at $INSTALL_DIR (from $SRC)..."
    mkdir -p "$INSTALL_DIR"
    cp "$SRC"/*.py "$INSTALL_DIR"/
    # First-party package dirs (those with __init__.py, e.g. whisper_guard/) —
    # same glob principle as *.py so a new package can't drift behind a stale
    # list. Skip tests/ and the venv.
    for pkg in "$SRC"/*/__init__.py; do
        [ -f "$pkg" ] || continue
        d="$(dirname "$pkg")"
        case "$(basename "$d")" in tests|.venv) continue ;; esac
        cp -R "$d" "$INSTALL_DIR"/
    done
    for f in requirements.txt .env.example smoke-test.sh install.sh uninstall.sh arkiv.command LICENSE README.md; do
        [ -f "$SRC/$f" ] && cp "$SRC/$f" "$INSTALL_DIR/"
    done
else
    # Running via curl — clone repo
    echo "Cloning arkiv from GitHub..."
    if [ -d "$INSTALL_DIR/.git" ]; then
        git -C "$INSTALL_DIR" pull --quiet
    else
        git clone --quiet "$REPO" "$INSTALL_DIR"
    fi
fi

# ── 2b. Build Svelte UI (server.py serves frontend/dist at /) ──
# The copy path (local dir) only copies *.py + packages + a file list, NOT the
# frontend/ tree — so build at the source and copy just the built dist over.
# The clone / in-place paths have frontend/ at $INSTALL_DIR, so build there.
if [ -d "$INSTALL_DIR/frontend" ]; then
    build_frontend "$INSTALL_DIR/frontend"
elif [ -d "$SRC/frontend" ]; then
    build_frontend "$SRC/frontend"
    if [ -d "$SRC/frontend/dist" ]; then
        mkdir -p "$INSTALL_DIR/frontend"
        rm -rf "$INSTALL_DIR/frontend/dist"
        cp -R "$SRC/frontend/dist" "$INSTALL_DIR/frontend/dist"
    fi
fi

# ── 3. Virtual environment ──
echo "  Creating virtual environment..."
"$PY_BIN" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

echo "  Installing dependencies..."
pip install -q -r "$INSTALL_DIR/requirements.txt"

# macOS Apple Silicon: install mlx-whisper
if [ "$(uname -m)" = "arm64" ] && [ "$(uname -s)" = "Darwin" ]; then
    pip install -q mlx-whisper
    echo -e "  ${GREEN}✓${NC} mlx-whisper (Apple Silicon)"
fi

echo ""

# ── 4. Pull Ollama models ──
# Must match config.py defaults — pulling the wrong embed model (the old
# nomic-embed-text instead of bge-m3) left a fresh install's semantic search
# silently degraded to SQL LIKE on first run (M3).
echo "Pulling Ollama models (this may take a while)..."
ollama serve &>/dev/null &
sleep 2
ollama pull bge-m3 2>/dev/null && echo -e "  ${GREEN}✓${NC} bge-m3 (embeddings)" || echo -e "  ${YELLOW}⏭${NC} bge-m3 (pull later)"
# Vision = qwen2.5vl:7b (config.py default). NOT qwen3-vl:8b — it's ~10x slower
# under Ollama (vision-path regression, ~60s vs ~8s/frame on M2 Max).
ollama pull qwen2.5vl:7b 2>/dev/null && echo -e "  ${GREEN}✓${NC} qwen2.5vl:7b (vision)" || echo -e "  ${YELLOW}⏭${NC} qwen2.5vl:7b (pull later)"
# Vision fallback (config.VISION_FALLBACK_MODEL) — retried on frames the primary
# leaves empty. Without it, that resilience path 404s.
ollama pull minicpm-v 2>/dev/null && echo -e "  ${GREEN}✓${NC} minicpm-v (vision fallback)" || echo -e "  ${YELLOW}⏭${NC} minicpm-v (pull later)"
ollama pull qwen2.5:14b 2>/dev/null && echo -e "  ${GREEN}✓${NC} qwen2.5:14b (chat)" || echo -e "  ${YELLOW}⏭${NC} qwen2.5:14b (pull later — needed for chat)"

echo ""

# ── 5. Create directories ──
mkdir -p "$INSTALL_DIR/thumbnails"
mkdir -p "$INSTALL_DIR/media"

# ── 6. Create .env if not exists ──
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env" 2>/dev/null || true
fi

# ── 7. Init DB ──
cd "$INSTALL_DIR"
python3 -c "import db; db.init_db(); print('  DB initialized')"

# ── 8. Create desktop shortcut (interactive installs only) ──
# A headless / piped (`curl | bash`) / nohup install has no GUI to answer macOS's
# Desktop (TCC) access prompt, so `cp` into ~/Desktop can hang indefinitely. Guard
# on STDIN, not stdout: for `curl | bash` fd 0 is the pipe (not a tty) while fd 1
# is still the terminal, so `-t 1` would wrongly run and hang (Codex P1). `-t 0`
# is false for piped/headless installs and true only for a real interactive shell.
SHORTCUT="$HOME/Desktop/arkiv.command"
SHORTCUT_CREATED=""
if [ -t 0 ] && [ -f "$INSTALL_DIR/arkiv.command" ]; then
    cp "$INSTALL_DIR/arkiv.command" "$SHORTCUT"
    chmod +x "$SHORTCUT"
    SHORTCUT_CREATED=1
    echo -e "  ${GREEN}✓${NC} Desktop shortcut created"
fi

echo ""
echo -e "${GREEN}${BOLD}═══ arkiv installed successfully ═══${NC}"
echo ""
echo "  Location:  $INSTALL_DIR"
if [ -n "$SHORTCUT_CREATED" ]; then
    echo "  Launch:    double-click ~/Desktop/arkiv.command"
    echo "  Or:        cd $INSTALL_DIR && source .venv/bin/activate && uvicorn server:app --port $PORT"
else
    # No desktop shortcut on a piped/headless install — don't advertise it (Codex P3)
    echo "  Launch:    cd $INSTALL_DIR && source .venv/bin/activate && uvicorn server:app --port $PORT"
fi
echo "  Ingest:    python ingest.py --dir /path/to/your/footage"
echo "  Health:    python health.py"
echo "  Uninstall: bash $INSTALL_DIR/uninstall.sh"
