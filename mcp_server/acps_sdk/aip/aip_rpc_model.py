"""
AIP v2 RPC 数据模型定义

本模块定义了 AIP v2 协议中 RPC 方式的数据对象，包括：
- JSON-RPC 基础类型：JSONRPCRequest, JSONRPCResponse, JSONRPCError
- RPC 请求/响应：RpcRequest, RpcResponse
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union, Literal
from .aip_base_model import TaskResult, TaskCommand


class JSONRPCRequest(BaseModel):
    """JSON-RPC 2.0 请求"""

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    id: Optional[Union[str, int]] = None
    params: Optional[Union[List[Any], Dict[str, Any]]] = None


class JSONRPCError(BaseModel):
    """JSON-RPC 2.0 错误"""

    code: int
    message: str
    data: Optional[Any] = None


class JSONRPCResponse(BaseModel):
    """JSON-RPC 2.0 响应"""

    jsonrpc: Literal["2.0"] = "2.0"
    id: Optional[Union[str, int]] = None
    result: Optional[Any] = None
    error: Optional[JSONRPCError] = None


class RpcRequestParams(BaseModel):
    """RPC 请求参数"""

    command: TaskCommand  # AIP v2: 使用 TaskCommand 类型


class RpcRequest(JSONRPCRequest):
    """AIP RPC 请求"""

    method: Literal["rpc"] = "rpc"
    params: RpcRequestParams


class RpcResponse(JSONRPCResponse):
    """AIP RPC 响应"""

    result: Optional[TaskResult] = None  # AIP v2: 使用 TaskResult 类型
