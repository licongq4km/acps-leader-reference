"""
群组模式客户端模块 - Leader角色的消息队列通信实现

本模块实现了AIP协议中群组模式(Group Mode)下Leader角色的核心功能：
1. 连接RabbitMQ消息队列服务器
2. 创建群组Exchange和队列
3. 发送群组消息（任务消息、管理消息）
4. 接收和处理群组内的消息
5. 管理群组成员（邀请加入、踢出、静音等）
"""

from __future__ import annotations
import asyncio
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Callable, Awaitable, Union
from enum import Enum

import httpx
import aio_pika
from aio_pika import Message as AMQPMessage, ExchangeType
from aio_pika.abc import (
    AbstractChannel,
    AbstractConnection,
    AbstractQueue,
    AbstractExchange,
)

from .aip_base_model import (
    Message,
    TaskCommand,
    TaskResult,
    TaskCommandType,
    TaskState,
    TaskStatus,
    TextDataItem,
    DataItem,
)
from .aip_group_model import (
    ACSObject,
    GroupInfo,
    RabbitMQServerConfig,
    AMQPConfig,
    RabbitMQRequest,
    RabbitMQRequestParams,
    RabbitMQResponse,
    RabbitMQResponseResult,
    GroupMgmtCommand,
    GroupMgmtCommandType,
    GroupMemberStatus,
)
from .aip_rpc_client import AipRpcClient


logger = logging.getLogger(__name__)


class GroupLeaderState(str, Enum):
    """群组Leader状态枚举"""

    DISCONNECTED = "DISCONNECTED"
    READY = "READY"
    DISSOLVING = "DISSOLVING"


class PartnerInviteError(Exception):
    """Partner邀请失败基类"""

    pass


class PartnerNetworkError(PartnerInviteError):
    """Partner邀请网络错误"""

    pass


class PartnerResponseError(PartnerInviteError):
    """Partner返回错误响应"""

    pass


class PartnerConnectionInfo:
    """Partner连接信息"""

    def __init__(
        self,
        aic: str,
        connection_name: str,
        vhost: str,
        node_name: str,
        queue_name: str,
        process_id: Optional[str] = None,
    ):
        self.aic = aic
        self.connection_name = connection_name
        self.vhost = vhost
        self.node_name = node_name
        self.queue_name = queue_name
        self.process_id = process_id
        self.connected = True
        self.muted = False
        self.joined_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aic": self.aic,
            "connection_name": self.connection_name,
            "vhost": self.vhost,
            "node_name": self.node_name,
            "queue_name": self.queue_name,
            "process_id": self.process_id,
            "connected": self.connected,
            "muted": self.muted,
            "joined_at": self.joined_at,
        }


# 消息处理回调类型
MessageHandler = Callable[
    [Union[TaskCommand, TaskResult, GroupMgmtCommand]], Awaitable[None]
]


class GroupLeaderMqClient:
    """
    群组模式Leader客户端

    负责：
    - 连接到RabbitMQ服务器
    - 创建和管理群组Exchange
    - 发送任务消息和管理消息到群组
    - 接收群组内的消息
    - 管理Partner成员
    """

    def __init__(
        self,
        leader_aic: str,
        rabbitmq_host: str = "localhost",
        rabbitmq_port: int = 5672,
        rabbitmq_vhost: str = "/",
        rabbitmq_user: str = "guest",
        rabbitmq_password: str = "guest",
        ssl_context=None,
        connection: Optional[AbstractConnection] = None,
        connection_owner: bool = True,
    ):
        """
        初始化Leader客户端

        Args:
            leader_aic: Leader的AIC标识
            rabbitmq_host: RabbitMQ服务器地址
            rabbitmq_port: RabbitMQ服务器端口
            rabbitmq_vhost: 虚拟主机
            rabbitmq_user: 用户名
            rabbitmq_password: 密码
            ssl_context: SSL上下文（可选）
        """
        self.leader_aic = leader_aic
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.rabbitmq_vhost = rabbitmq_vhost
        self.rabbitmq_user = rabbitmq_user
        self.rabbitmq_password = rabbitmq_password
        self.ssl_context = ssl_context
        self._connection_owner = connection_owner

        # 连接和通道
        self._connection: Optional[AbstractConnection] = connection
        self._channel: Optional[AbstractChannel] = None

        # 群组信息
        self._group_id: Optional[str] = None
        self._exchange_name: Optional[str] = None
        self._exchange: Optional[AbstractExchange] = None
        self._leader_queue: Optional[AbstractQueue] = None
        self._state: GroupLeaderState = GroupLeaderState.DISCONNECTED

        # Partner管理
        self._partners: Dict[str, PartnerConnectionInfo] = (
            {}
        )  # aic -> PartnerConnectionInfo
        self._partner_acs: List[ACSObject] = []  # Partner的ACS信息列表

        # 消息处理器
        self._message_handler: Optional[MessageHandler] = None
        self._consume_task: Optional[asyncio.Task] = None

        # 任务管理
        self._active_tasks: Dict[str, Task] = {}  # task_id -> Task

    @property
    def group_id(self) -> Optional[str]:
        return self._group_id

    @property
    def state(self) -> GroupLeaderState:
        return self._state

    @property
    def partners(self) -> Dict[str, PartnerConnectionInfo]:
        return self._partners.copy()

    @property
    def is_connected(self) -> bool:
        return self._connection is not None and not self._connection.is_closed

    async def connect(self) -> None:
        """建立到RabbitMQ的连接"""
        if self._connection and not self._connection.is_closed:
            if self._channel is None or getattr(self._channel, "is_closed", False):
                self._channel = await self._connection.channel()
            return

        if self._connection and getattr(self._connection, "is_closed", False):
            if not self._connection_owner:
                raise RuntimeError(
                    "Shared connection is closed; owner must recreate it"
                )

        connection_url = (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}{self.rabbitmq_vhost}"
        )

        try:
            self._connection = await aio_pika.connect_robust(
                connection_url,
                client_properties={"connection_name": f"leader-{self.leader_aic}"},
            )
            self._channel = await self._connection.channel()
            logger.info(
                "event=rabbitmq_connected leader_aic=%s host=%s vhost=%s",
                self.leader_aic,
                self.rabbitmq_host,
                self.rabbitmq_vhost,
            )
        except Exception as e:
            logger.error(
                "event=rabbitmq_connection_failed leader_aic=%s error=%s",
                self.leader_aic,
                str(e),
            )
            raise

    async def create_group(
        self,
        group_id: Optional[str] = None,
        partner_acs_list: Optional[List[ACSObject]] = None,
    ) -> str:
        """
        创建群组

        Args:
            group_id: 群组ID，如果不提供则自动生成
            partner_acs_list: Partner的ACS信息列表

        Returns:
            群组ID
        """
        if not self.is_connected:
            await self.connect()

        self._group_id = group_id or f"group-{uuid.uuid4()}"
        self._exchange_name = f"group-broadcast-{self._group_id}"
        self._partner_acs = partner_acs_list or []

        # 创建fanout类型的Exchange用于群组广播
        self._exchange = await self._channel.declare_exchange(
            name=self._exchange_name,
            type=ExchangeType.FANOUT,
            durable=True,
            auto_delete=False,
        )
        logger.info(
            "event=exchange_created group_id=%s exchange=%s",
            self._group_id,
            self._exchange_name,
        )

        # 创建Leader的队列
        leader_queue_name = f"leader-{self.leader_aic}-{self._group_id}"
        self._leader_queue = await self._channel.declare_queue(
            name=leader_queue_name, durable=True, exclusive=False, auto_delete=True
        )

        # 绑定队列到Exchange
        await self._leader_queue.bind(self._exchange)
        logger.info(
            "event=leader_queue_created group_id=%s queue=%s",
            self._group_id,
            leader_queue_name,
        )

        self._state = GroupLeaderState.READY
        return self._group_id

    def set_message_handler(self, handler: MessageHandler) -> None:
        """设置消息处理回调"""
        self._message_handler = handler

    async def start_consuming(self) -> None:
        """开始消费消息"""
        if not self._leader_queue:
            raise RuntimeError("Group not created yet")

        async def on_message(message: aio_pika.IncomingMessage):
            async with message.process():
                try:
                    body = json.loads(message.body.decode())
                    msg_type = body.get("type", "unknown")
                    sender_id = body.get("senderId", "unknown")
                    logger.debug(
                        "[GroupLeaderMq] >>> Message received: group_id=%s type=%s sender=%s",
                        self._group_id,
                        msg_type,
                        sender_id,
                    )
                    parsed_message = self._parse_incoming_message(body)
                    if parsed_message:
                        if self._message_handler:
                            logger.debug(
                                "[GroupLeaderMq] Calling message handler for type=%s",
                                msg_type,
                            )
                            await self._message_handler(parsed_message)
                        else:
                            logger.warning(
                                "[GroupLeaderMq] No message handler set, discarding message type=%s",
                                msg_type,
                            )
                    else:
                        logger.warning(
                            "[GroupLeaderMq] Failed to parse message type=%s",
                            msg_type,
                        )
                except Exception as e:
                    logger.error(
                        "event=message_processing_error group_id=%s error=%s",
                        self._group_id,
                        str(e),
                    )
                    import traceback

                    logger.error(
                        "[GroupLeaderMq] Traceback: %s", traceback.format_exc()
                    )

        self._consume_task = asyncio.create_task(self._leader_queue.consume(on_message))
        logger.info("event=consuming_started group_id=%s", self._group_id)

    def _parse_incoming_message(
        self, body: Dict[str, Any]
    ) -> Optional[Union[TaskCommand, TaskResult, GroupMgmtCommand]]:
        """解析接收到的消息"""
        msg_type = body.get("type")

        try:
            if msg_type == "group-mgmt-command":
                return GroupMgmtCommand.model_validate(body)
            elif msg_type == "task-result":
                return TaskResult.model_validate(body)
            elif msg_type == "task-command":
                return TaskCommand.model_validate(body)
            else:
                logger.warning(
                    "event=unknown_message_type group_id=%s type=%s",
                    self._group_id,
                    msg_type,
                )
                return None
        except Exception as e:
            logger.error(
                "event=message_parse_error group_id=%s error=%s body=%s",
                self._group_id,
                str(e),
                str(body)[:200],
            )
            return None

    async def invite_partner(
        self,
        partner_acs: ACSObject,
        partner_rpc_url: str,
        ssl_context=None,
        client_cert=None,
        timeout: float = 30.0,
    ) -> PartnerConnectionInfo:
        """
        邀请Partner加入群组

        通过RPC方式向Partner发送群组邀请请求

        Args:
            partner_acs: Partner的ACS信息
            partner_rpc_url: Partner的RPC端点URL
            ssl_context: SSL上下文
            timeout: 超时时间（秒）

        Returns:
            Partner连接信息

        Raises:
            PartnerNetworkError: 网络请求失败
            PartnerResponseError: Partner拒绝或返回错误
            PartnerInviteError: 其他邀请错误
        """
        if self._state != GroupLeaderState.READY:
            raise RuntimeError(f"Cannot invite partner in state: {self._state}")

        # 构造Leader的ACS对象
        leader_acs = ACSObject(
            aic=self.leader_aic, skills=["task-management", "group-management"]
        )

        # 构造群组信息
        group_info = GroupInfo(
            groupId=self._group_id,
            leader=leader_acs,
            partners=self._partner_acs + [partner_acs],
        )

        # 构造服务器配置 - 使用guest账户
        server_config = RabbitMQServerConfig(
            host=self.rabbitmq_host,
            port=self.rabbitmq_port,
            vhost=self.rabbitmq_vhost,
            accessToken=f"{self.rabbitmq_user}:{self.rabbitmq_password}",  # 简化版：直接传账户信息
        )

        # 构造AMQP配置
        amqp_config = AMQPConfig(
            exchange=self._exchange_name, exchangeType="fanout", routingKey=""
        )

        # 构造RPC请求
        request = RabbitMQRequest(
            id=str(uuid.uuid4()),
            params=RabbitMQRequestParams(
                protocol="rabbitmq:4.0",
                group=group_info,
                server=server_config,
                amqp=amqp_config,
            ),
        )

        # 发送RPC请求（带重试）
        max_attempts = 3
        backoff_seconds = 1.0

        try:
            async with httpx.AsyncClient(
                verify=ssl_context,
                cert=client_cert,
                timeout=timeout,
            ) as client:
                for attempt in range(1, max_attempts + 1):
                    try:
                        logger.info(
                            "event=partner_invite_request partner_aic=%s url=%s attempt=%d",
                            partner_acs.aic,
                            partner_rpc_url,
                            attempt,
                        )
                        response = await client.post(
                            partner_rpc_url,
                            json=request.model_dump(exclude_none=True),
                            headers={"Content-Type": "application/json"},
                        )
                        response.raise_for_status()
                        break
                    except httpx.HTTPStatusError as exc:
                        status = exc.response.status_code
                        if status >= 500 and attempt < max_attempts:
                            logger.warning(
                                "event=partner_invite_retry partner_aic=%s url=%s status=%s attempt=%d",
                                partner_acs.aic,
                                partner_rpc_url,
                                status,
                                attempt,
                            )
                            await asyncio.sleep(backoff_seconds)
                            backoff_seconds *= 2
                            continue
                        raise
                    except httpx.RequestError:
                        if attempt < max_attempts:
                            logger.warning(
                                "event=partner_invite_retry partner_aic=%s url=%s attempt=%d",
                                partner_acs.aic,
                                partner_rpc_url,
                                attempt,
                            )
                            await asyncio.sleep(backoff_seconds)
                            backoff_seconds *= 2
                            continue
                        raise

                rpc_response = RabbitMQResponse.model_validate(response.json())

                if rpc_response.error:
                    error_msg = rpc_response.error.message
                    logger.error(
                        "event=partner_invite_failed partner_aic=%s error=%s",
                        partner_acs.aic,
                        error_msg,
                    )
                    raise PartnerResponseError(f"Partner refused: {error_msg}")

                if rpc_response.result:
                    result = rpc_response.result
                    partner_info = PartnerConnectionInfo(
                        aic=partner_acs.aic,
                        connection_name=result.connectionName,
                        vhost=result.vhost,
                        node_name=result.nodeName,
                        queue_name=result.queueName,
                        process_id=result.processId,
                    )
                    self._partners[partner_acs.aic] = partner_info
                    self._partner_acs.append(partner_acs)

                    logger.info(
                        "event=partner_joined group_id=%s partner_aic=%s",
                        self._group_id,
                        partner_acs.aic,
                    )
                    return partner_info

                raise PartnerResponseError("Empty result in response")

        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.error(
                "event=partner_invite_network_error partner_aic=%s url=%s error=%s",
                partner_acs.aic,
                partner_rpc_url,
                str(e),
            )
            raise PartnerNetworkError(f"Network error: {str(e)}") from e
        except PartnerInviteError:
            raise
        except Exception as e:
            logger.error(
                "event=partner_invite_exception partner_aic=%s url=%s error_type=%s error=%s",
                partner_acs.aic,
                partner_rpc_url,
                type(e).__name__,
                repr(e),
            )
            raise PartnerInviteError(f"Unexpected error: {str(e)}") from e

    async def publish_message(
        self, message: Union[TaskCommand, TaskResult, GroupMgmtCommand]
    ) -> None:
        """
        发布消息到群组

        Args:
            message: 要发布的消息
        """
        if not self._exchange:
            raise RuntimeError("Group not created yet")

        body = message.model_dump(exclude_none=True)
        amqp_message = AMQPMessage(
            body=json.dumps(body, ensure_ascii=False).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )

        await self._exchange.publish(amqp_message, routing_key="")
        logger.info(
            "event=message_published group_id=%s type=%s id=%s mentions=%s",
            self._group_id,
            body.get("type"),
            body.get("id"),
            body.get("mentions"),
        )

    async def send_task_command(
        self,
        command: TaskCommandType,
        task_id: str,
        session_id: str,
        text_content: Optional[str] = None,
        data_items: Optional[List[DataItem]] = None,
        mentions: Optional[Union[str, List[str]]] = None,
    ) -> TaskCommand:
        """
        发送任务命令

        Args:
            command: 任务命令类型
            task_id: 任务ID
            session_id: 会话ID
            text_content: 文本内容
            data_items: 数据项列表
            mentions: 提及的Partner列表

        Returns:
            发送的命令对象
        """
        items = data_items or []
        if text_content:
            items.insert(0, TextDataItem(text=text_content))

        task_command = TaskCommand(
            id=f"cmd-{uuid.uuid4()}",
            sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="leader",
            senderId=self.leader_aic,
            command=command,
            dataItems=items,
            taskId=task_id,
            groupId=self._group_id,
            sessionId=session_id,
            mentions=mentions,
        )

        await self.publish_message(task_command)
        return task_command

    async def start_task(
        self,
        session_id: str,
        text_content: str,
        task_id: Optional[str] = None,
        mentions: Optional[Union[str, List[str]]] = None,
    ) -> str:
        """
        在群组中启动新任务

        Args:
            session_id: 会话ID
            text_content: 任务内容
            task_id: 任务ID（可选，不提供则自动生成）
            mentions: 指定处理任务的Partner

        Returns:
            任务ID
        """
        task_id = task_id or f"task-{uuid.uuid4()}"

        await self.send_task_command(
            command=TaskCommandType.Start,
            task_id=task_id,
            session_id=session_id,
            text_content=text_content,
            mentions=mentions,
        )

        logger.info(
            "event=task_started group_id=%s task_id=%s mentions=%s",
            self._group_id,
            task_id,
            mentions,
        )
        return task_id

    async def continue_task(
        self,
        task_id: str,
        session_id: str,
        text_content: str,
        mentions: Optional[Union[str, List[str]]] = None,
    ) -> None:
        """继续任务"""
        await self.send_task_command(
            command=TaskCommandType.Continue,
            task_id=task_id,
            session_id=session_id,
            text_content=text_content,
            mentions=mentions,
        )
        logger.info(
            "event=task_continue_sent group_id=%s task_id=%s mentions=%s",
            self._group_id,
            task_id,
            mentions,
        )

    async def complete_task(
        self,
        task_id: str,
        session_id: str,
        text_content: Optional[str] = None,
        mentions: Optional[Union[str, List[str]]] = None,
    ) -> None:
        """完成任务"""
        await self.send_task_command(
            command=TaskCommandType.Complete,
            task_id=task_id,
            session_id=session_id,
            text_content=text_content,
            mentions=mentions,
        )
        logger.info(
            "event=task_complete_sent group_id=%s task_id=%s mentions=%s",
            self._group_id,
            task_id,
            mentions,
        )

    async def cancel_task(
        self, task_id: str, session_id: str, reason: Optional[str] = None
    ) -> None:
        """取消任务"""
        await self.send_task_command(
            command=TaskCommandType.Cancel,
            task_id=task_id,
            session_id=session_id,
            text_content=reason,
        )

    async def send_mgmt_command(
        self,
        command: GroupMgmtCommandType,
        mentions: Optional[Union[str, List[str]]] = None,
    ) -> GroupMgmtCommand:
        """
        发送群组管理命令

        Args:
            command: 管理命令类型
            mentions: 目标成员

        Returns:
            发送的管理命令对象
        """
        mgmt_command = GroupMgmtCommand(
            id=f"mgmt-cmd-{uuid.uuid4()}",
            sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="leader",
            senderId=self.leader_aic,
            dataItems=[],
            command=command,
            mentions=mentions,
            groupId=self._group_id,
        )

        await self.publish_message(mgmt_command)
        return mgmt_command

    async def get_partner_status(
        self, partner_aic: Optional[str] = None
    ) -> GroupMgmtCommand:
        """
        获取Partner状态

        Args:
            partner_aic: 指定Partner的AIC，如果不提供则查询所有Partner
        """
        mentions = [partner_aic] if partner_aic else "all"
        return await self.send_mgmt_command(
            command=GroupMgmtCommandType.GET_STATUS, mentions=mentions
        )

    async def request_partner_leave(self, partner_aic: str) -> GroupMgmtCommand:
        """
        要求Partner退出群组

        Args:
            partner_aic: Partner的AIC
        """
        return await self.send_mgmt_command(
            command=GroupMgmtCommandType.LEAVE_GROUP, mentions=[partner_aic]
        )

    async def mute_partner(self, partner_aic: str) -> GroupMgmtCommand:
        """静音Partner"""
        msg = await self.send_mgmt_command(
            command=GroupMgmtCommandType.MUTE, mentions=[partner_aic]
        )
        if partner_aic in self._partners:
            self._partners[partner_aic].muted = True
        return msg

    async def unmute_partner(self, partner_aic: str) -> GroupMgmtCommand:
        """取消静音Partner"""
        msg = await self.send_mgmt_command(
            command=GroupMgmtCommandType.UNMUTE, mentions=[partner_aic]
        )
        if partner_aic in self._partners:
            self._partners[partner_aic].muted = False
        return msg

    def handle_partner_status_update(self, mgmt_result: GroupMgmtCommand) -> None:
        """
        处理Partner状态更新消息

        Args:
            mgmt_result: Partner发送的状态消息（GroupMgmtCommand 包含 status）
        """
        sender_id = mgmt_result.senderId
        # 检查是否有 status 属性 (GroupMgmtResult 才有)
        status = getattr(mgmt_result, "status", None)
        if sender_id and sender_id in self._partners and status:
            self._partners[sender_id].connected = status.connected
            self._partners[sender_id].muted = status.muted

            if not status.connected:
                logger.info(
                    "event=partner_disconnected group_id=%s partner_aic=%s",
                    self._group_id,
                    sender_id,
                )

    async def dissolve_group(self) -> None:
        """解散群组"""
        if self._state == GroupLeaderState.DISCONNECTED:
            return

        self._state = GroupLeaderState.DISSOLVING

        # 1. 通知所有成员退出
        try:
            await self.send_mgmt_command(
                command=GroupMgmtCommandType.LEAVE_GROUP, mentions="all"
            )
            # 给成员一些时间处理
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(
                "event=dissolve_notify_failed group_id=%s error=%s",
                self._group_id,
                str(e),
            )

        # 2. 停止消费
        if self._consume_task:
            self._consume_task.cancel()
            try:
                await self._consume_task
            except asyncio.CancelledError:
                pass

        # 3. 删除队列和Exchange
        try:
            if self._leader_queue:
                logger.info(
                    "event=queue_delete_attempt group_id=%s queue=%s",
                    self._group_id,
                    self._leader_queue.name,
                )
                await self._leader_queue.delete(if_unused=False, if_empty=False)
            if self._exchange:
                logger.info(
                    "event=exchange_delete_attempt group_id=%s exchange=%s",
                    self._group_id,
                    self._exchange_name,
                )
                await self._exchange.delete(if_unused=False)
        except Exception as e:
            logger.warning(
                "event=resource_cleanup_failed group_id=%s error=%s",
                self._group_id,
                str(e),
            )

        # 4. 关闭连接
        if self._channel and not getattr(self._channel, "is_closed", False):
            await self._channel.close()
        if (
            self._connection_owner
            and self._connection
            and not getattr(self._connection, "is_closed", False)
        ):
            await self._connection.close()

        self._state = GroupLeaderState.DISCONNECTED
        self._partners.clear()

        logger.info("event=group_dissolved group_id=%s", self._group_id)

    async def close(self) -> None:
        """关闭客户端连接"""
        if self._state != GroupLeaderState.DISCONNECTED:
            await self.dissolve_group()


class GroupLeaderSession:
    """
    群组会话管理

    封装一个完整的群组会话，包含：
    - 群组客户端
    - 会话状态
    - 任务状态跟踪
    - 消息历史
    """

    def __init__(
        self,
        session_id: str,
        group_id: str,
        leader_mq_client: GroupLeaderMqClient,
    ):
        self.MAX_HISTORY = 1000
        self.session_id = session_id
        self.group_id = group_id
        self.leader_mq_client = leader_mq_client
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.last_modified = datetime.now(timezone.utc)

        # 任务状态跟踪
        # task_id -> { partner_aic -> TaskState }
        self.task_states: Dict[str, Dict[str, TaskState]] = {}

        # Partner产出物
        # task_id -> { partner_aic -> product_text }
        self.task_products: Dict[str, Dict[str, str]] = {}

        # Partner等待输入的提示信息
        # task_id -> { partner_aic -> AwaitingInput 提示 }
        self.task_prompts: Dict[str, Dict[str, Optional[str]]] = {}

        # 消息历史
        self.message_history: List[Union[TaskCommand, TaskResult, GroupMgmtCommand]] = (
            []
        )

        # 状态更新事件
        self.state_update_event = asyncio.Event()

    def touch(self) -> None:
        """更新最后修改时间"""
        self.last_modified = datetime.now(timezone.utc)

    def record_task_state(
        self,
        task_id: str,
        partner_aic: str,
        state: TaskState,
        product_text: Optional[str] = None,
        awaiting_prompt: Optional[str] = None,
    ) -> None:
        """记录任务状态"""
        self.touch()
        if task_id not in self.task_states:
            self.task_states[task_id] = {}
            logger.debug(
                "[GroupLeaderSession] Created new task entry: session=%s task_id=%s",
                self.session_id,
                task_id,
            )

        old_state = self.task_states[task_id].get(partner_aic)
        self.task_states[task_id][partner_aic] = state

        logger.info(
            "[GroupLeaderSession] Task state updated: session=%s task_id=%s partner=%s old_state=%s new_state=%s total_partners=%d",
            self.session_id,
            task_id,
            partner_aic,
            old_state,
            state,
            len(self.task_states[task_id]),
        )

        if product_text:
            if task_id not in self.task_products:
                self.task_products[task_id] = {}
            self.task_products[task_id][partner_aic] = product_text

        # 处理额外的 prompt 信息
        if awaiting_prompt is not None:
            self.task_prompts.setdefault(task_id, {})[partner_aic] = awaiting_prompt
        elif task_id in self.task_prompts:
            self.task_prompts[task_id].pop(partner_aic, None)

        # 触发状态更新事件
        self.state_update_event.set()

    def get_partner_task_snapshot(
        self, task_id: str, partner_aic: str
    ) -> Optional[Dict[str, Any]]:
        """
        获取特定Partner在指定任务中的状态快照

        包含：
        - 当前状态 (state)
        - 产出物 (product_text)
        - 等待输入的提示 (awaiting_prompt)
        """
        state = self.task_states.get(task_id, {}).get(partner_aic)
        if not state:
            return None
        return {
            "task_id": task_id,
            "state": state,
            "product_text": self.task_products.get(task_id, {}).get(partner_aic),
            "awaiting_prompt": self.task_prompts.get(task_id, {}).get(partner_aic),
        }

    def get_task_summary(self, task_id: str) -> Dict[str, Any]:
        """获取任务摘要"""
        return {
            "task_id": task_id,
            "states": self.task_states.get(task_id, {}),
            "products": self.task_products.get(task_id, {}),
            "completed_count": sum(
                1
                for s in self.task_states.get(task_id, {}).values()
                if s == TaskState.Completed
            ),
            "working_count": sum(
                1
                for s in self.task_states.get(task_id, {}).values()
                if s in (TaskState.Accepted, TaskState.Working)
            ),
        }

    def record_message(
        self, message: Union[TaskCommand, TaskResult, GroupMgmtCommand]
    ) -> None:
        """记录消息到历史"""
        self.touch()
        self.message_history.append(message)
        if len(self.message_history) > self.MAX_HISTORY:
            self.message_history = self.message_history[-self.MAX_HISTORY :]


class GroupLeader:
    """
    通用群组Leader实现

    提供：
    - MQ连接管理
    - 会话(Session)管理
    - 任务生命周期管理
    - 成员管理
    """

    def __init__(
        self,
        leader_aic: str,
        rabbitmq_config: Dict[str, Any],
        ssl_context=None,
        ssl_cert: Optional[tuple] = None,
    ) -> None:
        self.leader_aic = leader_aic
        self.ssl_context = ssl_context
        self.ssl_cert = ssl_cert
        self.rabbitmq_host = rabbitmq_config.get("host", "localhost")
        self.rabbitmq_port = rabbitmq_config.get("port", 5672)
        self.rabbitmq_vhost = rabbitmq_config.get("vhost", "/")
        self.rabbitmq_user = rabbitmq_config.get("user", "guest")
        self.rabbitmq_password = rabbitmq_config.get("password", "guest")
        self._connection: Optional[AbstractConnection] = None
        self._connection_lock = asyncio.Lock()
        self.group_sessions: Dict[str, GroupLeaderSession] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

    async def _ensure_connection(self) -> AbstractConnection:
        async with self._connection_lock:
            if self._connection and not getattr(self._connection, "is_closed", False):
                return self._connection

            connection_url = (
                f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}"
                f"@{self.rabbitmq_host}:{self.rabbitmq_port}{self.rabbitmq_vhost}"
            )

            self._connection = await aio_pika.connect_robust(
                connection_url,
                client_properties={"connection_name": f"leader-{self.leader_aic}"},
            )
            logger.info(
                "event=rabbitmq_shared_connection_ready leader_aic=%s host=%s vhost=%s",
                self.leader_aic,
                self.rabbitmq_host,
                self.rabbitmq_vhost,
            )
            return self._connection

    def _get_session(self, session_id: Optional[str]) -> GroupLeaderSession:
        if session_id:
            session = self.group_sessions.get(session_id)
            if not session:
                raise ValueError(f"Group session not found: {session_id}")
            return session

        if len(self.group_sessions) == 1:
            return next(iter(self.group_sessions.values()))

        raise ValueError("Multiple sessions active; please specify session_id")

    # ------------------------------------------------------------------
    # 群组初始化与成员邀请
    # ------------------------------------------------------------------
    async def create_group_session(
        self, session_id: str, initial_partners: List[ACSObject]
    ) -> GroupLeaderSession:
        """
        创建群组会话

        Args:
            session_id: 会话ID
            initial_partners: 初始Partner列表
        """
        connection = await self._ensure_connection()

        session_client = GroupLeaderMqClient(
            leader_aic=self.leader_aic,
            rabbitmq_host=self.rabbitmq_host,
            rabbitmq_port=self.rabbitmq_port,
            rabbitmq_vhost=self.rabbitmq_vhost,
            rabbitmq_user=self.rabbitmq_user,
            rabbitmq_password=self.rabbitmq_password,
            ssl_context=self.ssl_context,
            connection=connection,
            connection_owner=False,
        )

        await session_client.connect()

        group_id = await session_client.create_group(
            group_id=f"group-{session_id}", partner_acs_list=initial_partners
        )

        session_client.set_message_handler(self._handle_group_message)
        try:
            await session_client.start_consuming()
        except Exception:
            # 如果消费启动失败，尝试清理由刚创建的资源，保持一致性
            try:
                await session_client.dissolve_group()
            finally:
                raise

        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        session = GroupLeaderSession(
            session_id=session_id,
            group_id=group_id,
            leader_mq_client=session_client,
        )
        self.group_sessions[session_id] = session
        logger.info(
            "event=group_session_created session_id=%s group_id=%s partner_count=%d",
            session_id,
            group_id,
            len(initial_partners),
        )
        return session

    async def invite_partner(
        self,
        session_id: str,
        partner_acs: ACSObject,
        partner_rpc_url: str,
    ) -> bool:
        """
        邀请Partner加入会话

        Args:
            session_id: 会话ID
            partner_acs: Partner ACS信息
            partner_rpc_url: Partner RPC地址
        """
        session = self.group_sessions.get(session_id)
        if not session:
            raise ValueError(f"Group session not found: {session_id}")

        logger.info(
            "event=partner_invite_begin partner=%s url=%s",
            partner_acs.aic,
            partner_rpc_url,
        )

        await session.leader_mq_client.invite_partner(
            partner_acs=partner_acs,
            partner_rpc_url=partner_rpc_url,
            ssl_context=self.ssl_context,
            client_cert=self.ssl_cert,
        )

        logger.info(
            "event=partner_invited session_id=%s partner=%s",
            session_id,
            partner_acs.aic,
        )
        return True

    # ------------------------------------------------------------------
    # 任务控制
    # ------------------------------------------------------------------
    async def start_task(
        self,
        session_id: str,
        *,
        task_content: str,
        task_id: Optional[str] = None,
        target_partners: Optional[List[str]] = None,
    ) -> str:
        """启动任务"""
        session = self.group_sessions.get(session_id)
        if not session:
            raise ValueError(f"Group session not found: {session_id}")

        return await session.leader_mq_client.start_task(
            session_id=session_id,
            text_content=task_content,
            task_id=task_id,
            mentions=target_partners,
        )

    async def continue_task(
        self,
        session_id: str,
        task_id: str,
        content: str,
        target_partner: Optional[str] = None,
    ) -> None:
        """继续任务"""
        mentions = [target_partner] if target_partner else None
        session = self.group_sessions.get(session_id)
        if not session:
            raise ValueError(f"Group session not found: {session_id}")

        await session.leader_mq_client.continue_task(
            task_id=task_id,
            session_id=session_id,
            text_content=content,
            mentions=mentions,
        )

    async def complete_task(
        self,
        session_id: str,
        task_id: str,
        target_partner: Optional[str] = None,
    ) -> None:
        """完成任务"""
        mentions = [target_partner] if target_partner else None
        session = self.group_sessions.get(session_id)
        if not session:
            raise ValueError(f"Group session not found: {session_id}")

        await session.leader_mq_client.complete_task(
            task_id=task_id,
            session_id=session_id,
            mentions=mentions,
        )

    async def cancel_task(
        self,
        session_id: str,
        task_id: str,
        reason: Optional[str] = None,
    ) -> None:
        """取消任务"""
        session = self.group_sessions.get(session_id)
        if not session:
            raise ValueError(f"Group session not found: {session_id}")

        await session.leader_mq_client.cancel_task(
            task_id=task_id,
            session_id=session_id,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # 群组管理命令
    # ------------------------------------------------------------------
    async def check_partner_status(
        self, partner_aic: Optional[str] = None, session_id: Optional[str] = None
    ) -> GroupMgmtCommand:
        session = self._get_session(session_id)
        return await session.leader_mq_client.get_partner_status(partner_aic)

    async def request_partner_leave(
        self, partner_aic: str, session_id: Optional[str] = None
    ) -> GroupMgmtCommand:
        session = self._get_session(session_id)
        return await session.leader_mq_client.request_partner_leave(partner_aic)

    async def dissolve_group_session(self, session_id: str) -> None:
        session = self.group_sessions.pop(session_id, None)
        if session:
            await session.leader_mq_client.dissolve_group()

    async def close(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        for session_id in list(self.group_sessions.keys()):
            await self.dissolve_group_session(session_id)

        if self._connection and not getattr(self._connection, "is_closed", False):
            await self._connection.close()

    # ------------------------------------------------------------------
    # 会话清理机制
    # ------------------------------------------------------------------
    async def _cleanup_loop(self) -> None:
        """定期清理过期会话"""
        logger.info("event=cleanup_task_started")
        while True:
            try:
                await asyncio.sleep(300)  # 每5分钟检查一次
                await self._cleanup_expired_sessions()
                if not self.group_sessions:
                    logger.info("event=cleanup_task_stopped reason=no_active_sessions")
                    self._cleanup_task = None
                    break
            except asyncio.CancelledError:
                logger.info("event=cleanup_task_stopped")
                self._cleanup_task = None
                break
            except Exception as e:
                logger.error("event=cleanup_task_error error=%s", str(e))
                await asyncio.sleep(60)

    async def _cleanup_expired_sessions(self) -> None:
        """清理超时未活动的会话"""
        timeout_seconds = 3600  # 1小时超时
        now = datetime.now(timezone.utc)
        expired_sessions = []

        for session_id, session in self.group_sessions.items():
            if (now - session.last_modified).total_seconds() > timeout_seconds:
                expired_sessions.append(session_id)

        for session_id in expired_sessions:
            logger.info(
                "event=session_expired_cleanup session_id=%s reason=inactive_timeout",
                session_id,
            )
            await self.dissolve_group_session(session_id)

    # ------------------------------------------------------------------
    # RabbitMQ 消息回调
    # ------------------------------------------------------------------
    async def _handle_group_message(
        self, message: Union[TaskCommand, TaskResult, GroupMgmtCommand]
    ) -> None:
        session_id = getattr(message, "sessionId", None)
        msg_type = type(message).__name__
        sender_id = getattr(message, "senderId", "unknown")

        logger.debug(
            "[GroupLeader] >>> _handle_group_message: type=%s sender=%s session_id=%s",
            msg_type,
            sender_id,
            session_id,
        )

        if not session_id:
            logger.warning(
                "[GroupLeader] Message has no sessionId, discarding: type=%s sender=%s",
                msg_type,
                sender_id,
            )
            return
        session = self.group_sessions.get(session_id)
        if not session:
            logger.warning(
                "[GroupLeader] Session not found for message: session_id=%s, available=%s",
                session_id,
                list(self.group_sessions.keys()),
            )
            return

        logger.debug(
            "[GroupLeader] Session found, recording message: session_id=%s type=%s",
            session_id,
            msg_type,
        )
        session.record_message(message)

        if isinstance(message, TaskResult):
            logger.info(
                "[GroupLeader] Processing TaskResult: session_id=%s sender=%s task_id=%s state=%s",
                session_id,
                sender_id,
                message.taskId,
                message.status.state if message.status else "no_status",
            )
            await self._handle_task_update(session, message)
        elif isinstance(message, GroupMgmtCommand):
            # 更新本地的 partner 状态（连接/静音等），并做额外日志处理
            session.leader_mq_client.handle_partner_status_update(message)
            self._handle_mgmt_command(message)
            logger.info(
                "event=mgmt_command_leader_received session_id=%s command=%s sender=%s",
                session_id,
                message.command,
                message.senderId,
            )

    async def _handle_task_update(
        self, group_session: GroupLeaderSession, task: TaskResult
    ) -> None:
        sender_id = task.senderId
        if not sender_id:
            logger.warning("[GroupLeader] TaskResult has no senderId, skipping")
            return
        task_id = task.taskId
        state = task.status.state
        product_text = self._extract_text_from_products(task)
        awaiting_prompt = self._extract_text_from_status(task)

        # 调试日志：显示提取到的内容
        short_sender = sender_id[-8:] if len(sender_id) > 8 else sender_id
        logger.debug(
            "[GroupLeader] Task update details: partner=%s state=%s "
            "has_products=%s product_len=%s has_prompt=%s prompt_len=%s",
            short_sender,
            state,
            task.products is not None and len(task.products) > 0,
            len(product_text) if product_text else 0,
            awaiting_prompt is not None,
            len(awaiting_prompt) if awaiting_prompt else 0,
        )

        # 额外调试：显示 product 内容的前100字符
        if product_text:
            logger.info(
                "[GroupLeader] Product content preview for %s: %s",
                short_sender,
                product_text[:100] if len(product_text) > 100 else product_text,
            )

        logger.info(
            "[GroupLeader] Recording task state: session=%s task_id=%s partner=%s state=%s",
            group_session.session_id,
            task_id,
            sender_id,
            state,
        )

        group_session.record_task_state(
            task_id=task_id,
            partner_aic=sender_id,
            state=state,
            product_text=product_text,
            awaiting_prompt=awaiting_prompt,
        )

        logger.info(
            "[GroupLeader] Task state recorded successfully: session=%s task_id=%s partner=%s",
            group_session.session_id,
            task_id,
            sender_id,
        )

    def _handle_mgmt_command(self, message: GroupMgmtCommand) -> None:
        if message.command in (
            GroupMgmtCommandType.MUTE,
            GroupMgmtCommandType.UNMUTE,
            GroupMgmtCommandType.LEAVE_GROUP,
        ):
            logger.info(
                "event=group_mgmt_command command=%s sender=%s",
                message.command,
                message.senderId,
            )

    @staticmethod
    def _extract_text_from_products(task: TaskResult) -> Optional[str]:
        if not task.products:
            return None
        parts: List[str] = []
        for product in task.products:
            for item in product.dataItems:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
        return "\n".join(parts) if parts else None

    @staticmethod
    def _extract_text_from_status(task: TaskResult) -> Optional[str]:
        items = task.status.dataItems or []
        texts = [
            getattr(item, "text", None) for item in items if getattr(item, "text", None)
        ]
        return "\n".join(texts) if texts else None
