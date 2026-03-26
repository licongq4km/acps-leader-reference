#!/bin/bash
# ACPS Leader — Start All Services
#
# Starts all components in the correct order:
#   1. Mock Discovery Server (port 7001)
#   2. ACPS MCP Server      (port 7004)
#   3. Backend API           (port 7002)
#   4. Frontend Dev Server   (port 7003)

set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=========================================="
echo "  Starting ACPS Leader (all services)"
echo "=========================================="
echo ""

# 1. Mock Discovery Server
echo "[1/4] Mock Discovery Server..."
bash "$ROOT_DIR/mocked-discovery/start.sh"
echo ""

# 2. ACPS MCP Server
echo "[2/4] ACPS MCP Server..."
bash "$ROOT_DIR/mcp_server/start.sh"
echo ""

# 3. Backend API
echo "[3/4] Backend API..."
bash "$ROOT_DIR/backend/start.sh"
echo ""

# 4. Frontend Dev Server
echo "[4/4] Frontend Dev Server..."
FRONTEND_DIR="$ROOT_DIR/frontend"
PID_FILE="$FRONTEND_DIR/.dev.pid"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "✓ Frontend dev server is already running (pid $OLD_PID)"
    else
        rm -f "$PID_FILE"
    fi
fi

if [ ! -f "$PID_FILE" ]; then
    cd "$FRONTEND_DIR"
    if [ ! -d "node_modules" ]; then
        echo "→ Installing frontend dependencies..."
        npm install --silent
    fi
    nohup npx vite --host 0.0.0.0 --port 7003 > "$FRONTEND_DIR/dev.log" 2>&1 &
    echo $! > "$PID_FILE"
    disown
    echo "✓ Frontend dev server started (port 7003, pid $(cat $PID_FILE))"
    echo "  Log file: $FRONTEND_DIR/dev.log"
fi

echo ""
echo "=========================================="
echo "  All services started!"
echo ""
echo "  Frontend  : http://localhost:7003"
echo "  Backend   : http://localhost:7002"
echo "  MCP Server: http://localhost:7004"
echo "  Discovery : http://localhost:7001"
echo "=========================================="
