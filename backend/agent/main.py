"""
main.py — Interactive CLI entry point for the ACPS Leader Agent.

Error handling policy:
  - Tool errors are returned as JSON to the agent; the agent decides whether to
    retry, use a fallback approach, or escalate to the user.
  - If graph.invoke() itself throws (e.g. network error reaching the LLM), the
    exception is caught here, formatted as a system error message, and injected
    back into the conversation so the agent gets one more chance to recover.
  - Only persistent failures that prevent the agent from responding at all are
    surfaced to the user as a brief notice.
  - The main loop NEVER breaks on an error; only explicit "quit" commands do.

Usage:
    cd backend && python -m agent.main
    # or
    cd backend/agent && python main.py

Logs are written to: acps_leader/backend/logs/agent.log
"""

import sys
import traceback
import uuid
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from graph_builder import build_agent
from logger import get_logger

log = get_logger("main")

_MAX_CONSECUTIVE_FAILURES = 2

_TYPE_PREFIX: dict[str, str] = {
    "result":   "✔ Agent",
    "error":    "✘ Agent",
    "question": "? Agent",
    "info":     "  Agent",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_reply(messages: list) -> tuple[str, str]:
    """
    Scan messages for the last `generate_response` tool call.
    Returns (message_text, response_type).
    """
    last_msg = ""
    last_type = "info"

    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for call in msg.tool_calls:
                if call.get("name") == "generate_response":
                    call_args = call.get("args", {})
                    last_msg = call_args.get("message", "")
                    last_type = call_args.get("response_type", "info")

    if last_msg:
        return last_msg, last_type

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            if isinstance(msg.content, str):
                return msg.content, "info"
            parts = [
                b["text"]
                for b in msg.content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if parts:
                return "\n".join(parts), "info"

    return "", "info"


def _log_and_print_steps(messages: list, turn: int) -> None:
    """Walk the message list and log every tool call + result for this turn."""
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for call in msg.tool_calls:
                name = call.get("name", "?")
                if name == "generate_response":
                    continue
                call_args = call.get("args", {})
                brief: dict = {}
                for k, v in call_args.items():
                    s = str(v)
                    brief[k] = (s[:120] + "...") if len(s) > 120 else s
                log.info("TURN %d  TOOL_CALL  tool=%s  args=%s", turn, name, brief)
                print(f"  → [{name}] {brief}")

        elif isinstance(msg, ToolMessage):
            content = str(msg.content)
            if '"delivered": true' in content:
                continue
            preview = (content[:200] + "...") if len(content) > 200 else content
            log.info("TURN %d  TOOL_RESULT  tool_call_id=%s  preview=%s",
                     turn, msg.tool_call_id, preview)
            print(f"    ↳ {preview}")


def _inject_error_and_retry(
    graph,
    error: Exception,
    invoke_config: dict,
    turn: int,
) -> tuple[str, str, bool]:
    """Inject a system-level error notice and let the agent try to recover."""
    tb = traceback.format_exc()
    error_summary = (
        f"[SYSTEM] An internal error occurred while processing your last turn:\n"
        f"  type  : {type(error).__name__}\n"
        f"  detail: {error}\n\n"
        f"Please assess the situation and decide the best next step:\n"
        f"  - If this is a transient issue (network, timeout), suggest retrying.\n"
        f"  - If a tool call was in progress, try to recover gracefully.\n"
        f"  - Do NOT expose raw stack traces to the user.\n"
        f"  - Call generate_response(response_type='error') with a plain-language\n"
        f"    explanation only if truly unrecoverable."
    )
    log.warning("TURN %d  INJECTING_ERROR_TO_AGENT  error=%s", turn, error)
    log.debug("TURN %d  FULL_TRACEBACK\n%s", turn, tb)

    try:
        result = graph.invoke(
            {"messages": [HumanMessage(content=error_summary)]},
            config=invoke_config,
        )
        messages: list = result.get("messages", [])
        _log_and_print_steps(messages, turn)
        reply, rtype = _extract_reply(messages)
        if reply:
            log.info("TURN %d  AGENT_RECOVERED  type=%s  reply=%s",
                     turn, rtype, reply[:300])
            return reply, rtype, True
    except Exception as retry_err:
        log.error("TURN %d  RETRY_ALSO_FAILED  error=%s", turn, retry_err)

    return "", "error", False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("  ACPS Leader Agent")
    print("=" * 60)
    print("Type your message and press Enter.")
    print("Type 'quit' or press Ctrl+C to exit.\n")

    log.info("=" * 50)
    log.info("ACPS Leader Agent starting up")

    try:
        graph = build_agent()
    except ValueError as e:
        log.error("Startup failed: %s", e)
        print(f"[startup error] {e}")
        return

    thread_id = str(uuid.uuid4())
    invoke_config = {"configurable": {"thread_id": thread_id}}
    log.info("Session started  thread_id=%s", thread_id)

    turn = 0
    consecutive_failures = 0
    pending_interrupt = False  # 是否有未处理的 interrupt

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            log.info("Session ended by user  thread_id=%s  turns=%d", thread_id, turn)
            print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "bye"}:
            log.info("Session ended by user command  thread_id=%s  turns=%d",
                     thread_id, turn)
            print("Goodbye.")
            break

        turn += 1
        log.info("TURN %d  USER  message=%s", turn, user_input)
        print()

        try:
            if pending_interrupt:
                log.info("TURN %d  RESUMING from interrupt", turn)
                input_cmd = Command(resume=user_input)
            else:
                input_cmd = {"messages": [HumanMessage(content=user_input)]}

            result = graph.invoke(input_cmd, config=invoke_config)
            messages: list = result.get("messages", [])
            _log_and_print_steps(messages, turn)

            # 检查是否触发了 interrupt（generate_response 暂停）
            state = graph.get_state(invoke_config)
            has_interrupt = False
            interrupt_data = None
            if hasattr(state, "tasks") and state.tasks:
                for task in state.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        has_interrupt = True
                        intr = task.interrupts[0]
                        interrupt_data = intr.value if hasattr(intr, "value") else intr
                        break

            if has_interrupt and isinstance(interrupt_data, dict):
                reply = interrupt_data.get("message", "")
                rtype = interrupt_data.get("response_type", "info")
                pending_interrupt = True
            else:
                reply, rtype = _extract_reply(messages)
                pending_interrupt = False

            if not reply:
                reply = "(Agent returned no text response.)"
                rtype = "info"

            prefix = _TYPE_PREFIX.get(rtype, "  Agent")
            log.info("TURN %d  AGENT  type=%s  reply=%s", turn, rtype, reply[:300])
            print(f"\n{prefix}: {reply}")
            consecutive_failures = 0

        except Exception as e:
            log.error("TURN %d  INVOKE_FAILED  error=%s", turn, e, exc_info=True)
            pending_interrupt = False

            reply, rtype, recovered = _inject_error_and_retry(
                graph, e, invoke_config, turn
            )

            if recovered and reply:
                prefix = _TYPE_PREFIX.get(rtype, "  Agent")
                print(f"\n{prefix}: {reply}")
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                log.error("TURN %d  UNRECOVERABLE  consecutive=%d",
                          turn, consecutive_failures)
                print(
                    "\n✘ Agent: 本轮处理时遇到了一个内部错误，暂时无法正常响应。"
                    "请稍后再试，或换一种方式描述您的请求。"
                )
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    print(
                        f"\n[提示] 连续 {consecutive_failures} 次出现内部错误，"
                        "如问题持续请检查日志 backend/service.log 。"
                    )
                    consecutive_failures = 0

        print()

    log.info("=" * 50)


if __name__ == "__main__":
    main()
