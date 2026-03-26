"""
AIP v2 基础数据模型定义

本模块定义了 AIP（Agent Interaction Protocol）v2 协议的基础数据对象，包括：
- 枚举类型：TaskState, TaskCommandType
- 数据项：DataItem (TextDataItem, FileDataItem, StructuredDataItem)
- 消息基类：Message
- 任务命令：TaskCommand (继承自 Message)
- 任务结果：TaskResult (继承自 Message)
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from enum import Enum
from typing import List, Optional, Dict, Any, Union, Literal


# =============================================================================
# 枚举类型
# =============================================================================


class TaskState(str, Enum):
    """任务状态枚举"""

    Accepted = "accepted"
    Working = "working"
    AwaitingInput = "awaiting-input"
    AwaitingCompletion = "awaiting-completion"
    Completed = "completed"
    Canceled = "canceled"
    Failed = "failed"
    Rejected = "rejected"


class TaskCommandType(str, Enum):
    """
    AIP v2: 任务命令类型枚举

    注意：v1 中此枚举名为 TaskCommand，v2 重命名为 TaskCommandType，
    因为 TaskCommand 现在是一个继承自 Message 的类。
    """

    Get = "get"
    Start = "start"
    Continue = "continue"
    Cancel = "cancel"
    Complete = "complete"
    ReStream = "re-stream"


# =============================================================================
# 数据项类型
# =============================================================================


class DataItemBase(BaseModel):
    """数据项基类"""

    metadata: Optional[Dict[str, Any]] = None


class TextDataItem(DataItemBase):
    """文本数据项"""

    type: Literal["text"] = "text"
    text: str


class FileDataItem(DataItemBase):
    """文件数据项"""

    type: Literal["file"] = "file"
    name: Optional[str] = None
    mimeType: Optional[str] = None
    uri: Optional[str] = None
    bytes: Optional[str] = None


class StructuredDataItem(DataItemBase):
    """结构化数据项"""

    type: Literal["data"] = "data"
    data: Dict[str, Any]


DataItem = Union[TextDataItem, FileDataItem, StructuredDataItem]


# =============================================================================
# 任务状态和产出物
# =============================================================================


class TaskStatus(BaseModel):
    """任务状态"""

    state: TaskState
    stateChangedAt: str
    dataItems: Optional[List[DataItem]] = None


class Product(BaseModel):
    """产出物"""

    id: str
    name: Optional[str] = None
    description: Optional[str] = None
    dataItems: List[DataItem]


# =============================================================================
# 命令参数
# =============================================================================


class GetCommandParams(BaseModel):
    """Get 命令参数"""

    lastCommandSentAt: Optional[str] = None  # AIP v2: 从 lastMessageSentAt 重命名
    lastStateChangedAt: Optional[str] = None


class StartCommandParams(BaseModel):
    """Start 命令参数"""

    timeout: Optional[int] = None
    maxProductsBytes: Optional[int] = None


# =============================================================================
# AIP v2 消息类型
# =============================================================================


class Message(BaseModel):
    """
    AIP v2 消息基类

    所有交互数据对象都继承自此类。Message 是中立的、双向的、无状态的。
    """

    type: str = "message"
    id: str
    sentAt: str
    senderRole: Literal["leader", "partner"]
    senderId: str
    mentions: Optional[Union[Literal["all"], List[str]]] = None
    dataItems: Optional[List[DataItem]] = None
    groupId: Optional[str] = None
    sessionId: Optional[str] = None


class TaskCommand(Message):
    """
    AIP v2 任务命令

    继承自 Message，用于 Leader 向 Partner 发送任务相关的命令。
    """

    type: Literal["task-command"] = "task-command"
    command: TaskCommandType  # 必填
    commandParams: Optional[Dict[str, Any]] = None
    taskId: Optional[str] = None  # 新建任务时可不填


class TaskResult(Message):
    """
    AIP v2 任务结果

    继承自 Message，用于 Partner 向 Leader 返回任务的状态和结果。
    """

    type: Literal["task-result"] = "task-result"
    taskId: str  # 必填
    status: TaskStatus  # 必填
    products: Optional[List[Product]] = None
    commandHistory: Optional[List[TaskCommand]] = (
        None  # AIP v2: 从 messageHistory 重命名
    )
    statusHistory: Optional[List[TaskStatus]] = None
