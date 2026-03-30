"""
start_task.py — AIP start command

Starts a new task with the specified Partner agent (identified by AIC).
Reads the cached ACS to resolve the Partner RPC endpoint.
Caches initial task state to state/tasks/<task_id>.json.

Usage:
    python start_task.py \\
        --aic <partner_aic> \\
        --task_description "what the partner should do" \\
        --session_id <session_id> \\
        [--task_id <task_id>] \\
        [--leader_aic <leader_aic>]
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
SKILL_ROOT = os.path.join(SCRIPT_DIR, "..")
STATE_DISCOVERY_DIR = os.path.join(SKILL_ROOT, "state", "discovery")
STATE_TASKS_DIR = os.path.join(SKILL_ROOT, "state", "tasks")

from acps_sdk.aip.aip_rpc_client import AipRpcClient

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


def _read_dotenv_value(dotenv_path: str, key: str) -> str | None:
    """
    Read a single key from a .env file.
    - Ignores blank lines and comments
    - Supports simple KEY=VALUE (optionally quoted with ' or ")
    - Returns None if file/key not found or value is empty
    """
    try:
        if not os.path.exists(dotenv_path):
            return None
        with open(dotenv_path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() != key:
                    continue
                val = v.strip()
                if (len(val) >= 2) and ((val[0] == val[-1]) and val[0] in ("'", '"')):
                    val = val[1:-1]
                val = val.strip()
                return val or None
        return None
    except Exception:
        return None


def _get_leader_aic() -> str:
    """
    Compute leader_aic default.
    Priority:
    1) environment variable LEADER_AIC
    2) backend/.env -> LEADER_AIC
    3) fallback to "acps-leader-agent"
    """
    default_leader = "acps-leader-agent"
    try:
        leader_from_env = os.environ.get("LEADER_AIC")
        if leader_from_env:
            return leader_from_env

        backend_dir = os.path.abspath(os.path.join(SKILL_ROOT, "..", ".."))
        dotenv_path = os.path.join(backend_dir, ".env")
        leader_from_dotenv = _read_dotenv_value(dotenv_path, "LEADER_AIC")
        return leader_from_dotenv or default_leader
    except Exception:
        return default_leader


def _load_acs_cache(aic: str) -> dict | None:
    """Load the cached ACS document for the given AIC."""
    path = os.path.join(STATE_DISCOVERY_DIR, f"{aic}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_task_state(task_id: str, aic: str, partner_url: str, state: str,
                     last_result: dict, session_id: str) -> None:
    """Persist task lifecycle state to state/tasks/<task_id>.json."""
    os.makedirs(STATE_TASKS_DIR, exist_ok=True)
    path = os.path.join(STATE_TASKS_DIR, f"{task_id}.json")
    data = {
        "task_id": task_id,
        "aic": aic,
        "partner_url": partner_url,
        "session_id": session_id,
        "state": state,
        "last_result": last_result,
        "error_context": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _task_result_to_dict(result) -> dict:
    """Serialize a TaskResult object to a plain dict."""
    try:
        return result.model_dump(exclude_none=True)
    except Exception:
        return {}


def _extract_message(result) -> str:
    """Pull a human-readable message from the Partner's TaskResult."""
    try:
        items = result.status.dataItems or []
        for item in items:
            if hasattr(item, "text"):
                return item.text
    except Exception:
        pass
    return ""


def _extract_products(result) -> list:
    """Return a concise list of products from the Partner's TaskResult."""
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


async def start_task(aic: str, task_description: str, session_id: str,
                     task_id: str | None, leader_aic: str) -> dict:
    """Send a start command to the Partner and cache the result."""
    # Resolve endpoint from ACS cache
    cache = _load_acs_cache(aic)
    if cache is None:
        return {
            "success": False,
            "error": f"ACS cache not found for aic: {aic}. Run discover.py first.",
            "error_type": "cache_miss",
        }

    summary = cache.get("normalized_summary", {})
    partner_url = summary.get("endpoint_url", "")
    if not partner_url:
        return {
            "success": False,
            "error": f"No endpoint URL found in ACS cache for aic: {aic}",
            "error_type": "cache_miss",
        }

    if not task_id:
        task_id = f"task-{uuid.uuid4()}"

    ssl_ctx = _get_ssl_context() if partner_url.startswith("https") else None
    client = AipRpcClient(partner_url=partner_url, leader_id=leader_aic, ssl_context=ssl_ctx)
    try:
        result = await client.start_task(session_id, task_description, task_id=task_id)
    except Exception as e:
        error_msg = str(e) or repr(e)
        _save_task_state(task_id, aic, partner_url, "error",
                         {"error": error_msg}, session_id)
        return {
            "success": False,
            "error": error_msg,
            "error_type": "http_error",
            "hint": "The partner agent could not be reached. Do NOT call get_task "
                    "with a task_id from a failed start_task.",
        }
    finally:
        await client.close()

    TERMINAL_STATES = {"completed", "canceled", "failed", "rejected"}

    state = result.status.state.value if hasattr(result.status.state, "value") else str(result.status.state)
    message = _extract_message(result)
    products = _extract_products(result)
    _save_task_state(task_id, aic, partner_url, state,
                     _task_result_to_dict(result), session_id)

    return {
        "success": True,
        "task_id": task_id,
        "state": state,
        "message": message,
        "products": products,
        "is_terminal": state in TERMINAL_STATES,
        "needs_input": state == "awaiting-input",
        "awaiting_completion": state == "awaiting-completion",
        "aic": aic,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Start an AIP task with a Partner agent")
    parser.add_argument("--aic", required=True, help="Partner agent AIC")
    parser.add_argument("--task_description", required=True, help="Task description for the Partner")
    parser.add_argument("--session_id", required=True, help="Session ID")
    parser.add_argument("--task_id", default=None, help="Optional task ID (auto-generated if omitted)")
    parser.add_argument("--leader_aic", default=_get_leader_aic(), help="Leader AIC identifier")
    args, _ = parser.parse_known_args() 

    result = asyncio.run(
        start_task(args.aic, args.task_description, args.session_id,
                   args.task_id, args.leader_aic)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
