"""
ACPS MCP Server — Exposes ACPS protocol operations as MCP tools.

Any MCP-capable agent can connect to this server and gain the ability to
discover Partner agents (ADP) and manage task lifecycles (AIP).

Transport : SSE (default port 7004)
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# SDK path — local copy under mcp_server/acps_sdk
# ---------------------------------------------------------------------------
SERVER_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SERVER_DIR))

from acps_sdk.aip.aip_rpc_client import AipRpcClient  # noqa: E402
from acps_sdk.aip.aip_base_model import TaskState  # noqa: E402

# ---------------------------------------------------------------------------
# State directories (independent from the skill-based agent)
# ---------------------------------------------------------------------------
STATE_DIR = SERVER_DIR / "state"
DISCOVERY_DIR = STATE_DIR / "discovery"
TASKS_DIR = STATE_DIR / "tasks"
CONFIG_FILE = STATE_DIR / "config" / "config.yaml"

DISCOVERY_DIR.mkdir(parents=True, exist_ok=True)
TASKS_DIR.mkdir(parents=True, exist_ok=True)

LEADER_AIC = os.getenv("LEADER_AIC", "leader-acps-agent")

TERMINAL_STATES = {
    TaskState.Completed.value,
    TaskState.Canceled.value,
    TaskState.Failed.value,
    TaskState.Rejected.value,
}
WORKING_STATES = {"working", "accepted"}

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
_port = int(os.getenv("MCP_SERVER_PORT", "7004"))

mcp = FastMCP(
    "ACPS Protocol Server",
    instructions=(
        "This MCP server provides tools for the ACPS (Agent Communication Protocol Suite). "
        "Use 'discover' to find Partner agents, then 'start_task' to delegate work. "
        "Poll with 'get_task', interact via 'continue_task', and finalize with "
        "'complete_task' or 'cancel_task'. Task lifecycle: "
        "discover → start → poll(get) → [continue if awaiting-input] → complete/cancel."
    ),
    host="0.0.0.0",
    port=_port,
)

# ========================== MCP Resources ==========================

RESOURCES_DIR = SERVER_DIR / "resources"


@mcp.resource("acps://guides/adp")
def adp_guide() -> str:
    """ADP (Agent Discovery Protocol) guide — how discovery works, request/response format, ACS structure."""
    return (RESOURCES_DIR / "adp_guide.md").read_text(encoding="utf-8")


@mcp.resource("acps://guides/aip")
def aip_guide() -> str:
    """AIP (Agent Interaction Protocol) guide — task state machine, command types, RPC format, polling."""
    return (RESOURCES_DIR / "aip_guide.md").read_text(encoding="utf-8")


@mcp.resource("acps://guides/error-handling")
def error_handling_guide() -> str:
    """Error handling guide — error types, retry rules, decision tree for all ACPS operations."""
    return (RESOURCES_DIR / "error_handling_guide.md").read_text(encoding="utf-8")


# ========================== Helper functions ==========================

def _load_discovery_url() -> str:
    default_url = "https://ioa.pub/discovery/acps-adp-v2/discover"
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        custom = (cfg.get("custom_discovery_url") or "").strip()
        return custom if custom else cfg.get("default_discovery_url") or default_url
    except (FileNotFoundError, yaml.YAMLError):
        return default_url


def _extract_endpoint_url(acs: dict) -> str:
    for ep in acs.get("endPoints") or []:
        protocol = (ep.get("protocol") or "").lower()
        if "aip" in protocol or "rpc" in protocol:
            url = ep.get("url", "")
            if url:
                return url
    endpoints = acs.get("endPoints") or []
    return endpoints[0].get("url", "") if endpoints else ""


def _build_skills_summary(acs: dict) -> str:
    parts = []
    for skill in acs.get("skills") or []:
        name = skill.get("name", "")
        desc = skill.get("description", "")
        if name or desc:
            parts.append(f"{name}: {desc}".strip(": "))
    return " | ".join(parts)


def _build_normalized_summary(acs: dict, ranking: int) -> dict:
    return {
        "aic": acs.get("aic", ""),
        "name": acs.get("name", ""),
        "description": acs.get("description", ""),
        "active": acs.get("active", True),
        "skills_summary": _build_skills_summary(acs),
        "endpoint_url": _extract_endpoint_url(acs),
        "protocol_version": acs.get("protocolVersion", ""),
        "ranking": ranking,
    }


def _cache_acs(acs: dict, ranking: int, source_url: str) -> None:
    aic = acs.get("aic", "unknown")
    cache_path = DISCOVERY_DIR / f"{aic}.json"
    payload = {
        "raw_payload": acs,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "source": source_url,
        "normalized_summary": _build_normalized_summary(acs, ranking),
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_acs_cache(aic: str) -> dict | None:
    path = DISCOVERY_DIR / f"{aic}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_task_cache(task_id: str) -> dict | None:
    path = TASKS_DIR / f"{task_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_task_state(task_id: str, aic: str, partner_url: str,
                     state: str, last_result: dict, session_id: str) -> None:
    path = TASKS_DIR / f"{task_id}.json"
    data = {
        "task_id": task_id, "aic": aic, "partner_url": partner_url,
        "session_id": session_id, "state": state, "last_result": last_result,
        "error_context": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_task_cache(task_id: str, state: str, last_result: dict) -> None:
    path = TASKS_DIR / f"{task_id}.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["state"] = state
    data["last_result"] = last_result
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_to_dict(result) -> dict:
    try:
        return result.model_dump(exclude_none=True)
    except Exception:
        return {}


def _extract_message(result) -> str:
    try:
        for item in result.status.dataItems or []:
            if hasattr(item, "text"):
                return item.text
    except Exception:
        pass
    return ""


def _extract_products(result) -> list:
    products = []
    try:
        for product in result.products or []:
            texts = [item.text for item in product.dataItems or [] if hasattr(item, "text")]
            products.append({"id": product.id, "name": product.name or "", "content": " ".join(texts)})
    except Exception:
        pass
    return products


def _get_state_str(result) -> str:
    return result.status.state.value if hasattr(result.status.state, "value") else str(result.status.state)


# ========================== MCP Tools ==========================

@mcp.tool()
async def discover(query: str, limit: int = 5) -> str:
    """Find Partner agents matching a capability description via ADP discovery.

    Args:
        query: Natural-language description of the needed capability (e.g. "poetry generation")
        limit: Maximum number of results to return (default 5)

    Returns:
        JSON with discovered agents' name, description, skills_summary, and aic for use in start_task.
    """
    discovery_url = _load_discovery_url()
    last_error = None

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(verify=False, timeout=120.0) as client:
                resp = await client.post(
                    discovery_url,
                    json={"query": query, "limit": limit, "type": "explicit"},
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                break
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            last_error = str(e)
            if attempt < 2:
                await asyncio.sleep(3)
            continue
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < 2:
                last_error = f"HTTP {e.response.status_code}"
                await asyncio.sleep(3)
                continue
            return json.dumps({"success": False, "error": f"HTTP {e.response.status_code}", "error_type": "discovery_error"})
    else:
        return json.dumps({"success": False, "error": f"Discovery unreachable: {last_error}", "error_type": "discovery_error"})

    data = resp.json()
    result_body = data.get("result") or {}
    acs_map = result_body.get("acsMap") or {}

    agent_skills = []
    for group in result_body.get("agents") or []:
        agent_skills.extend(group.get("agentSkills") or [])
    agent_skills.sort(key=lambda s: s.get("ranking", 999))

    if not agent_skills:
        return json.dumps({"success": False, "error": f"No agents found for: {query}", "error_type": "discovery_error"})

    summaries = []
    for entry in agent_skills:
        aic = entry.get("aic", "")
        ranking = entry.get("ranking", 99)
        acs = acs_map.get(aic)
        if not isinstance(acs, dict):
            continue
        _cache_acs(acs, ranking, discovery_url)
        summaries.append(_build_normalized_summary(acs, ranking))

    return json.dumps({
        "success": True,
        "summary": f"Discovered {len(summaries)} agent(s) for query: {query}",
        "data": {"agents": summaries, "total": len(summaries)},
    }, ensure_ascii=False)


@mcp.tool()
async def start_task(aic: str, task_description: str, session_id: str,
                     task_id: str = "", leader_aic: str = "") -> str:
    """Start a new AIP task with the specified Partner agent.

    Args:
        aic: Partner agent AIC from discover results
        task_description: What the partner should do, in natural language
        session_id: Unique session identifier (use a UUID or conversation ID)
        task_id: Optional task ID (auto-generated if empty)
        leader_aic: Your own AIC as Leader (default: from env LEADER_AIC)

    Returns:
        JSON with task_id, state, message, products, is_terminal.
        Check is_terminal: if true, the task completed synchronously.
    """
    leader = leader_aic or LEADER_AIC
    cache = _load_acs_cache(aic)
    if cache is None:
        return json.dumps({"success": False, "error": f"ACS cache not found for aic: {aic}. Run discover first.", "error_type": "cache_miss"})

    partner_url = cache.get("normalized_summary", {}).get("endpoint_url", "")
    if not partner_url:
        return json.dumps({"success": False, "error": f"No endpoint URL for aic: {aic}", "error_type": "cache_miss"})

    tid = task_id or f"task-{uuid.uuid4()}"
    client = AipRpcClient(partner_url=partner_url, leader_id=leader)
    try:
        result = await client.start_task(session_id, task_description, task_id=tid)
    except Exception as e:
        _save_task_state(tid, aic, partner_url, "error", {"error": str(e)}, session_id)
        return json.dumps({"success": False, "error": str(e), "error_type": "http_error"})
    finally:
        await client.close()

    state = _get_state_str(result)
    _save_task_state(tid, aic, partner_url, state, _result_to_dict(result), session_id)

    return json.dumps({
        "success": True, "task_id": tid, "state": state,
        "message": _extract_message(result), "products": _extract_products(result),
        "is_terminal": state in TERMINAL_STATES,
        "needs_input": state == "awaiting-input",
        "awaiting_completion": state == "awaiting-completion", "aic": aic,
    }, ensure_ascii=False)


@mcp.tool()
async def get_task(task_id: str, leader_aic: str = "",
                   poll: bool = True, poll_interval: int = 5,
                   poll_timeout: int = 600) -> str:
    """Poll task state until it leaves the working/accepted state.

    This tool handles polling internally — call it once and it blocks until the
    task reaches a meaningful state (awaiting-input, awaiting-completion, or terminal).

    Args:
        task_id: Task ID returned by start_task
        leader_aic: Your own AIC as Leader
        poll: Auto-poll until non-working state (default True)
        poll_interval: Seconds between polls (default 5)
        poll_timeout: Max total wait seconds (default 600)

    Returns:
        JSON with task_id, state, message, products, is_terminal, needs_input, awaiting_completion.
    """
    leader = leader_aic or LEADER_AIC
    cache = _load_task_cache(task_id)
    if cache is None:
        return json.dumps({"success": False, "error": f"Task cache not found: {task_id}", "error_type": "cache_miss"})

    cached_state = cache.get("state", "")
    if cached_state == "error":
        return json.dumps({"success": False, "error": "Task failed to start.", "error_type": "task_start_failed", "task_id": task_id})
    if cached_state in TERMINAL_STATES:
        return json.dumps({"success": True, "task_id": task_id, "state": cached_state,
                           "message": "(from cache)", "is_terminal": True, "products": []})

    partner_url = cache.get("partner_url", "")
    session_id = cache.get("session_id", "")
    deadline = time.monotonic() + poll_timeout
    attempts = 0

    while True:
        attempts += 1
        client = AipRpcClient(partner_url=partner_url, leader_id=leader)
        try:
            result = await client.get_task(task_id, session_id)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e), "error_type": "http_error", "task_id": task_id})
        finally:
            await client.close()

        state = _get_state_str(result)
        _update_task_cache(task_id, state, _result_to_dict(result))

        if not poll or state not in WORKING_STATES:
            return json.dumps({
                "success": True, "task_id": task_id, "state": state,
                "message": _extract_message(result),
                "needs_input": state == TaskState.AwaitingInput.value,
                "awaiting_completion": state == TaskState.AwaitingCompletion.value,
                "is_terminal": state in TERMINAL_STATES,
                "products": _extract_products(result), "poll_attempts": attempts,
            }, ensure_ascii=False)

        if time.monotonic() >= deadline:
            return json.dumps({"success": False, "error": f"Polling timed out after {poll_timeout}s",
                               "error_type": "poll_timeout", "task_id": task_id, "state": state})

        await asyncio.sleep(poll_interval)


@mcp.tool()
async def continue_task(task_id: str, user_input: str, leader_aic: str = "") -> str:
    """Supply user input to a task that is awaiting-input or awaiting-completion.

    Args:
        task_id: Task ID
        user_input: The user's response or additional information
        leader_aic: Your own AIC as Leader

    Returns:
        JSON with task_id, state, message.
    """
    leader = leader_aic or LEADER_AIC
    cache = _load_task_cache(task_id)
    if cache is None:
        return json.dumps({"success": False, "error": f"Task cache not found: {task_id}", "error_type": "cache_miss"})

    continuable = {TaskState.AwaitingInput.value, TaskState.AwaitingCompletion.value}
    current = cache.get("state", "")
    if current not in continuable:
        return json.dumps({"success": False, "error": f"Task is in '{current}', cannot continue.", "error_type": "state_error"})

    partner_url = cache.get("partner_url", "")
    session_id = cache.get("session_id", "")
    client = AipRpcClient(partner_url=partner_url, leader_id=leader)
    try:
        result = await client.continue_task(task_id, session_id, user_input)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "error_type": "http_error", "task_id": task_id})
    finally:
        await client.close()

    state = _get_state_str(result)
    _update_task_cache(task_id, state, _result_to_dict(result))
    return json.dumps({"success": True, "task_id": task_id, "state": state, "message": _extract_message(result)}, ensure_ascii=False)


@mcp.tool()
async def complete_task(task_id: str, leader_aic: str = "") -> str:
    """Confirm Partner's deliverables and complete the task. Only valid from awaiting-completion state.

    Args:
        task_id: Task ID
        leader_aic: Your own AIC as Leader

    Returns:
        JSON with task_id, state, message.
    """
    leader = leader_aic or LEADER_AIC
    cache = _load_task_cache(task_id)
    if cache is None:
        return json.dumps({"success": False, "error": f"Task cache not found: {task_id}", "error_type": "cache_miss"})

    if cache.get("state", "") != TaskState.AwaitingCompletion.value:
        return json.dumps({"success": False, "error": f"Task is in '{cache.get('state')}', complete requires awaiting-completion.", "error_type": "state_error"})

    partner_url = cache.get("partner_url", "")
    session_id = cache.get("session_id", "")
    client = AipRpcClient(partner_url=partner_url, leader_id=leader)
    try:
        result = await client.complete_task(task_id, session_id)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "error_type": "http_error", "task_id": task_id})
    finally:
        await client.close()

    state = _get_state_str(result)
    _update_task_cache(task_id, state, _result_to_dict(result))
    return json.dumps({"success": True, "task_id": task_id, "state": state, "message": _extract_message(result)}, ensure_ascii=False)


@mcp.tool()
async def cancel_task(task_id: str, leader_aic: str = "") -> str:
    """Cancel an active task. Valid from any non-terminal state.

    Args:
        task_id: Task ID
        leader_aic: Your own AIC as Leader

    Returns:
        JSON with task_id, state, message.
    """
    leader = leader_aic or LEADER_AIC
    cache = _load_task_cache(task_id)
    if cache is None:
        return json.dumps({"success": False, "error": f"Task cache not found: {task_id}", "error_type": "cache_miss"})

    if cache.get("state", "") in TERMINAL_STATES:
        return json.dumps({"success": False, "error": f"Task already in terminal state '{cache.get('state')}'.", "error_type": "state_error"})

    partner_url = cache.get("partner_url", "")
    session_id = cache.get("session_id", "")
    client = AipRpcClient(partner_url=partner_url, leader_id=leader)
    try:
        result = await client.cancel_task(task_id, session_id)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "error_type": "http_error", "task_id": task_id})
    finally:
        await client.close()

    state = _get_state_str(result)
    _update_task_cache(task_id, state, _result_to_dict(result))
    return json.dumps({"success": True, "task_id": task_id, "state": state, "message": _extract_message(result)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="sse")
