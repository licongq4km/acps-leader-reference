#!/bin/bash
# ACPS MCP Server — Stop Script

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.server.pid"
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a; . "$SCRIPT_DIR/.env"; set +a
fi
PORT="${MCP_SERVER_PORT:-7004}"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "→ Stopping ACPS MCP Server (pid $PID)..."
        kill "$PID" 2>/dev/null
        sleep 2
        kill -9 "$PID" 2>/dev/null
        rm -f "$PID_FILE"
        echo "✓ MCP Server stopped"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

PID=$(lsof -ti ":${PORT}" -sTCP:LISTEN 2>/dev/null)
if [ -n "$PID" ]; then
    echo "→ Stopping MCP Server (port $PORT, pid $PID)..."
    kill "$PID" 2>/dev/null
    sleep 2
    kill -9 "$PID" 2>/dev/null
    echo "✓ MCP Server stopped"
else
    echo "  MCP Server is not running"
fi
