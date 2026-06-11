#!/usr/bin/env bash
# arkiv — Uninstaller
# Usage: bash uninstall.sh
set -e

INSTALL_DIR="${ARKIV_DIR:-$HOME/.arkiv}"
RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; BOLD='\033[1m'; NC='\033[0m'

echo -e "${BOLD}═══ arkiv uninstaller ═══${NC}"
echo ""

# Refuse to rm -rf a directory that isn't actually an arkiv install. ARKIV_DIR
# comes from the environment, so a stray `ARKIV_DIR=$HOME` (or a footage volume)
# would otherwise wipe an unrelated tree (M5). Require the install markers.
if [ ! -f "$INSTALL_DIR/server.py" ] || [ ! -f "$INSTALL_DIR/config.py" ]; then
    echo -e "${RED}Refusing to uninstall:${NC} '$INSTALL_DIR' does not look like an"
    echo "arkiv install (server.py / config.py not found)."
    echo "Set ARKIV_DIR to the correct install directory and retry."
    exit 1
fi

echo "This will remove:"
echo "  $INSTALL_DIR (app + venv + DB)"
echo ""
echo "Your media files will NOT be deleted."
echo ""
read -p "Continue? [y/N] " confirm

if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Cancelled."
    exit 0
fi

# Stop server
pkill -f 'uvicorn server:app' 2>/dev/null && echo "  Stopped running server" || true

# Remove install directory
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo -e "  ${GREEN}✓${NC} Removed $INSTALL_DIR"
fi

# Remove desktop shortcut if exists
SHORTCUT="$HOME/Desktop/arkiv.command"
if [ -f "$SHORTCUT" ]; then
    rm "$SHORTCUT"
    echo -e "  ${GREEN}✓${NC} Removed desktop shortcut"
fi

echo ""
echo -e "${GREEN}arkiv uninstalled.${NC}"
echo "Ollama and FFmpeg were NOT removed (shared dependencies)."
