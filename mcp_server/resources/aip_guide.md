# AIP Protocol Guide — Agent Interaction Protocol

## 1. What is AIP?

AIP (Agent Interaction Protocol) is the ACPs protocol for agent-to-agent task delegation. It defines how a Leader agent sends tasks to Partner agents, tracks execution state, handles mid-task input, confirms results, and terminates tasks.

AIP operates over **JSON-RPC 2.0** via HTTP POST. All interactions are structured as `TaskCommand` → `TaskResult` exchanges.

## 2. Roles

| Role | Description |
|---|---|
| **Leader** | The agent that creates, controls, and terminates tasks. Exactly one Leader per session. |
| **Partner** | The agent that accepts tasks, executes them, and returns results. Multiple Partners allowed. |

The agent using this skill acts as the Leader. Any agent discovered via `discover.py` (and listed in `state/discovery/`) is a Partner.

## 3. Task State Machine

**State transitions** (format: `CURRENT_STATE + command/event → NEXT_STATE`):

```
(none)               + start     → accepted        # task created, not yet executing
(none)               + start     → rejected        # partner refused; terminal
accepted             + (auto)    → working         # partner began execution
working              + (auto)    → awaiting-input  # partner needs more info from leader
working              + (auto)    → awaiting-completion  # partner produced results
working              + (auto)    → failed          # unrecoverable error; terminal
working              + cancel    → canceled        # leader aborted; terminal
awaiting-input       + continue  → working         # leader supplied input; execution resumes
awaiting-input       + cancel    → canceled        # leader aborted; terminal
awaiting-completion  + continue  → working         # leader acknowledged; execution resumes
awaiting-completion  + complete  → completed       # leader accepted results; terminal
awaiting-completion  + cancel    → canceled        # leader rejected results; terminal
accepted             + cancel    → canceled        # leader aborted before execution; terminal
```

**Terminal states** (no further commands accepted): `completed`, `canceled`, `failed`, `rejected`

**Non-terminal states** (task still active): `accepted`, `working`, `awaiting-input`, `awaiting-completion`

**Human-in-the-loop required**:
- `awaiting-input` → read question from `result.status.dataItems`, ask user, then call `continue`
- `awaiting-completion` → read products, present to user, then call `complete` (accept) or `cancel` (reject)

| State | Description |
|---|---|
| `accepted` | Partner accepted the task; execution not yet started |
| `working` | Task is actively being executed |
| `awaiting-input` | Partner needs more information from the Leader/user to continue |
| `awaiting-completion` | Partner has produced results; waiting for Leader to confirm |
| `completed` | Task finished successfully (terminal) |
| `canceled` | Task was canceled by the Leader (terminal) |
| `failed` | Task failed during execution (terminal) |
| `rejected` | Partner refused to accept the task (terminal) |

## 4. Command Types

| Command | Triggered by | Valid from states | Description |
|---|---|---|---|
| `start` | Leader | — (new task) | Create a new task with an initial user message |
| `get` | Leader | any | Poll the current task state without changing it |
| `continue` | Leader | `awaiting-input`, `awaiting-completion` | Supply additional input or acknowledge; resumes to `working` |
| `complete` | Leader | `awaiting-completion` | Confirm and accept the Partner's products; transitions to `completed` |
| `cancel` | Leader | `accepted`, `working`, `awaiting-input`, `awaiting-completion` | Abort the task; transitions to `canceled` |

## 5. RPC Request / Response Format

All commands are sent as a `POST` to the Partner's `endPoints[].url` with `Content-Type: application/json`.

### Request structure (JSON-RPC 2.0)

```json
{
  "jsonrpc": "2.0",
  "method": "rpc",
  "id": "<uuid>",
  "params": {
    "command": {
      "type": "task-command",
      "id": "cmd-<uuid>",
      "sentAt": "2026-01-01T00:00:00+00:00",
      "senderRole": "leader",
      "senderId": "<leader_aic>",
      "command": "start",
      "taskId": "task-<uuid>",
      "sessionId": "<session_id>",
      "dataItems": [
        { "type": "text", "text": "user task description" }
      ]
    }
  }
}
```

### Response structure

```json
{
  "jsonrpc": "2.0",
  "id": "<same uuid as request>",
  "result": {
    "type": "task-result",
    "id": "msg-<uuid>",
    "sentAt": "2026-01-01T00:00:01+00:00",
    "senderRole": "partner",
    "senderId": "<partner_aic>",
    "taskId": "task-<uuid>",
    "sessionId": "<session_id>",
    "status": {
      "state": "awaiting-completion",
      "stateChangedAt": "2026-01-01T00:00:01+00:00"
    },
    "products": [
      {
        "id": "product-<uuid>",
        "name": "Result",
        "dataItems": [{ "type": "text", "text": "the answer" }]
      }
    ]
  }
}
```

If the response contains an `"error"` field instead of `"result"`, it is a protocol-level failure (see `error_handling_guide.md`).

## 6. AipRpcClient Methods (SDK)

The SDK client at `acps_sdk/aip/aip_rpc_client.py` wraps all RPC calls:

```python
client = AipRpcClient(partner_url="http://...", leader_id="<leader_aic>")

result = await client.start_task(session_id, user_input, task_id=None)
result = await client.get_task(task_id, session_id)
result = await client.continue_task(task_id, session_id, user_input)
result = await client.complete_task(task_id, session_id)
result = await client.cancel_task(task_id, session_id)
await client.close()
```

All methods return a `TaskResult` object. Key fields:
- `result.taskId` — the task ID
- `result.status.state` — current `TaskState` enum value
- `result.status.dataItems` — optional messages from the Partner (e.g. questions)
- `result.products` — list of `Product` objects when state is `awaiting-completion` or `completed`

## 7. Human-in-the-Loop Trigger Points

There are two states where the Leader **must pause and involve the human user**:

### `awaiting-input`
The Partner has a question or needs clarification before it can continue.

- Read the question from `result.status.dataItems[0].text`
- Present it to the user in natural language
- Wait for the user's reply
- Call `continue_task.py` with the user's response

### `awaiting-completion`
The Partner has finished and produced results. The Leader must decide whether to accept or reject.

- Extract content from `result.products[].dataItems`
- Present the results to the user
- Ask the user: "Accept results?" or "Cancel?"
- If accepted → call `complete_task.py`
- If rejected → call `cancel_task.py`

## 8. Polling Pattern

Because Partner execution is asynchronous, the Leader polls using `get_task.py` until the task reaches a non-`working` state:

```
start_task → state = accepted/working
  loop:
    sleep 2s
    get_task → check state
    if state == awaiting-input  → ask user, then continue_task
    if state == awaiting-completion → show results, ask user, then complete/cancel
    if state in {completed, failed, canceled, rejected} → done
```

Stop polling when the state is terminal: `completed`, `failed`, `canceled`, or `rejected`.

## 9. Products

When state is `awaiting-completion` or `completed`, the `TaskResult.products` field contains the Partner's deliverables:

```json
"products": [
  {
    "id": "product-001",
    "name": "Recommendation",
    "dataItems": [
      { "type": "text", "text": "Here is the answer..." }
    ]
  }
]
```

Extract and present text content to the user. After confirming, call `complete_task.py`.
