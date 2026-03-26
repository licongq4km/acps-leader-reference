"""
cancel_task.py — AIP cancel command

Cancels an active task. Valid from any non-terminal state:
accepted, working, awaiting-input, awaiting-completion.

Usage:
    python cancel_task.py --task_id <task_id> [--leader_aic <leader_aic>]
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.join(SCRIPT_DIR, "..")
STATE_TASKS_DIR = os.path.join(SKILL_ROOT, "state", "tasks")

sys.path.insert(0, os.path.join(SKILL_ROOT, "dependency"))

from acps_sdk.aip.aip_rpc_client import AipRpcClient  # noqa: E402
from acps_sdk.aip.aip_base_model import TaskState  # noqa: E402

TERMINAL_STATES = {
    TaskState.Completed.value,
    TaskState.Canceled.value,
    TaskState.Failed.value,
    TaskState.Rejected.value,
}


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


async def cancel_task(task_id: str, leader_aic: str) -> dict:
    cache = _load_task_cache(task_id)
    if cache is None:
        return {
            "success": False,
            "error": f"Task cache not found for task_id: {task_id}",
            "error_type": "cache_miss",
        }

    current_state = cache.get("state", "")
    if current_state in TERMINAL_STATES:
        return {
            "success": False,
            "error": (
                f"Task {task_id} is already in terminal state '{current_state}'. "
                f"Cannot cancel a terminated task."
            ),
            "error_type": "state_error",
            "task_id": task_id,
            "current_state": current_state,
        }

    partner_url = cache.get("partner_url", "")
    session_id = cache.get("session_id", "")

    client = AipRpcClient(partner_url=partner_url, leader_id=leader_aic)
    try:
        result = await client.cancel_task(task_id, session_id)
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
    parser = argparse.ArgumentParser(description="Cancel an active AIP task")
    parser.add_argument("--task_id", required=True, help="Task ID to cancel")
    parser.add_argument("--leader_aic", default="leader-acps-agent", help="Leader AIC identifier")
    args, _ = parser.parse_known_args()  # ignore extra args injected by run_python

    result = asyncio.run(cancel_task(args.task_id, args.leader_aic))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
