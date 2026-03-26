# Error Handling Guide — ACPS Skill

## Overview

Errors in the ACPS skill fall into four layers:
1. **Discovery errors** — problems reaching the discovery server or parsing its response
2. **HTTP errors** — network-level failures when calling the Partner RPC endpoint
3. **RPC protocol errors** — JSON-RPC error objects returned by the Partner
4. **Task state errors** — the task reached a terminal failure state (`failed`, `rejected`)

All scripts output a JSON object to stdout. On error, the output is:
```json
{
  "success": false,
  "error": "short error description",
  "error_type": "one of: discovery_error | http_error | rpc_error | task_error | state_error | cache_miss"
}
```

---

## 1. Discovery Errors

### 1.1 Cannot reach discovery server
- **Symptom**: `httpx.ConnectError`, `httpx.TimeoutException`, or HTTP 5xx from discovery URL
- **Action**: Retry up to 2 times with a 3-second interval. If still failing, report to user that the discovery service is unreachable and suggest checking `state/discovery/discovery_config.yaml` for a correct URL.
- **Output**: `"error_type": "discovery_error"`

### 1.2 No agents found
- **Symptom**: `result.agents` is empty or `acsMap` is empty
- **Action**: Report to user that no matching agents were found for the query. Suggest rephrasing the query or trying a different keyword.
- **Output**: `"error_type": "discovery_error"`, `"error": "No agents found for query: ..."`

### 1.3 Config file missing or malformed
- **Symptom**: `state/discovery/discovery_config.yaml` does not exist or is invalid YAML
- **Action**: Fall back to the hardcoded default URL `https://ioa.pub/discovery/acps-adp-v2/discover`. Log a warning. Do not crash.

---

## 2. HTTP Errors (Partner RPC)

| Status | Meaning | Action |
|---|---|---|
| 400 | Bad Request | The request was malformed. Do not retry. Check script parameters and SDK version. |
| 401 / 403 | Unauthorized / Forbidden | Authentication failure (e.g. mTLS not configured). Report to user. |
| 404 | Not Found | The Partner RPC endpoint URL is wrong. Check `endpoint_url` in the cached ACS. |
| 408 / 504 | Timeout | Retry up to 2 times with 3-second interval. If still failing, report timeout. |
| 500 | Internal Server Error | Partner-side error. Retry once. If still failing, report that the Partner is unavailable. |
| 503 | Service Unavailable | Partner is temporarily down. Retry once after 5 seconds. |

**Retry rule**: Maximum 2 retries, 3-second interval. Never retry 4xx errors (except 408).

---

## 3. RPC Protocol Errors (JSON-RPC error object)

When the Partner returns a response with an `"error"` field instead of `"result"`:

```json
{
  "jsonrpc": "2.0",
  "id": "...",
  "error": {
    "code": -32001,
    "message": "TaskNotFoundError"
  }
}
```

| Error Code | Name | Cause | Action |
|---|---|---|---|
| -32700 | Parse error | Malformed JSON sent | Check that SDK serialization is correct |
| -32600 | Invalid Request | Wrong JSON-RPC structure | SDK issue; report bug |
| -32601 | Method not found | Partner does not support `"rpc"` method | Partner incompatible; try a different agent |
| -32602 | Invalid params | `TaskCommand` missing required fields | Check `aic`, `task_id`, `session_id` are all set |
| -32603 | Internal error | Partner-side unhandled exception | Retry once; if still failing, abandon and report |
| -32001 | TaskNotFoundError | `task_id` not found on Partner | The task may have expired; start a new task |
| -32002 | TaskNotCancelableError | Task is already in a terminal state | No action needed; task is done |
| -32007 | GroupNotSupportedError | Partner requires group mode | This skill uses direct RPC; use a different agent |

---

## 4. Task State Errors

### 4.1 State `failed`
- **Symptom**: `result.status.state == "failed"`
- **Read**: `result.status.dataItems[0].text` for the failure message
- **Action**: Report failure reason to user. Ask if they want to retry (start a new task) or abandon.
- **Output**: `"error_type": "task_error"`, `"error": "<failure message>"`

### 4.2 State `rejected`
- **Symptom**: `result.status.state == "rejected"` (returned immediately after `start`)
- **Read**: `result.status.dataItems[0].text` for the rejection reason
- **Action**: The Partner declined the task (e.g. out of scope). Report reason to user. Suggest discovering a different agent.
- **Output**: `"error_type": "task_error"`, `"error": "<rejection reason>"`

---

## 5. State Machine Violations

Calling a command when the task is in the wrong state will either be silently ignored by the Partner or return an error.

| Command | Allowed states | If called from wrong state |
|---|---|---|
| `start` | (no prior task) | Creates a duplicate task |
| `get` | any | Always safe |
| `continue` | `awaiting-input`, `awaiting-completion` | May be ignored by Partner |
| `complete` | `awaiting-completion` | May return `TaskNotCancelableError` |
| `cancel` | `accepted`, `working`, `awaiting-input`, `awaiting-completion` | Returns `TaskNotCancelableError` if terminal |

**Before calling any command, check the cached state in `state/tasks/<task_id>.json`.** If the cached state is terminal (`completed`, `canceled`, `failed`, `rejected`), do not send any further commands.

---

## 6. ACS Cache Miss

- **Symptom**: `state/discovery/<aic>.json` does not exist when `start_task.py` is called
- **Cause**: Discovery was not run, or a different AIC was specified
- **Action**: Output `"error_type": "cache_miss"`, `"error": "ACS cache not found for aic: ..."`. Tell the user to run `discover.py` first.

---

## 7. Task Cache Miss

- **Symptom**: `state/tasks/<task_id>.json` does not exist when `get_task.py`, `continue_task.py`, `complete_task.py`, or `cancel_task.py` is called
- **Cause**: Wrong `task_id` provided
- **Action**: Output `"error_type": "cache_miss"`, `"error": "Task cache not found for task_id: ..."`. Ask the user to verify the task ID (it should have been returned by `start_task.py`).

---

## 8. Quick Decision Tree

```
Script fails
  ├─ Discovery script
  │    ├─ Network error → retry 2x → report unreachable
  │    └─ No results → suggest new query
  │
  ├─ AIP script (start/get/continue/complete/cancel)
  │    ├─ HTTP error
  │    │    ├─ 4xx (not 408) → don't retry, report error
  │    │    └─ 5xx / 408    → retry 2x then report
  │    ├─ RPC error object
  │    │    ├─ -32001 (TaskNotFound) → start new task
  │    │    ├─ -32002 (NotCancelable) → task already terminal, ignore
  │    │    └─ other → report error code and message
  │    └─ Task state terminal (failed/rejected)
  │         └─ extract message, report to user, ask what to do next
  │
  └─ Cache miss → run discover.py first, or check task_id
```
