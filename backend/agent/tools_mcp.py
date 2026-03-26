"""
tools_mcp.py — Local tools for the MCP-based agent.

The ACPS protocol tools come from the MCP server. This module only provides
the local generate_response tool (which uses LangGraph's interrupt mechanism
to pause and wait for user input).
"""

import json
from typing import Literal

from langchain_core.tools import tool
from langgraph.types import interrupt

from logger import get_logger

log = get_logger("tools_mcp")

_VALID_RESPONSE_TYPES = {"result", "error", "question", "info"}


@tool
def generate_response(message: str, response_type: Literal["result", "error", "question", "info"] = "info") -> str:
    """Deliver a reply to the user. This tool MUST be called for every message
    that is intended for the user — task results, error notices, clarifying
    questions, or plain information.

    After delivering the message, execution pauses to wait for the user's next
    input. The user's reply is returned as a string so you can continue the
    conversation based on what the user said.

    Parameters
    ----------
    message       : The human-readable text to show the user.
    response_type : One of "result", "error", "question", "info".

    Returns the user's next message as a string.
    """
    rtype = response_type if response_type in _VALID_RESPONSE_TYPES else "info"
    log.info("generate_response  type=%s  preview=%s", rtype, message[:200])

    user_reply = interrupt({"message": message, "response_type": rtype})
    log.info("generate_response  resumed with user_reply=%s", str(user_reply)[:200])

    return json.dumps({
        "success": True,
        "delivered": True,
        "response_type": rtype,
        "user_reply": user_reply,
    })


LOCAL_TOOLS = [generate_response]
