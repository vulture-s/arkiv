#!/usr/bin/env bash
# arkiv — Desktop Launch Shortcut
# Double-click this file to start arkiv
INSTALL_DIR="${ARKIV_DIR:-$HOME/.arkiv}"
PORT=${ARKIV_PORT:-8501}

# Use installed location, fallback to script location
if [ -f "$INSTALL_DIR/server.py" ]; then
    cd "$INSTALL_DIR"
elif [ -f "$(dirname "$0")/server.py" ]; then
    cd "$(dirname "$0")"
else
    echo "arkiv not found. Run install.sh first."
    read -p "Press Enter to close..."
    exit 1
fi

# Activate venv
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "$HOME/omlx/.venv/bin/activate" ]; then
    source "$HOME/omlx/.venv/bin/activate"
fi

# Start Ollama if not running
if ! pgrep -x ollama &>/dev/null; then
    ollama serve &>/dev/null &
    sleep 2
fi

echo "Starting arkiv on http://localhost:$PORT"
echo "Press Ctrl+C to stop"
echo ""

# Open browser
open "http://localhost:$PORT" 2>/dev/null &

# Run server (foreground so terminal stays open)
uvicorn server:app --host 0.0.0.0 --port "$PORT"
