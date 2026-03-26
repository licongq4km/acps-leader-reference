"""
AIP v2 SDK 公共导出

本模块导出 AIP v2 协议的所有公共类型和客户端。
"""

import logging

# Set up NullHandler to avoid "No handler found" warnings
logging.getLogger(__name__).addHandler(logging.NullHandler())

# AIP v2 基础类型
from .aip_base_model import (  # noqa: F401
    # 枚举
    TaskState,
    TaskCommandType,
    # 数据项
    DataItem,
    TextDataItem,
    FileDataItem,
    StructuredDataItem,
    # 消息基类
    Message,
    # 任务命令和结果
    TaskCommand,
    TaskResult,
    # 任务状态和产出物
    TaskStatus,
    Product,
    # 命令参数
    GetCommandParams,
    StartCommandParams,
)

# RPC 客户端
from .aip_rpc_client import AipRpcClient  # noqa: F401

# 群组模式类型
from .aip_group_model import (  # noqa: F401
    # 基础信息
    ACSObject,
    GroupInfo,
    # 枚举
    GroupMgmtCommandType,
    # 群组管理命令和结果
    GroupMgmtCommand,
    GroupMgmtResult,
    GroupMemberStatus,
    # RabbitMQ 配置
    RabbitMQRequest,
    RabbitMQResponse,
    RabbitMQRequestParams,
    RabbitMQServerConfig,
    AMQPConfig,
)

# 群组模式客户端
try:
    from .aip_group_leader import (  # noqa: F401
        GroupLeaderMqClient,
        GroupLeaderSession,
        GroupLeader,
    )
    from .aip_group_partner import (  # noqa: F401
        GroupPartnerMqClient,
        PartnerGroupSession,
        PartnerGroupState,
    )
except ImportError:
    pass
