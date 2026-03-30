#!/bin/bash
# ACPS MCP Server — Start Script
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/server.log"
PID_FILE="$SCRIPT_DIR/.server.pid"
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a; . "$SCRIPT_DIR/.env"; set +a
fi
PORT="${MCP_SERVER_PORT:-7004}"

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "✓ MCP Server is already running (pid $OLD_PID, port $PORT)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# Create / activate venv
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "→ Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/.venv"
    echo "→ Installing dependencies..."
    "$SCRIPT_DIR/.venv/bin/pip" install --upgrade pip -q
    "$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -q
    echo "✓ Dependencies installed"
fi

source "$SCRIPT_DIR/.venv/bin/activate"

cd "$SCRIPT_DIR"
echo "→ Starting ACPS MCP Server on port $PORT..."

nohup python3 server.py > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
disown

sleep 2
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "✓ ACPS MCP Server started (port $PORT, pid $(cat $PID_FILE))"
    echo "  Log file: $LOG_FILE"
else
    echo "✘ MCP Server failed to start. Check $LOG_FILE"
    tail -20 "$LOG_FILE" 2>/dev/null
    rm -f "$PID_FILE"
    exit 1
fi
