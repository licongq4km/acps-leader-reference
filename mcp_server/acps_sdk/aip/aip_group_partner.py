"""
群组模式 Partner 客户端模块

本模块实现了 AIP 协议中群组模式 (Group Mode) 下 Partner 角色的核心功能：
1. 接收 Leader 发送的群组邀请 (RabbitMQRequest)
2. 连接到 RabbitMQ 消息队列服务器
3. 创建自己的队列并绑定到群组 Exchange
4. 接收和处理群组内的消息（任务消息、管理消息）
5. 发送任务状态更新和产出物到群组
6. 响应群组管理命令（状态查询、退出、静音等）
"""

from __future__ import annotations
import asyncio
import json
import uuid
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Callable, Awaitable, Union
from enum import Enum

import aio_pika
from aio_pika import Message as AMQPMessage, ExchangeType
from aio_pika.abc import (
    AbstractChannel,
    AbstractConnection,
    AbstractQueue,
    AbstractExchange,
)

from .aip_base_model import (
    TaskCommand,
    TaskResult,
    TaskCommandType,
    TaskState,
    TaskStatus,
    TextDataItem,
    DataItem,
    Product,
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
    RabbitMQResponseError,
    RabbitMQResponseErrorData,
    GroupMgmtCommand,
    GroupMgmtResult,
    GroupMgmtCommandType,
    GroupMemberStatus,
)


logger = logging.getLogger("acps_sdk.aip.group_partner")


class PartnerGroupState(str, Enum):
    """Partner 群组连接状态"""

    DISCONNECTED = "disconnected"  # 未连接
    JOINED = "joined"  # 已加入
    LEAVING = "leaving"  # 正在退出


# 消息处理回调类型
# (command, is_mentioned) -> None
TaskCommandHandler = Callable[[TaskCommand, bool], Awaitable[None]]
TaskResultHandler = Callable[[TaskResult], Awaitable[None]]
MgmtCommandHandler = Callable[[GroupMgmtCommand], Awaitable[None]]


class GroupPartnerMqClient:
    """
    群组模式 Partner 客户端

    负责：
    - 接收群组邀请并连接到 RabbitMQ
    - 创建自己的队列并绑定到群组 Exchange
    - 接收和处理群组消息
    - 发送任务状态更新和产出物
    - 响应群组管理命令
    """

    def __init__(self, partner_aic: str):
        """
        初始化 Partner 客户端

        Args:
            partner_aic: Partner 的 AIC 标识
        """
        self.partner_aic = partner_aic

        # 连接和通道
        self._connection: Optional[AbstractConnection] = None
        self._channel: Optional[AbstractChannel] = None
        self._pending_leave: bool = False

        # 群组信息
        self._group_id: Optional[str] = None
        self._group_info: Optional[GroupInfo] = None
        self._exchange_name: Optional[str] = None
        self._exchange: Optional[AbstractExchange] = None
        self._queue: Optional[AbstractQueue] = None
        self._queue_name: Optional[str] = None

        # 连接信息（用于返回给 Leader）
        self._connection_name: Optional[str] = None
        self._vhost: Optional[str] = None
        self._node_name: Optional[str] = None

        # 状态
        self._state: PartnerGroupState = PartnerGroupState.DISCONNECTED
        # 静音状态。注意：当前实现中，暂时没有强制限制，依靠 Partner 业务逻辑自觉遵守。
        self._muted: bool = False

        # 消息处理器
        self._command_handler: Optional[TaskCommandHandler] = None
        self._task_result_handler: Optional[TaskResultHandler] = None
        self._mgmt_command_handler: Optional[MgmtCommandHandler] = None
        self._consume_task: Optional[asyncio.Task] = None

        # 任务跟踪
        # task_id -> TaskState
        self._task_states: Dict[str, TaskState] = {}

    @property
    def group_id(self) -> Optional[str]:
        return self._group_id

    @property
    def state(self) -> PartnerGroupState:
        return self._state

    @property
    def is_joined(self) -> bool:
        return (
            self._state == PartnerGroupState.JOINED
            and self._connection is not None
            and not self._connection.is_closed
        )

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def queue_name(self) -> Optional[str]:
        return self._queue_name

    def set_command_handler(self, handler: TaskCommandHandler) -> None:
        """
        设置任务命令处理回调（必须）。Leader发出的命令消息需要在此回调函数处理。

        用于处理所有群组任务命令。
        Args:
            handler: 回调函数，接收 (command, is_mentioned) 参数
                     - command: TaskCommand 对象
                     - is_mentioned: 是否被提及（即是否需要作为任务处理）
        """
        self._command_handler = handler

    def set_task_result_handler(self, handler: TaskResultHandler) -> None:
        """设置任务结果处理回调（可选）。其它Partner发出的任务状态更新通过此回调处理（通常不需要处理，避免混乱自己的状态机）。"""
        self._task_result_handler = handler

    def set_mgmt_command_handler(self, handler: MgmtCommandHandler) -> None:
        """设置管理命令处理回调（可选）。用于处理群组管理消息的额外操作。通用操作如静音、状态查询等已经内置处理。"""
        self._mgmt_command_handler = handler

    async def join_group(self, request: RabbitMQRequest) -> RabbitMQResponse:
        """
        处理群组加入邀请

        根据设计文档 6.2.2 的流程：
        1. 接收邀请
        2. 建立连接
        3. 验证 Exchange
        4. 创建队列
        5. 绑定 Exchange
        6. 开始监听
        7. 返回连接信息
        8. 通知加入（发送 GroupMgmtMessage）

        Args:
            request: Leader 发送的群组邀请请求

        Returns:
            RabbitMQResponse 包含连接信息或错误
        """
        logger.info(
            "event=partner_join_invite_received partner_aic=%s group_id=%s exchange=%s host=%s",
            self.partner_aic,
            getattr(request.params.group, "groupId", None),
            getattr(request.params.amqp, "exchange", None),
            getattr(request.params.server, "host", None),
        )

        params = request.params
        server = params.server
        amqp = params.amqp
        group = params.group

        self._group_id = group.groupId
        self._group_info = group
        self._exchange_name = amqp.exchange
        self._vhost = server.vhost

        # 解析 accessToken（当前简化版：user:password 格式）
        # 设计文档要求使用 JWT，但这里暂时使用简化方式
        if ":" in server.accessToken:
            username, password = server.accessToken.split(":", 1)
        else:
            # 如果不是 user:password 格式，使用 guest
            username = "guest"
            password = "guest"

        try:
            # 2. 建立连接
            connection_url = (
                f"amqp://{username}:{password}"
                f"@{server.host}:{server.port}{server.vhost}"
            )

            self._connection = await aio_pika.connect_robust(
                connection_url,
                client_properties={"connection_name": f"partner-{self.partner_aic}"},
            )
            self._channel = await self._connection.channel()

            # 获取连接信息
            # 注意：aio_pika 不直接暴露 connection_name，这里使用自定义名称
            self._connection_name = f"partner-{self.partner_aic}-{self._group_id}"
            # node_name 需要从 RabbitMQ 获取，这里使用占位符
            self._node_name = "rabbit@localhost"

            logger.info(
                "event=rabbitmq_connected partner_aic=%s group_id=%s host=%s",
                self.partner_aic,
                self._group_id,
                server.host,
            )

            # 3. 验证 Exchange（通过声明 passive=True）
            try:
                self._exchange = await self._channel.get_exchange(self._exchange_name)
            except Exception:
                # Exchange 不存在，尝试重新声明（Leader 应该已经创建）
                self._exchange = await self._channel.declare_exchange(
                    name=self._exchange_name,
                    type=ExchangeType.FANOUT,
                    durable=True,
                    auto_delete=False,
                    passive=True,  # 只检查存在，不创建
                )

            # 4. 创建队列
            self._queue_name = f"partner-{self.partner_aic}-{self._group_id}"
            self._queue = await self._channel.declare_queue(
                name=self._queue_name,
                durable=True,
                exclusive=False,
                auto_delete=True,  # Partner 断开连接后自动删除
            )

            # 5. 绑定 Exchange
            await self._queue.bind(self._exchange)

            logger.info(
                "event=queue_created partner_aic=%s queue=%s exchange=%s",
                self.partner_aic,
                self._queue_name,
                self._exchange_name,
            )

            # 6. 开始监听
            await self._start_consuming()

            self._state = PartnerGroupState.JOINED

            # 8. 通知加入（发送 GroupMgmtMessage）
            await self._notify_joined()

            # 7. 返回连接信息
            return RabbitMQResponse(
                jsonrpc="2.0",
                id=request.id,
                result=RabbitMQResponseResult(
                    connectionName=self._connection_name,
                    vhost=self._vhost,
                    nodeName=self._node_name,
                    queueName=self._queue_name,
                    processId=f"pid-{os.getpid()}",
                ),
            )

        except Exception as e:
            logger.error(
                "event=join_group_failed partner_aic=%s error=%s",
                self.partner_aic,
                str(e),
            )
            self._state = PartnerGroupState.DISCONNECTED

            return RabbitMQResponse(
                jsonrpc="2.0",
                id=request.id,
                error=RabbitMQResponseError(
                    code=-32603,
                    message="Internal error",
                    data=RabbitMQResponseErrorData(
                        errorType="CONNECTION_FAILED",
                        details={
                            "host": server.host,
                            "port": server.port,
                            "reason": str(e),
                        },
                    ),
                ),
            )

    async def _start_consuming(self) -> None:
        """开始消费消息"""
        if not self._queue:
            raise RuntimeError("Queue not created yet")

        async def on_message(message: aio_pika.IncomingMessage):
            async with message.process():
                try:
                    body = json.loads(message.body.decode())
                    logger.info(
                        "event=queue_message_received partner_aic=%s msg_type=%s sender=%s",
                        self.partner_aic,
                        body.get("type"),
                        body.get("senderId"),
                    )
                    await self._handle_incoming_message(body)
                except Exception as e:
                    logger.error(
                        "event=message_processing_error partner_aic=%s error=%s",
                        self.partner_aic,
                        str(e),
                    )
            if self._pending_leave:
                self._pending_leave = False
                await self._handle_leave_request()

        self._consume_task = asyncio.create_task(self._queue.consume(on_message))
        logger.info(
            "event=consuming_started partner_aic=%s group_id=%s",
            self.partner_aic,
            self._group_id,
        )

    async def _handle_incoming_message(self, body: Dict[str, Any]) -> None:
        """处理接收到的消息"""
        msg_type = body.get("type")
        sender_id = body.get("senderId")

        # 忽略自己发送的消息
        if sender_id == self.partner_aic:
            return

        try:
            if msg_type == "group-mgmt-command":
                mgmt_cmd = GroupMgmtCommand.model_validate(body)
                logger.info(
                    "event=mgmt_command_received partner_aic=%s command=%s sender=%s",
                    self.partner_aic,
                    mgmt_cmd.command,
                    mgmt_cmd.senderId,
                )
                await self._handle_mgmt_command(mgmt_cmd)
            elif msg_type == "task-result":
                task_result = TaskResult.model_validate(body)
                if self._task_result_handler:
                    await self._task_result_handler(task_result)
            elif msg_type == "task-command":
                command = TaskCommand.model_validate(body)
                logger.info(
                    "event=task_command_received partner_aic=%s command=%s sender=%s task_id=%s",
                    self.partner_aic,
                    command.command,
                    command.senderId,
                    command.taskId,
                )
                await self._handle_task_command(command)
            else:
                logger.warning(
                    "event=unknown_message_type partner_aic=%s type=%s",
                    self.partner_aic,
                    msg_type,
                )
        except Exception as e:
            logger.error(
                "event=message_parse_error partner_aic=%s error=%s body=%s",
                self.partner_aic,
                str(e),
                str(body)[:200],
            )

    async def _handle_mgmt_command(self, command: GroupMgmtCommand) -> None:
        """处理群组管理命令"""
        # 检查是否需要响应
        is_targeted = command.must_respond(self.partner_aic)

        if command.command == GroupMgmtCommandType.GET_STATUS:
            if is_targeted:
                await self._send_status_response()
        elif command.command == GroupMgmtCommandType.LEAVE_GROUP:
            if is_targeted:
                self._pending_leave = True  # 延后执行，避免在 ack 前关闭 channel
        elif command.command == GroupMgmtCommandType.MUTE:
            if is_targeted:
                self._muted = True
                await self._send_status_response()
                logger.info(
                    "event=partner_muted partner_aic=%s group_id=%s",
                    self.partner_aic,
                    self._group_id,
                )
        elif command.command == GroupMgmtCommandType.UNMUTE:
            if is_targeted:
                self._muted = False
                await self._send_status_response()
                logger.info(
                    "event=partner_unmuted partner_aic=%s group_id=%s",
                    self.partner_aic,
                    self._group_id,
                )

        # 调用自定义处理器
        if self._mgmt_command_handler:
            await self._mgmt_command_handler(command)

    async def _handle_task_command(self, command: TaskCommand) -> None:
        """处理任务命令"""
        # 检查是否被提及
        mentions = command.mentions
        is_mentioned = False

        if mentions is None or (isinstance(mentions, list) and len(mentions) == 0):
            # 未指定 mentions，所有 Partner 都可以响应
            is_mentioned = True
        elif mentions == "all":
            is_mentioned = True
        elif isinstance(mentions, list) and self.partner_aic in mentions:
            is_mentioned = True

        # 统一调用命令处理器
        if self._command_handler:
            await self._command_handler(command, is_mentioned)

    async def _notify_joined(self) -> None:
        """通知群组自己已加入"""
        await self.send_status_result(connected=True, muted=False)
        logger.info(
            "event=partner_join_notified partner_aic=%s group_id=%s queue=%s",
            self.partner_aic,
            self._group_id,
            self._queue_name,
        )

    async def _send_status_response(self) -> None:
        """发送状态响应"""
        await self.send_status_result(
            connected=self._state == PartnerGroupState.JOINED, muted=self._muted
        )
        logger.info(
            "event=partner_status_response partner_aic=%s connected=%s muted=%s",
            self.partner_aic,
            self._state == PartnerGroupState.JOINED,
            self._muted,
        )

    async def _handle_leave_request(self) -> None:
        """处理退出请求"""
        self._state = PartnerGroupState.LEAVING

        # 发送断开状态消息
        await self.send_status_result(connected=False, muted=self._muted)

        # 清理资源
        await self.leave_group()

    async def send_status_result(self, connected: bool, muted: bool) -> None:
        """发送状态结果消息"""
        if not self._exchange:
            return

        result = GroupMgmtResult(
            id=f"mgmt-result-{uuid.uuid4()}",
            sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="partner",
            senderId=self.partner_aic,
            dataItems=[],
            status=GroupMemberStatus(connected=connected, muted=muted),
            groupId=self._group_id,
        )

        await self._publish_message(result)

    async def _publish_message(
        self, message: Union[TaskCommand, TaskResult, GroupMgmtCommand, GroupMgmtResult]
    ) -> None:
        """
        发布消息到群组

        注意：此方法目前未检查 _muted 状态。即使 Partner 被静音，
        仍然可以调用此方法发送消息。请业务层自觉遵守静音规则。
        """
        if not self._exchange:
            raise RuntimeError("Not connected to group")

        body = message.model_dump(exclude_none=True)
        amqp_message = AMQPMessage(
            body=json.dumps(body, ensure_ascii=False).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )

        await self._exchange.publish(amqp_message, routing_key="")
        logger.debug(
            "event=message_published partner_aic=%s type=%s id=%s",
            self.partner_aic,
            body.get("type"),
            body.get("id"),
        )

    async def send_task_result(
        self,
        task_id: str,
        session_id: str,
        state: TaskState,
        products: Optional[List[Product]] = None,
        status_data_items: Optional[List[DataItem]] = None,
    ) -> None:
        """
        发送任务状态更新

        Args:
            task_id: 任务 ID
            session_id: 会话 ID
            state: 新状态
            products: 产出物列表（AwaitingCompletion 时使用）
            status_data_items: 状态附带的数据项（如错误信息、提示等）
        """
        self._task_states[task_id] = state

        result = TaskResult(
            id=f"result-{uuid.uuid4()}",
            sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="partner",
            senderId=self.partner_aic,
            taskId=task_id,
            status=TaskStatus(
                state=state,
                stateChangedAt=datetime.now(timezone.utc).isoformat(),
                dataItems=status_data_items,
            ),
            products=products,
            groupId=self._group_id,
            sessionId=session_id,
        )

        await self._publish_message(result)
        logger.info(
            "event=task_result_sent partner_aic=%s task_id=%s state=%s",
            self.partner_aic,
            task_id,
            state,
        )

    async def accept_task(self, task_id: str, session_id: str) -> None:
        """接受任务"""
        await self.send_task_result(task_id, session_id, TaskState.Accepted)

    async def start_working(self, task_id: str, session_id: str) -> None:
        """开始工作"""
        await self.send_task_result(task_id, session_id, TaskState.Working)

    async def request_input(self, task_id: str, session_id: str, prompt: str) -> None:
        """请求更多输入"""
        await self.send_task_result(
            task_id,
            session_id,
            TaskState.AwaitingInput,
            status_data_items=[TextDataItem(text=prompt)],
        )

    async def submit_for_completion(
        self, task_id: str, session_id: str, products: List[Product]
    ) -> None:
        """提交产出物等待完成确认"""
        await self.send_task_result(
            task_id, session_id, TaskState.AwaitingCompletion, products=products
        )

    async def complete_task(self, task_id: str, session_id: str) -> None:
        """完成任务"""
        await self.send_task_result(task_id, session_id, TaskState.Completed)

    async def reject_task(self, task_id: str, session_id: str, reason: str) -> None:
        """拒绝任务"""
        await self.send_task_result(
            task_id,
            session_id,
            TaskState.Rejected,
            status_data_items=[TextDataItem(text=reason)],
        )

    async def fail_task(self, task_id: str, session_id: str, error: str) -> None:
        """任务失败"""
        await self.send_task_result(
            task_id,
            session_id,
            TaskState.Failed,
            status_data_items=[TextDataItem(text=error)],
        )

    async def cancel_task(self, task_id: str, session_id: str) -> None:
        """取消任务"""
        await self.send_task_result(task_id, session_id, TaskState.Canceled)

    async def leave_group(self) -> None:
        """退出群组"""
        if self._state == PartnerGroupState.DISCONNECTED:
            return

        self._state = PartnerGroupState.LEAVING

        # 停止消费
        if self._consume_task:
            self._consume_task.cancel()
            try:
                await self._consume_task
            except asyncio.CancelledError:
                pass
        logger.info(
            "event=consumer_stopped partner_aic=%s group_id=%s",
            self.partner_aic,
            self._group_id,
        )

        # 删除队列（允许强制删除，避免 consumer 占用导致 PRECONDITION_FAILED）
        try:
            if self._queue:
                logger.info(
                    "event=queue_delete_attempt partner_aic=%s queue=%s",
                    self.partner_aic,
                    self._queue.name,
                )
                await self._queue.delete(if_unused=False, if_empty=False)
        except Exception as e:
            logger.warning(
                "event=queue_delete_failed partner_aic=%s error=%s",
                self.partner_aic,
                str(e),
            )

        # 关闭连接
        if self._channel:
            await self._channel.close()
        if self._connection:
            await self._connection.close()

        logger.info(
            "event=connection_closed partner_aic=%s group_id=%s",
            self.partner_aic,
            self._group_id,
        )

        self._state = PartnerGroupState.DISCONNECTED
        logger.info(
            "event=left_group partner_aic=%s group_id=%s",
            self.partner_aic,
            self._group_id,
        )

    async def close(self) -> None:
        """关闭客户端"""
        if self._state != PartnerGroupState.DISCONNECTED:
            await self.leave_group()


class PartnerGroupSession:
    """
    Partner 群组会话

    封装单个 Partner 在群组中的会话状态和任务管理
    """

    def __init__(self, partner_aic: str, client: GroupPartnerMqClient):
        self.partner_aic = partner_aic
        self.client = client
        self.created_at = datetime.now(timezone.utc).isoformat()

        # 任务状态跟踪
        # task_id -> { state, products, ... }
        self.tasks: Dict[str, Dict[str, Any]] = {}

        # 消息历史
        self.message_history: List[Union[TaskCommand, TaskResult, GroupMgmtCommand]] = (
            []
        )

    def record_message(
        self, message: Union[TaskCommand, TaskResult, GroupMgmtCommand]
    ) -> None:
        """记录消息"""
        self.message_history.append(message)

    def update_task(
        self,
        task_id: str,
        state: TaskState,
        products: Optional[List[Product]] = None,
    ) -> None:
        """更新任务状态"""
        if task_id not in self.tasks:
            self.tasks[task_id] = {}
        self.tasks[task_id]["state"] = state
        self.tasks[task_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        if products:
            self.tasks[task_id]["products"] = products

    def get_task_state(self, task_id: str) -> Optional[TaskState]:
        """获取任务状态"""
        return self.tasks.get(task_id, {}).get("state")


def extract_text_from_command(command: TaskCommand) -> str:
    """
    辅助函数：从任务命令中提取文本内容

    将命令中所有 TextDataItem 的文本拼接起来
    """
    texts = []
    if command.dataItems:
        for item in command.dataItems:
            text = getattr(item, "text", None)
            if text:
                texts.append(str(text))
    return "\n".join(texts)
