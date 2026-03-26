#!/bin/bash
# ACPS Leader — Stop All Services

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=========================================="
echo "  Stopping ACPS Leader (all services)"
echo "=========================================="
echo ""

# 1. Frontend Dev Server
echo "[1/4] Frontend Dev Server..."
FRONTEND_PID_FILE="$ROOT_DIR/frontend/.dev.pid"
if [ -f "$FRONTEND_PID_FILE" ]; then
    PID=$(cat "$FRONTEND_PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "→ Stopping frontend dev server (pid $PID)..."
        kill "$PID" 2>/dev/null
        sleep 1
        kill -9 "$PID" 2>/dev/null
        echo "✓ Frontend stopped"
    else
        echo "  Frontend is not running (stale PID)"
    fi
    rm -f "$FRONTEND_PID_FILE"
else
    echo "  Frontend is not running"
fi
echo ""

# 2. Backend API
echo "[2/4] Backend API..."
bash "$ROOT_DIR/backend/stop.sh"
echo ""

# 3. ACPS MCP Server
echo "[3/4] ACPS MCP Server..."
bash "$ROOT_DIR/mcp_server/stop.sh"
echo ""

# 4. Mock Discovery Server
echo "[4/4] Mock Discovery Server..."
bash "$ROOT_DIR/mocked-discovery/stop.sh"
echo ""

echo "=========================================="
echo "  All services stopped."
echo "=========================================="
