#!/bin/bash
# ACPS Leader Backend — Stop Script

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.service.pid"
ENV_FILE="$SCRIPT_DIR/.env"

# 读取端口配置
SERVICE_PORT=7002
if [ -f "$ENV_FILE" ]; then
    val=$(grep -E "^SERVICE_PORT=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]')
    SERVICE_PORT="${val:-$SERVICE_PORT}"
fi

# 优先使用 PID 文件
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "→ Stopping ACPS Leader Service (pid $PID)..."
        kill "$PID" 2>/dev/null
        sleep 2
        kill -9 "$PID" 2>/dev/null
        rm -f "$PID_FILE"
        echo "✓ Service stopped"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# 回退：按端口查找进程
PID=$(lsof -ti ":${SERVICE_PORT}" -sTCP:LISTEN 2>/dev/null)
if [ -n "$PID" ]; then
    echo "→ Stopping ACPS Leader Service (port $SERVICE_PORT, pid $PID)..."
    kill "$PID" 2>/dev/null
    sleep 2
    kill -9 "$PID" 2>/dev/null
    echo "✓ Service stopped"
else
    echo "  Service is not running"
fi
