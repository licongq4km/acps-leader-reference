"""
API 请求/响应模型
"""
from typing import Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., description="用户消息")
    thread_id: Optional[str] = Field(None, description="会话线程ID，为空则自动创建")
