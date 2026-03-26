"""
graph_builder_mcp.py — Build the MCP-based ACPS Leader Agent graph.

Connects to the external ACPS MCP Server to obtain protocol tools, then
combines them with the local generate_response tool to build the agent.
"""

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from langchain_mcp_adapters.client import MultiServerMCPClient

from config import MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL, MCP_SERVER_URL
from logger import get_logger
from system_prompt_mcp import SYSTEM_PROMPT_MCP
from tools_mcp import LOCAL_TOOLS

log = get_logger("agent_mcp")

_mcp_client: MultiServerMCPClient | None = None


def _get_mcp_client() -> MultiServerMCPClient:
    """Lazily create the MCP client (singleton)."""
    global _mcp_client
    if _mcp_client is None:
        log.info("Creating MCP client for %s", MCP_SERVER_URL)
        _mcp_client = MultiServerMCPClient(
            {"acps": {"url": MCP_SERVER_URL, "transport": "sse"}}
        )
    return _mcp_client


def _stringify_tool(tool):
    """Wrap an MCP tool so its return value is always a plain string.

    Some LLM providers (e.g. DeepSeek) reject tool messages whose content is a
    list of structured content blocks. This wrapper flattens the MCP response
    to a single string.
    """
    original = tool.coroutine

    async def _wrapped(**kwargs):
        result = await original(**kwargs)
        if isinstance(result, str):
            return result
        if isinstance(result, list):
            parts = []
            for item in result:
                if hasattr(item, "text"):
                    parts.append(item.text)
                elif isinstance(item, dict) and "text" in item:
                    parts.append(item["text"])
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(result)

    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        coroutine=_wrapped,
    )


async def build_agent_mcp():
    """Build and return the compiled MCP agent graph (CompiledStateGraph)."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set.")

    client = _get_mcp_client()
    raw_tools = await client.get_tools()
    mcp_tools = [_stringify_tool(t) for t in raw_tools]

    all_tools = mcp_tools + LOCAL_TOOLS

    log.info("Building MCP agent  model=%s  tools=%s",
             MODEL_NAME, [t.name for t in all_tools])

    llm = ChatOpenAI(
        model=MODEL_NAME,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        temperature=0,
    )

    checkpointer = MemorySaver()

    graph = create_agent(
        model=llm,
        tools=all_tools,
        system_prompt=SYSTEM_PROMPT_MCP,
        checkpointer=checkpointer,
    )

    log.info("MCP agent graph built successfully")
    return graph


async def shutdown_mcp_client():
    """Clean up the MCP client on shutdown."""
    global _mcp_client
    if _mcp_client is not None:
        _mcp_client = None
        log.info("MCP client released")
