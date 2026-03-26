import httpx
import uuid
import ssl
from datetime import datetime, timezone
from typing import Optional

from .aip_base_model import TaskCommand, TaskResult, TaskCommandType, TextDataItem
from .aip_rpc_model import RpcRequest, RpcRequestParams, RpcResponse


class AipRpcClient:
    """
    A client for interacting with an AIP-compliant Partner over RPC.
    Supports both HTTP and HTTPS with mTLS.
    """

    def __init__(
        self,
        partner_url: str,
        leader_id: str,
        ssl_context: Optional[ssl.SSLContext] = None,
    ):
        """
        初始化AIP RPC客户端

        Args:
            partner_url: Partner的RPC端点URL
            leader_id: Leader Agent的ID
            ssl_context: 可选的SSL上下文，用于mTLS连接
        """
        self.partner_url = partner_url
        self.leader_id = leader_id

        # 如果提供了SSL上下文，创建支持mTLS的HTTP客户端
        if ssl_context:
            self.http_client = httpx.AsyncClient(verify=ssl_context)
        else:
            self.http_client = httpx.AsyncClient()

    async def _send_request(self, command: TaskCommand) -> RpcResponse:
        """
        Sends a task command to the Partner and returns the response.
        """
        request_id = str(uuid.uuid4())
        rpc_request = RpcRequest(
            id=request_id,
            params=RpcRequestParams(command=command),
        )

        try:
            response = await self.http_client.post(
                self.partner_url,
                json=rpc_request.model_dump(exclude_none=True),
                headers={"Content-Type": "application/json"},
                timeout=300.0, 
            )
            response.raise_for_status()

            # The response from the server should be a RpcResponse
            # Pydantic will automatically handle the validation and parsing
            # of the nested TaskResult object in the 'result' field.
            rpc_response = RpcResponse.model_validate(response.json())

            if rpc_response.error:
                raise Exception(
                    f"RPC Error: {rpc_response.error.code} - {rpc_response.error.message}"
                )

            if rpc_response.id != request_id:
                raise Exception("RPC Error: Response ID does not match request ID.")

            return rpc_response

        except httpx.HTTPStatusError as e:
            raise Exception(
                f"HTTP Error: {e.response.status_code} - {e.response.text}"
            ) from e
        except Exception as e:
            # Re-raise other exceptions like connection errors or parsing errors
            raise e

    def _create_task_command(
        self,
        command: TaskCommandType,
        task_id: str,
        session_id: str,
        text_content: str | None = None,
    ) -> TaskCommand:
        """
        Helper to create a new TaskCommand object.
        """
        data_items = []
        if text_content:
            data_items.append(TextDataItem(text=text_content))

        return TaskCommand(
            id=f"cmd-{uuid.uuid4()}",
            sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="leader",
            senderId=self.leader_id,
            dataItems=data_items if data_items else None,
            sessionId=session_id,
            command=command,
            taskId=task_id,
        )

    async def start_task(
        self, session_id: str, user_input: str, task_id: Optional[str] = None
    ) -> TaskResult:
        """
        Starts a new task with the Partner.
        """
        if not task_id:
            task_id = f"task-{uuid.uuid4()}"
        command = self._create_task_command(
            command=TaskCommandType.Start,
            task_id=task_id,
            session_id=session_id,
            text_content=user_input,
        )

        response = await self._send_request(command)
        if isinstance(response.result, TaskResult):
            return response.result
        raise TypeError(f"Expected TaskResult, got {type(response.result)}")

    async def continue_task(
        self, task_id: str, session_id: str, user_input: str
    ) -> TaskResult:
        """
        Continues a task that is in a waiting state.
        """
        command = self._create_task_command(
            command=TaskCommandType.Continue,
            task_id=task_id,
            session_id=session_id,
            text_content=user_input,
        )
        response = await self._send_request(command)
        if isinstance(response.result, TaskResult):
            return response.result
        raise TypeError(f"Expected TaskResult, got {type(response.result)}")

    async def complete_task(self, task_id: str, session_id: str) -> TaskResult:
        """
        Marks a task as completed.
        """
        command = self._create_task_command(
            command=TaskCommandType.Complete,
            task_id=task_id,
            session_id=session_id,
        )
        response = await self._send_request(command)
        if isinstance(response.result, TaskResult):
            return response.result
        raise TypeError(f"Expected TaskResult, got {type(response.result)}")

    async def cancel_task(self, task_id: str, session_id: str) -> TaskResult:
        """
        Cancels a task that is in a non-terminal state.
        """
        command = self._create_task_command(
            command=TaskCommandType.Cancel,
            task_id=task_id,
            session_id=session_id,
        )
        response = await self._send_request(command)
        if isinstance(response.result, TaskResult):
            return response.result
        raise TypeError(f"Expected TaskResult, got {type(response.result)}")

    async def get_task(self, task_id: str, session_id: str) -> TaskResult:
        """
        Retrieves the current state of a task.
        """
        command = self._create_task_command(
            command=TaskCommandType.Get,
            task_id=task_id,
            session_id=session_id,
        )
        response = await self._send_request(command)
        if isinstance(response.result, TaskResult):
            return response.result
        raise TypeError(f"Expected TaskResult, got {type(response.result)}")

    async def close(self):
        """
        Closes the underlying HTTP client.
        """
        await self.http_client.aclose()
