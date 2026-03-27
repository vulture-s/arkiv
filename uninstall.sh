#!/usr/bin/env bash
# arkiv — Uninstaller
# Usage: bash uninstall.sh
set -e

INSTALL_DIR="${ARKIV_DIR:-$HOME/.arkiv}"
RED='\033[31m'; GREEN='\033[32m'; BOLD='\033[1m'; NC='\033[0m'

echo -e "${BOLD}═══ arkiv uninstaller ═══${NC}"
echo ""
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
