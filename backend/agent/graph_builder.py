"""graph_builder.py — Build the ACPS Leader Agent graph."""

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from config import MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL
from logger import get_logger
from system_prompt import SYSTEM_PROMPT
from tools import ALL_TOOLS

log = get_logger("agent")


def build_agent():
    """Build and return the compiled agent graph (CompiledStateGraph)."""
    if not OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY is not set. "
            "Create a .env file in the backend directory."
        )

    log.info("Building agent  model=%s  base_url=%s  tools=%s",
             MODEL_NAME, OPENAI_BASE_URL, [t.name for t in ALL_TOOLS])

    llm = ChatOpenAI(
        model=MODEL_NAME,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        temperature=0,
    )

    checkpointer = MemorySaver()

    graph = create_agent(
        model=llm,
        tools=ALL_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )

    log.info("Agent graph built successfully")
    return graph
