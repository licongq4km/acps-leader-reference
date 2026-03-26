#!/bin/bash
# ACPS Leader Backend — Start Script
#
# 1. 检查并创建 .venv（如不存在则自动安装 requirements.txt）
# 2. 启动 FastAPI 服务（默认端口 7002，可在 .env 中配置 SERVICE_PORT）
# 3. 所有日志统一输出到 backend/service.log

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/service.log"
PID_FILE="$SCRIPT_DIR/.service.pid"
ENV_FILE="$SCRIPT_DIR/.env"

# ---------------------------------------------------------------
# 读取端口配置
# ---------------------------------------------------------------
SERVICE_PORT=7002
if [ -f "$ENV_FILE" ]; then
    val=$(grep -E "^SERVICE_PORT=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]')
    SERVICE_PORT="${val:-$SERVICE_PORT}"
fi

# ---------------------------------------------------------------
# 检查是否已在运行
# ---------------------------------------------------------------
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "✓ Service is already running (pid $OLD_PID, port $SERVICE_PORT)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# ---------------------------------------------------------------
# 检查 / 创建虚拟环境
# ---------------------------------------------------------------
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "→ Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/.venv"
    echo "→ Installing dependencies..."
    "$SCRIPT_DIR/.venv/bin/pip" install --upgrade pip -q
    "$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -q
    echo "✓ Dependencies installed"
fi

# 激活虚拟环境
source "$SCRIPT_DIR/.venv/bin/activate"

# ---------------------------------------------------------------
# 启动服务
# ---------------------------------------------------------------
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR:$SCRIPT_DIR/agent:$PYTHONPATH"

echo "→ Starting ACPS Leader Service on port $SERVICE_PORT..."

nohup python3 -m uvicorn service.main:app \
    --host 0.0.0.0 \
    --port "$SERVICE_PORT" \
    > "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
disown

# ---------------------------------------------------------------
# 等待启动完成（最多 30 秒）
# ---------------------------------------------------------------
for i in $(seq 1 30); do
    if curl -s "http://localhost:${SERVICE_PORT}/api/health" > /dev/null 2>&1; then
        echo "✓ ACPS Leader Service started (port $SERVICE_PORT, pid $(cat $PID_FILE))"
        echo "  Log file: $LOG_FILE"
        exit 0
    fi
    sleep 1
done

echo "✘ Service failed to start. Check $LOG_FILE"
tail -20 "$LOG_FILE" 2>/dev/null
rm -f "$PID_FILE"
exit 1
