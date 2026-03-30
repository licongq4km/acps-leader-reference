"""
continue_task.py — AIP continue command

Resumes a task that is in the 'awaiting-input' or 'awaiting-completion' state
by supplying additional user input to the Partner.

Typical use case: the Partner asked a clarifying question (awaiting-input)
and the user has provided an answer.

Usage:
    python continue_task.py \\
        --task_id <task_id> \\
        --user_input "the user's response" \\
        [--leader_aic <leader_aic>]
"""

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.join(SCRIPT_DIR, "..")
STATE_TASKS_DIR = os.path.join(SKILL_ROOT, "state", "tasks")

from acps_sdk.aip.aip_rpc_client import AipRpcClient
from acps_sdk.aip.aip_base_model import TaskState

CONTINUABLE_STATES = {TaskState.AwaitingInput.value, TaskState.AwaitingCompletion.value}


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


async def continue_task(task_id: str, user_input: str, leader_aic: str) -> dict:
    cache = _load_task_cache(task_id)
    if cache is None:
        return {
            "success": False,
            "error": f"Task cache not found for task_id: {task_id}",
            "error_type": "cache_miss",
        }

    current_state = cache.get("state", "")
    if current_state not in CONTINUABLE_STATES:
        return {
            "success": False,
            "error": (
                f"Task {task_id} is in state '{current_state}', "
                f"which does not accept continue. "
                f"Continue is only valid from: {sorted(CONTINUABLE_STATES)}"
            ),
            "error_type": "state_error",
            "task_id": task_id,
            "current_state": current_state,
        }

    partner_url = cache.get("partner_url", "")
    session_id = cache.get("session_id", "")

    client = AipRpcClient(partner_url=partner_url, leader_id=leader_aic)
    try:
        result = await client.continue_task(task_id, session_id, user_input)
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "error_type": "http_error",
            "task_id": task_id,
        }
    finally:
        await client.close()

    state = result.status.state.value if hasattr(result.status.state, "value") else str(result.status.state)
    message = _extract_message(result)
    _update_task_cache(task_id, state, _task_result_to_dict(result))

    return {
        "success": True,
        "task_id": task_id,
        "state": state,
        "message": message,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Continue an AIP task with user input")
    parser.add_argument("--task_id", required=True, help="Task ID to continue")
    parser.add_argument("--user_input", required=True, help="User's response or additional information")
    parser.add_argument("--leader_aic", default="leader-acps-agent", help="Leader AIC identifier")
    args, _ = parser.parse_known_args()  # ignore extra args injected by run_python

    result = asyncio.run(continue_task(args.task_id, args.user_input, args.leader_aic))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
