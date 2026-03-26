"""
聊天路由

POST /api/chat/stream — SSE 流式聊天（自动检测 interrupt 并 resume）
"""
import uuid
import logging
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from ..schemas import ChatRequest
from ..stream_handler import stream_agent_events

logger = logging.getLogger(__name__)

router = APIRouter()

_graph = None


def _get_graph():
    """延迟初始化 agent graph（单例）"""
    global _graph
    if _graph is None:
        from graph_builder import build_agent
        _graph = build_agent()
        logger.info("Agent graph initialized")
    return _graph


async def _has_pending_interrupt(graph, config: dict) -> bool:
    """检查当前 thread 是否有未处理的 interrupt。"""
    try:
        state = await graph.aget_state(config)
        if state and hasattr(state, "tasks") and state.tasks:
            for task in state.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    return True
    except Exception as e:
        logger.warning("Failed to check interrupt state: %s", e)
    return False


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """SSE 流式聊天：自动判断是 resume（从 interrupt 恢复）还是新消息。"""
    thread_id = request.thread_id or str(uuid.uuid4())
    logger.info("Chat stream request: thread_id=%s, message=%s",
                thread_id, request.message[:100])

    graph = _get_graph()

    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    has_interrupt = await _has_pending_interrupt(graph, config)

    if has_interrupt:
        logger.info("Resuming from interrupt: thread_id=%s", thread_id)
        input_cmd = Command(resume=request.message)
    else:
        input_cmd = {"messages": [HumanMessage(content=request.message)]}

    async def event_generator() -> AsyncIterator[str]:
        async for event in stream_agent_events(graph, input_cmd, config, is_resume=has_interrupt):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
