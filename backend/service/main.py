"""
ACPS Leader Agent — FastAPI 服务入口

提供 SSE 流式聊天 API。
作为整个后端的入口，在此处配置根 logger，
所有日志统一输出到 stdout（由 start.sh 重定向至 service.log）。
"""
import sys
import logging
from pathlib import Path
from contextlib import asynccontextmanager

# ---------------------------------------------------------------------------
# 路径配置：将 backend/ 和 backend/agent/ 加入 sys.path
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_BACKEND_DIR / "agent"))

from config import SERVICE_HOST, SERVICE_PORT

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# 根 logger 配置 — 所有模块共享，统一输出到 stdout
# start.sh 通过 nohup 将 stdout 重定向到 backend/service.log
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  [%(name)s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger("service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("ACPS Leader Service starting up on %s:%s", SERVICE_HOST, SERVICE_PORT)
    yield
    logger.info("ACPS Leader Service shutting down...")
    try:
        from graph_builder_mcp import shutdown_mcp_client
        await shutdown_mcp_client()
    except Exception:
        pass


app = FastAPI(
    title="ACPS Leader Agent",
    description="ACPS Leader Agent API with SSE streaming",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from .routes import chat, chat_mcp

app.include_router(chat.router, prefix="/api/skill/chat", tags=["Chat (Skill)"])
app.include_router(chat_mcp.router, prefix="/api/mcp/chat", tags=["Chat (MCP)"])


@app.get("/api/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy", "service": "acps_leader_agent", "modes": ["skill", "mcp"]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "service.main:app",
        host=SERVICE_HOST,
        port=SERVICE_PORT,
        reload=True,
    )
