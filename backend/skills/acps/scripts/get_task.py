"""
get_task.py — AIP get (poll) command

Queries the current state of an existing task from the Partner.
When --poll is enabled (default), keeps polling until the task leaves the
"working" / "accepted" state (i.e. reaches awaiting-input, awaiting-completion,
or a terminal state). The polling loop runs entirely inside this script so the
agent only needs to call get_task once per wait cycle.

Usage:
    python get_task.py --task_id <task_id> [--leader_aic <leader_aic>]
                       [--poll true|false] [--poll_interval <seconds>]
                       [--poll_timeout <seconds>]
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
SKILL_ROOT = os.path.join(SCRIPT_DIR, "..")
STATE_TASKS_DIR = os.path.join(SKILL_ROOT, "state", "tasks")

from acps_sdk.aip.aip_rpc_client import AipRpcClient
from acps_sdk.aip.aip_base_model import TaskState

try:
    from mtls import get_client_ssl_context as _build_ssl_ctx
except ImportError:
    _build_ssl_ctx = None


def _get_ssl_context():
    """Build mTLS client context; returns None if not configured (falls back to HTTP)."""
    if _build_ssl_ctx is None:
        return None
    backend_dir = os.path.abspath(os.path.join(SKILL_ROOT, "..", ".."))
    return _build_ssl_ctx(backend_dir)


AWAITING_INPUT_STATES = {TaskState.AwaitingInput.value}
TERMINAL_STATES = {
    TaskState.Completed.value,
    TaskState.Canceled.value,
    TaskState.Failed.value,
    TaskState.Rejected.value,
}
# States that mean "still running, keep polling"
WORKING_STATES = {"working", "accepted"}


def _load_task_cache(task_id: str) -> dict | None:
    path = os.path.join(STATE_TASKS_DIR, f"{task_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _update_task_cache(task_id: str, state: str, last_result: dict) -> None:
    path = os.path.join(STATE_TASKS_DIR, f"{task_id}.json")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["state"] = state
    data["last_result"] = last_result
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _task_result_to_dict(result) -> dict:
    try:
        return result.model_dump(exclude_none=True)
    except Exception:
        return {}


def _extract_message(result) -> str:
    try:
        items = result.status.dataItems or []
        for item in items:
            if hasattr(item, "text"):
                return item.text
    except Exception:
        pass
    return ""


def _extract_products_summary(result) -> list:
    """Return a concise summary of products without deep nesting."""
    products = []
    try:
        for product in result.products or []:
            texts = []
            for item in product.dataItems or []:
                if hasattr(item, "text"):
                    texts.append(item.text)
            products.append({
                "id": product.id,
                "name": product.name or "",
                "content": " ".join(texts),
            })
    except Exception:
        pass
    return products


async def _query_once(task_id: str, partner_url: str, session_id: str,
                      leader_aic: str, ssl_ctx=None) -> dict:
    """Send a single Get RPC to the partner and return a normalised result dict."""
    client = AipRpcClient(partner_url=partner_url, leader_id=leader_aic, ssl_context=ssl_ctx)
    try:
        result = await client.get_task(task_id, session_id)
    except Exception as e:
        return {
            "success": False,
            "error": str(e) or repr(e),
            "error_type": "http_error",
            "task_id": task_id,
        }
    finally:
        await client.close()

    state = (
        result.status.state.value
        if hasattr(result.status.state, "value")
        else str(result.status.state)
    )
    _update_task_cache(task_id, state, _task_result_to_dict(result))
    return {
        "success": True,
        "task_id": task_id,
        "state": state,
        "message": _extract_message(result),
        "needs_input": state == TaskState.AwaitingInput.value,
        "awaiting_completion": state == TaskState.AwaitingCompletion.value,
        "is_terminal": state in TERMINAL_STATES,
        "products": _extract_products_summary(result),
    }


async def get_task(
    task_id: str,
    leader_aic: str,
    poll: bool = True,
    poll_interval: float = 5.0,
    poll_timeout: float = 600.0,
) -> dict:
    """
    Query (and optionally poll) the state of a task.

    When poll=True the function keeps calling _query_once every poll_interval
    seconds until the task leaves WORKING_STATES, then returns. The agent only
    needs to call this script once per wait cycle — no LLM inference occurs
    between polls.
    """
    cache = _load_task_cache(task_id)
    if cache is None:
        return {
            "success": False,
            "error": f"Task cache not found for task_id: {task_id}",
            "error_type": "cache_miss",
        }

    cached_state = cache.get("state", "")

    # start_task failed — partner never saw this task
    if cached_state == "error":
        last_result = cache.get("last_result", {})
        return {
            "success": False,
            "error": last_result.get("error", "Task failed to start."),
            "error_type": "task_start_failed",
            "task_id": task_id,
            "state": "error",
            "hint": "The partner agent was unreachable when start_task was called. "
                    "Try calling start_task again.",
        }

    # Already terminal locally — no need to hit the partner
    if cached_state in TERMINAL_STATES:
        return {
            "success": True,
            "task_id": task_id,
            "state": cached_state,
            "message": "(restored from local cache)",
            "needs_input": False,
            "awaiting_completion": False,
            "is_terminal": True,
            "products": [],
            "from_cache": True,
        }

    partner_url = cache.get("partner_url", "")
    session_id = cache.get("session_id", "")
    ssl_ctx = _get_ssl_context() if partner_url.startswith("https") else None

    deadline = time.monotonic() + poll_timeout
    attempts = 0

    while True:
        attempts += 1
        res = await _query_once(task_id, partner_url, session_id, leader_aic, ssl_ctx)

        if not res["success"]:
            return res  # network error — surface immediately

        state = res["state"]

        # Stop polling when the task has left the working states
        if not poll or state not in WORKING_STATES:
            res["poll_attempts"] = attempts
            return res

        # Timed out
        if time.monotonic() >= deadline:
            return {
                "success": False,
                "error": (
                    f"Polling timed out after {poll_timeout:.0f}s "
                    f"({attempts} attempts). Last state: {state}"
                ),
                "error_type": "poll_timeout",
                "task_id": task_id,
                "state": state,
                "poll_attempts": attempts,
                "hint": "The partner agent is still working. Call get_task again to resume polling.",
            }

        await asyncio.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Query (and poll) an AIP task state")
    parser.add_argument("--task_id", required=True, help="Task ID to query")
    parser.add_argument("--leader_aic", default="leader-acps-agent", help="Leader AIC identifier")
    parser.add_argument("--poll", default="true",
                        help="Enable auto-polling until task leaves working state (default: true)")
    parser.add_argument("--poll_interval", type=float, default=5.0,
                        help="Seconds between polls (default: 5)")
    parser.add_argument("--poll_timeout", type=float, default=600.0,
                        help="Max total seconds to wait (default: 600)")
    args, _ = parser.parse_known_args()  # ignore extra args injected by run_python

    result = asyncio.run(
        get_task(
            args.task_id,
            args.leader_aic,
            poll=args.poll.lower() not in {"false", "0", "no"},
            poll_interval=args.poll_interval,
            poll_timeout=args.poll_timeout,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
