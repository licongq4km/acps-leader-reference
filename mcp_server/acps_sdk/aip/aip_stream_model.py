"""
AIP v2 流式传输数据模型定义

本模块定义了 AIP v2 协议中流式传输方式的数据对象，包括：
- SSE 事件：TaskStatusUpdateEvent, ProductChunkEvent (均继承自 Message)
- 流式请求/响应：StreamRequest, StreamResponse
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union, Literal
from .aip_base_model import TaskResult, TaskCommand, Message, TaskStatus, Product
from .aip_rpc_model import JSONRPCRequest, JSONRPCResponse


class StreamRequestParams(BaseModel):
    """流式请求参数"""

    message: TaskCommand  # AIP v2: 使用 TaskCommand 类型


class StreamRequest(JSONRPCRequest):
    """AIP 流式请求"""

    method: Literal["stream"] = "stream"
    params: StreamRequestParams


class TaskStatusUpdateEvent(Message):
    """
    AIP v2 任务状态更新事件

    继承自 Message，用于 SSE 流式传输中的任务状态更新通知。
    """

    type: Literal["task-status-update"] = "task-status-update"
    taskId: str
    status: TaskStatus


class ProductChunkEvent(Message):
    """
    AIP v2 产出物分块事件

    继承自 Message，用于 SSE 流式传输中的产出物分块传输。
    """

    type: Literal["product-chunk"] = "product-chunk"
    taskId: str
    product: Product
    append: bool
    lastChunk: bool


class StreamEventData(BaseModel):
    """流式事件数据"""

    eventSeq: int
    eventData: Union[TaskResult, TaskCommand, TaskStatusUpdateEvent, ProductChunkEvent]


class StreamResponse(JSONRPCResponse):
    """AIP 流式响应"""

    result: StreamEventData


class ReStreamCommandParams(BaseModel):
    """重连流式传输命令参数"""

    lastEventSeq: Optional[int] = None
