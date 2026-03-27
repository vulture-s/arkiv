#!/usr/bin/env bash
# arkiv — macOS One-Click Installer
# Usage: curl -fsSL <url> | bash
#    or: bash install.sh
set -e

INSTALL_DIR="${ARKIV_DIR:-$HOME/.arkiv}"
VENV_DIR="$INSTALL_DIR/.venv"
PORT=${ARKIV_PORT:-8501}

RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; BOLD='\033[1m'; NC='\033[0m'

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
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo -e "${YELLOW}Installing Python 3.12...${NC}"
    brew install python@3.12
fi
echo -e "  ${GREEN}✓${NC} Python $PY_VERSION"

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

# ── 2. Create install directory ──
echo "Setting up arkiv at $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# Copy files (if running from source)
if [ -f "$(dirname "$0")/server.py" ]; then
    SRC="$(cd "$(dirname "$0")" && pwd)"
    echo "  Copying from $SRC..."
    for f in server.py db.py config.py health.py index.html ingest.py transcribe.py embed.py frames.py vision.py vectordb.py requirements.txt .env.example smoke-test.sh LICENSE README.md; do
        [ -f "$SRC/$f" ] && cp "$SRC/$f" "$INSTALL_DIR/"
    done
    [ -d "$SRC/pages" ] && cp -r "$SRC/pages" "$INSTALL_DIR/"
fi

# ── 3. Virtual environment ──
echo "  Creating virtual environment..."
python3 -m venv "$VENV_DIR"
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
echo "Pulling Ollama models (this may take a while)..."
ollama serve &>/dev/null &
sleep 2
ollama pull nomic-embed-text 2>/dev/null && echo -e "  ${GREEN}✓${NC} nomic-embed-text" || echo -e "  ${YELLOW}⏭${NC} nomic-embed-text (pull later)"
ollama pull llava:7b 2>/dev/null && echo -e "  ${GREEN}✓${NC} llava:7b" || echo -e "  ${YELLOW}⏭${NC} llava:7b (pull later)"

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

echo ""
echo -e "${GREEN}${BOLD}═══ arkiv installed successfully ═══${NC}"
echo ""
echo "  Location: $INSTALL_DIR"
echo "  Start:    cd $INSTALL_DIR && source .venv/bin/activate && uvicorn server:app --port $PORT"
echo "  Ingest:   python ingest.py /path/to/your/footage"
echo "  Health:   python health.py"
echo ""
echo "  Or use the desktop shortcut: double-click arkiv.command"
