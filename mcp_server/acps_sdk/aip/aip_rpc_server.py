from fastapi import FastAPI, Request, HTTPException
from pydantic import ValidationError
from datetime import datetime, timezone
import uuid
from typing import Dict, Optional, Callable, Awaitable

from .aip_base_model import (
    TaskResult,
    TaskStatus,
    TaskState,
    TaskCommand,
    Product,
    TextDataItem,
    TaskCommandType,
)
from .aip_rpc_model import RpcRequest, RpcResponse, JSONRPCError


# -------- Pluggable Command Framework --------
class CommandHandlers:
    """
    Per-command optional handlers. Implement the methods you want to override.
    Any method left as None will fall back to DefaultHandlers.

    Handler signature: (command: TaskCommand, task: Optional[TaskResult]) -> Awaitable[TaskResult]
    Return the updated TaskResult snapshot. If you need to return a custom RpcResponse,
    you can raise an exception yourself and set the task to Failed, but the
    recommended way is to update the Task via TaskManager and return it.
    """

    def __init__(
        self,
        on_start: Optional[
            Callable[[TaskCommand, Optional[TaskResult]], Awaitable[TaskResult]]
        ] = None,
        on_get: Optional[
            Callable[[TaskCommand, TaskResult], Awaitable[TaskResult]]
        ] = None,
        on_cancel: Optional[
            Callable[[TaskCommand, TaskResult], Awaitable[TaskResult]]
        ] = None,
        on_complete: Optional[
            Callable[[TaskCommand, TaskResult], Awaitable[TaskResult]]
        ] = None,
        on_continue: Optional[
            Callable[[TaskCommand, TaskResult], Awaitable[TaskResult]]
        ] = None,
        # Catch-all for unknown/unsupported/missing command messages
        on_message: Optional[
            Callable[[TaskCommand, Optional[TaskResult]], Awaitable[TaskResult]]
        ] = None,
    ):
        self.on_start = on_start
        self.on_get = on_get
        self.on_cancel = on_cancel
        self.on_complete = on_complete
        self.on_continue = on_continue
        self.on_message = on_message


class DefaultHandlers:
    """
    Built-in default behaviors for AIP commands. Agents can reuse these from their
    overrides if they want to apply standard semantics.
    """

    @staticmethod
    async def start(command: TaskCommand, task: Optional[TaskResult]) -> TaskResult:
        # Per spec: Start from non-existent creates a new task; if already exists, ignore
        # and return current snapshot (idempotent Start on same taskId)
        task_id = command.taskId
        if task:
            TaskManager.add_command_to_history(task_id, command)
            return task
        return TaskManager.create_task(command)

    @staticmethod
    async def get(command: TaskCommand, task: TaskResult) -> TaskResult:
        # Incremental filtering based on commandParams (optional)
        params = command.commandParams or {}
        last_command_sent_at = params.get("lastCommandSentAt")
        last_state_changed_at = params.get("lastStateChangedAt")

        filtered_task = task
        try:
            if last_command_sent_at and task.commandHistory:
                filtered_task = TaskResult(**filtered_task.model_dump())
                filtered_task.commandHistory = [
                    m for m in task.commandHistory if m.sentAt > last_command_sent_at
                ]
            if last_state_changed_at and task.statusHistory:
                if filtered_task is task:
                    filtered_task = TaskResult(**filtered_task.model_dump())
                filtered_task.statusHistory = [
                    s
                    for s in task.statusHistory
                    if s.stateChangedAt > last_state_changed_at
                ]
        except Exception:
            filtered_task = task
        return filtered_task

    @staticmethod
    async def cancel(command: TaskCommand, task: TaskResult) -> TaskResult:
        # Do not overwrite terminal states; make cancel idempotent.
        terminal_states = {
            TaskState.Completed,
            TaskState.Failed,
            TaskState.Rejected,
            TaskState.Canceled,
        }
        if task.status.state in terminal_states:
            return task
        TaskManager.add_command_to_history(task.taskId, command)
        return TaskManager.update_task_status(task.taskId, TaskState.Canceled)

    @staticmethod
    async def complete(command: TaskCommand, task: TaskResult) -> TaskResult:
        # Only effective when current state == AwaitingCompletion; otherwise ignore (no state change)
        if task.status.state == TaskState.AwaitingCompletion:
            TaskManager.add_command_to_history(task.taskId, command)
            return TaskManager.update_task_status(task.taskId, TaskState.Completed)
        TaskManager.add_command_to_history(task.taskId, command)
        return task

    @staticmethod
    async def continue_(command: TaskCommand, task: TaskResult) -> TaskResult:
        # Only effective when current state is AwaitingInput or AwaitingCompletion
        if task.status.state not in (
            TaskState.AwaitingInput,
            TaskState.AwaitingCompletion,
        ):
            TaskManager.add_command_to_history(task.taskId, command)
            return task
        # Require at least one non-empty Text data item; otherwise ignore
        try:
            has_text = False
            for di in command.dataItems or []:
                if isinstance(di, TextDataItem) and (di.text or "").strip():
                    has_text = True
                    break
            if not has_text:
                TaskManager.add_command_to_history(task.taskId, command)
                return task
        except Exception:
            TaskManager.add_command_to_history(task.taskId, command)
            return task
        # Fallthrough: by default, do not change state here. Agent on_continue should implement business logic.
        TaskManager.add_command_to_history(task.taskId, command)
        return task


class TaskManager:
    """
    A simple in-memory store and state machine for managing tasks.
    In a real application, this would be backed by a persistent database.
    """

    _tasks: Dict[str, TaskResult] = {}

    @classmethod
    def get_task(cls, task_id: str) -> TaskResult | None:
        return cls._tasks.get(task_id)

    @classmethod
    def create_task(
        cls,
        command: TaskCommand,
        initial_state: TaskState | None = None,
        data_items: list | None = None,
    ) -> TaskResult:
        if not command.taskId:
            raise ValueError("A command to start a task must have a taskId.")

        task_status = TaskStatus(
            state=initial_state or TaskState.Accepted,
            stateChangedAt=datetime.now(timezone.utc).isoformat(),
            dataItems=data_items or [],
        )
        task = TaskResult(
            id=f"result-{uuid.uuid4()}",
            sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="partner",
            senderId="server",  # Will be overridden by actual partner
            taskId=command.taskId,
            status=task_status,
            sessionId=command.sessionId,
            commandHistory=[command],
            statusHistory=[task_status],
        )
        cls._tasks[command.taskId] = task
        return task

    @classmethod
    def update_task_status(
        cls, task_id: str, new_state: TaskState, data_items: list | None = None
    ) -> TaskResult:
        task = cls.get_task(task_id)
        if not task:
            raise ValueError(f"Task with id {task_id} not found.")

        new_status = TaskStatus(
            state=new_state,
            stateChangedAt=datetime.now(timezone.utc).isoformat(),
            dataItems=data_items or [],
        )
        task.status = new_status
        if task.statusHistory:
            task.statusHistory.append(new_status)
        else:
            task.statusHistory = [new_status]

        return task

    @classmethod
    def add_command_to_history(cls, task_id: str, command: TaskCommand):
        task = cls.get_task(task_id)
        if task:
            if task.commandHistory:
                task.commandHistory.append(command)
            else:
                task.commandHistory = [command]

    @classmethod
    def set_products(cls, task_id: str, products: list[Product]):
        task = cls.get_task(task_id)
        if not task:
            return
        # Enforce maxProductsBytes if configured
        max_bytes = getattr(task, "_aip_max_products_bytes", None)
        if max_bytes is not None:
            try:
                total_bytes = 0
                for p in products:
                    for di in p.dataItems:
                        if isinstance(di, TextDataItem):
                            total_bytes += len(di.text.encode("utf-8"))
                        elif getattr(di, "bytes", None):
                            total_bytes += len(getattr(di, "bytes"))
                if total_bytes > max_bytes:
                    # Exceed limit -> fail task
                    fail_msg = TextDataItem(
                        text=f"Products size {total_bytes} bytes exceeds maxProductsBytes={max_bytes}."
                    )
                    cls.update_task_status(task_id, TaskState.Failed, [fail_msg])
                    return
            except Exception:
                # On error, do not block but record failure gracefully
                fail_msg = TextDataItem(text="Error calculating products size.")
                cls.update_task_status(task_id, TaskState.Failed, [fail_msg])
                return
        task.products = products


async def handle_rpc_request(request: Request, agent_handlers: CommandHandlers):
    """
    Generic handler for AIP RPC requests.
    It parses the request, validates it, and passes it to the agent-specific logic.
    """
    try:
        body = await request.json()
        rpc_request = RpcRequest.model_validate(body)
    except (ValidationError, ValueError) as e:
        error = JSONRPCError(code=-32700, message="Parse error", data=str(e))
        return RpcResponse(id=None, error=error)

    command = rpc_request.params.command
    task_id = command.taskId

    if not task_id and command.command != TaskCommandType.Start:
        error = JSONRPCError(
            code=-32602,
            message="Invalid params",
            data="taskId is required for non-Start commands.",
        )
        return RpcResponse(id=rpc_request.id, error=error)

    # --- Command Dispatch via pluggable handlers ---
    task = TaskManager.get_task(task_id) if task_id else None

    try:
        # Start can create a new task if missing
        if command.command == TaskCommandType.Start:
            if getattr(agent_handlers, "on_start", None):
                result = await agent_handlers.on_start(command, task)
            else:
                result = await DefaultHandlers.start(command, task)
            return RpcResponse(id=rpc_request.id, result=result)

        # Get requires existing task
        if command.command == TaskCommandType.Get:
            if not task:
                error = JSONRPCError(
                    code=-32001, message="Task not found", data={"taskId": task_id}
                )
                return RpcResponse(id=rpc_request.id, error=error)
            if getattr(agent_handlers, "on_get", None):
                result = await agent_handlers.on_get(command, task)
            else:
                result = await DefaultHandlers.get(command, task)
            return RpcResponse(id=rpc_request.id, result=result)

        # Other commands require existing task
        if not task:
            error = JSONRPCError(
                code=-32001, message="Task not found", data={"taskId": task_id}
            )
            return RpcResponse(id=rpc_request.id, error=error)

        if command.command == TaskCommandType.Cancel:
            if getattr(agent_handlers, "on_cancel", None):
                result = await agent_handlers.on_cancel(command, task)
            else:
                result = await DefaultHandlers.cancel(command, task)
            return RpcResponse(id=rpc_request.id, result=result)

        if command.command == TaskCommandType.Complete:
            if getattr(agent_handlers, "on_complete", None):
                result = await agent_handlers.on_complete(command, task)
            else:
                result = await DefaultHandlers.complete(command, task)
            return RpcResponse(id=rpc_request.id, result=result)

        if command.command == TaskCommandType.Continue:
            if getattr(agent_handlers, "on_continue", None):
                result = await agent_handlers.on_continue(command, task)
            else:
                result = await DefaultHandlers.continue_(command, task)
            return RpcResponse(id=rpc_request.id, result=result)

        # Unknown or missing command -> try catch-all handler if provided
        if getattr(agent_handlers, "on_message", None):
            result = await agent_handlers.on_message(command, task)
            return RpcResponse(id=rpc_request.id, result=result)

        # Default: respond with invalid params error
        error = JSONRPCError(
            code=-32602,
            message="Invalid params",
            data=f"Unknown or missing command: {command.command}",
        )
        return RpcResponse(id=rpc_request.id, error=error)

    except Exception as e:
        # If the agent logic fails, update the task state to 'failed'
        error_item = TextDataItem(text=f"Agent execution failed: {str(e)}")
        failed_task = TaskManager.update_task_status(
            task_id, TaskState.Failed, data_items=[error_item]
        )
        return RpcResponse(id=rpc_request.id, result=failed_task)


def add_aip_rpc_router(app: FastAPI, endpoint: str, agent_handlers: CommandHandlers):
    """
    Adds the AIP RPC endpoint to a FastAPI application.
    """

    @app.post(endpoint, response_model=RpcResponse)
    async def rpc_endpoint(request: Request):
        return await handle_rpc_request(request, agent_handlers)
