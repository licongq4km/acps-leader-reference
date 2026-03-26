"""
AIP v2 群组模式数据模型定义

本模块定义了 AIP v2 协议中群组模式相关的数据对象，包括：
- 枚举类型：GroupMgmtCommandType
- 群组管理命令：GroupMgmtCommand (继承自 Message)
- 群组管理结果：GroupMgmtResult (继承自 Message)
- RabbitMQ 相关配置
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from enum import Enum
from typing import List, Optional, Dict, Any, Literal, Union

from .aip_rpc_model import JSONRPCRequest, JSONRPCResponse, JSONRPCError
from .aip_base_model import Message


# =============================================================================
# ACS 对象
# =============================================================================


class ACSObject(BaseModel):
    """智能体能力说明对象"""

    aic: str
    skills: Optional[List[str]] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# 群组信息
# =============================================================================


class GroupInfo(BaseModel):
    """群组信息"""

    groupId: str
    leader: ACSObject
    partners: List[ACSObject]


# =============================================================================
# RabbitMQ 配置
# =============================================================================


class RabbitMQServerConfig(BaseModel):
    """RabbitMQ 服务器配置"""

    host: str
    port: int
    vhost: str
    accessToken: str


class AMQPConfig(BaseModel):
    """AMQP 配置"""

    exchange: str
    exchangeType: str
    routingKey: str


class RabbitMQRequestParams(BaseModel):
    """RabbitMQ 请求参数"""

    protocol: str
    group: GroupInfo
    server: RabbitMQServerConfig
    amqp: AMQPConfig


class RabbitMQRequest(JSONRPCRequest):
    """RabbitMQ 群组加入请求"""

    method: Literal["group"] = "group"
    params: RabbitMQRequestParams


class RabbitMQResponseResult(BaseModel):
    """RabbitMQ 响应结果"""

    connectionName: str
    vhost: str
    nodeName: str
    queueName: str
    processId: Optional[str] = None


class RabbitMQResponseErrorData(BaseModel):
    """RabbitMQ 响应错误数据"""

    errorType: str
    details: Optional[Any] = None


class RabbitMQResponseError(JSONRPCError):
    """RabbitMQ 响应错误"""

    data: Optional[RabbitMQResponseErrorData] = None


class RabbitMQResponse(JSONRPCResponse):
    """RabbitMQ 群组加入响应"""

    result: Optional[RabbitMQResponseResult] = None
    error: Optional[RabbitMQResponseError] = None


# =============================================================================
# AIP v2 群组管理类型
# =============================================================================


class GroupMgmtCommandType(str, Enum):
    """
    AIP v2: 群组管理命令类型枚举

    注意：v1 中此枚举名为 GroupMgmtCommand，v2 重命名为 GroupMgmtCommandType，
    因为 GroupMgmtCommand 现在是一个继承自 Message 的类。
    """

    GET_STATUS = "get-status"
    LEAVE_GROUP = "leave-group"
    MUTE = "mute"
    UNMUTE = "unmute"


class GroupMemberStatus(BaseModel):
    """群组成员状态"""

    connected: bool
    muted: bool


class GroupMgmtCommand(Message):
    """
    AIP v2 群组管理命令

    继承自 Message，用于发送群组管理相关的命令。
    """

    type: Literal["group-mgmt-command"] = "group-mgmt-command"
    command: GroupMgmtCommandType  # 必填

    @property
    def is_status_query(self) -> bool:
        """是否是状态查询命令"""
        return self.command == GroupMgmtCommandType.GET_STATUS

    @property
    def is_leave_request(self) -> bool:
        """是否是退出请求"""
        return self.command == GroupMgmtCommandType.LEAVE_GROUP

    @property
    def is_mute_command(self) -> bool:
        """是否是静音命令"""
        return self.command == GroupMgmtCommandType.MUTE

    @property
    def is_unmute_command(self) -> bool:
        """是否是取消静音命令"""
        return self.command == GroupMgmtCommandType.UNMUTE

    def is_mentioned(self, aic: str) -> bool:
        """
        检查指定的AIC是否被提及

        Args:
            aic: 要检查的智能体AIC

        Returns:
            True表示被提及（需要响应），False表示未被提及
        """
        if self.mentions is None or (
            isinstance(self.mentions, list) and len(self.mentions) == 0
        ):
            return False
        if self.mentions == "all":
            return True
        if isinstance(self.mentions, list) and aic in self.mentions:
            return True
        return False

    def must_respond(self, aic: str) -> bool:
        """
        检查指定的AIC是否必须响应

        Args:
            aic: 要检查的智能体AIC

        Returns:
            True表示必须响应，False表示可选
        """
        if self.mentions == "all":
            return True
        if isinstance(self.mentions, list) and aic in self.mentions:
            return True
        return False


class GroupMgmtResult(Message):
    """
    AIP v2 群组管理结果

    继承自 Message，用于返回群组成员的状态信息。
    """

    type: Literal["group-mgmt-result"] = "group-mgmt-result"
    status: GroupMemberStatus  # 必填
