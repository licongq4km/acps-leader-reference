"""
SSE 流式输出处理器

将 LangGraph astream_events 转换为前端可消费的 SSE 事件流。

事件协议（v2）：
  thinking    — Agent 思考/推理过程（逐 token 流式）
  message     — Agent 正式回复（逐 token 流式，从 tool_call_chunks 增量解析）
  message_end — 正式回复结束，附带 response_type
  tool_start  — 工具开始执行
  tool_end    — 工具执行完成
  error       — 错误
  done        — 流结束
"""
import json
import logging
from typing import AsyncIterator

logger = logging.getLogger(__name__)


def sse_event(event_type: str, data: dict) -> str:
    """构造 SSE 事件字符串"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class _GRParser:
    """从 generate_response 的 tool_call args JSON 碎片中增量提取 message 值。

    同时在完成后提取 response_type。
    """

    def __init__(self):
        self._names: dict[int, str] = {}
        self._gr_index: int = -1
        self._args_buf: str = ""
        self._msg_start: int = -1
        self._scan_pos: int = 0
        self._done: bool = False

    @property
    def matched(self) -> bool:
        return self._gr_index >= 0

    @property
    def done(self) -> bool:
        return self._done

    @property
    def response_type(self) -> str:
        if not self._done:
            return "info"
        buf = self._args_buf
        tag = '"response_type"'
        idx = buf.find(tag)
        if idx < 0:
            return "info"
        colon = buf.find(":", idx + len(tag))
        if colon < 0:
            return "info"
        q1 = buf.find('"', colon + 1)
        if q1 < 0:
            return "info"
        q2 = buf.find('"', q1 + 1)
        if q2 < 0:
            return "info"
        return buf[q1 + 1:q2]

    def feed(self, name: str, args: str, index: int) -> str:
        name = name or ""
        args = args or ""

        if name:
            self._names[index] = self._names.get(index, "") + name
            if self._names[index] == "generate_response":
                self._gr_index = index

        if index != self._gr_index or self._gr_index < 0 or self._done:
            return ""
        if not args:
            return ""

        self._args_buf += args
        return self._extract()

    def _extract(self) -> str:
        buf = self._args_buf

        if self._msg_start < 0:
            idx = buf.find('"message"')
            if idx < 0:
                return ""
            colon = buf.find(":", idx + 9)
            if colon < 0:
                return ""
            quote = buf.find('"', colon + 1)
            if quote < 0:
                return ""
            self._msg_start = quote + 1
            self._scan_pos = self._msg_start

        new_chars: list[str] = []
        i = self._scan_pos
        while i < len(buf):
            ch = buf[i]
            if ch == "\\":
                if i + 1 < len(buf):
                    esc = buf[i + 1]
                    new_chars.append(
                        {"n": "\n", "t": "\t", "r": "\r", '"': '"',
                         "\\": "\\", "/": "/"}.get(esc, esc)
                    )
                    i += 2
                else:
                    break
            elif ch == '"':
                self._done = True
                break
            else:
                new_chars.append(ch)
                i += 1

        self._scan_pos = i
        return "".join(new_chars)


async def stream_agent_events(
    graph,
    input_cmd,
    config: dict,
    is_resume: bool = False,
) -> AsyncIterator[str]:
    """将 LangGraph astream_events 转换为 SSE 事件流。

    is_resume: True 时表示本次调用是从 interrupt 恢复。LangGraph 在 resume 时会
    重放被中断节点（generate_response）的 on_tool_start / on_tool_end 事件，导致
    旧消息再次输出、新消息被压制。通过 skipping_replay 标志跳过这段重放事件，
    在第一个 generate_response on_tool_end 之后才开始正常处理。
    """
    thread_id = config.get("configurable", {}).get("thread_id", "unknown")
    logger.info("Stream started: thread_id=%s is_resume=%s", thread_id, is_resume)

    gr_parser = _GRParser()
    message_delivered = False
    thinking_buf: list[str] = []
    skipping_replay = is_resume  # True 时跳过重放事件

    try:
        async for event in graph.astream_events(input_cmd, config, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")
            data = event.get("data", {})

            # ------------------------------------------
            # 跳过 resume 时 LangGraph 重放的旧事件
            # ------------------------------------------
            if skipping_replay:
                if kind == "on_tool_end" and name == "generate_response":
                    # 消费掉上一轮被中断的 generate_response 结束事件后停止跳过
                    skipping_replay = False
                continue

            # ------------------------------------------
            # LLM 流式 token
            # ------------------------------------------
            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if not chunk:
                    continue

                tc_chunks = getattr(chunk, "tool_call_chunks", None) or []
                if tc_chunks:
                    for tc in tc_chunks:
                        tc_name = tc.get("name", "") or getattr(tc, "name", "") or ""
                        tc_args = tc.get("args", "") or getattr(tc, "args", "") or ""
                        tc_idx = tc.get("index", 0) if isinstance(tc, dict) else getattr(tc, "index", 0)
                        tc_idx = tc_idx or 0
                        new_text = gr_parser.feed(tc_name, tc_args, tc_idx)
                        if new_text:
                            yield sse_event("message", {"content": new_text})

                    # 当 _GRParser 检测到 message 解析完毕，立即发 message_end
                    if gr_parser.done and not message_delivered:
                        rtype = gr_parser.response_type
                        logger.info("Agent response end (from parser): type=%s", rtype)
                        yield sse_event("message_end", {"response_type": rtype})
                        message_delivered = True
                        gr_parser = _GRParser()
                    continue

                if message_delivered:
                    continue

                content = ""
                if hasattr(chunk, "content") and chunk.content:
                    content = chunk.content if isinstance(chunk.content, str) else ""
                if content:
                    thinking_buf.append(content)
                    yield sse_event("thinking", {"content": content})
                continue

            # ------------------------------------------
            # 工具开始
            # ------------------------------------------
            if kind == "on_tool_start":
                input_data = data.get("input", {})

                if name == "generate_response":
                    rtype = "info"
                    if isinstance(input_data, dict):
                        rtype = input_data.get("response_type", "info")
                        if not gr_parser.matched and not message_delivered:
                            msg = input_data.get("message", "")
                            logger.info("Agent response (fallback): type=%s", rtype)
                            yield sse_event("message", {"content": msg})
                    if not message_delivered:
                        logger.info("Agent response end: type=%s", rtype)
                        yield sse_event("message_end", {"response_type": rtype})
                        message_delivered = True
                    gr_parser = _GRParser()
                else:
                    thinking_buf.clear()
                    args = input_data if isinstance(input_data, dict) else {}
                    brief_args = {}
                    for k, v in args.items():
                        s = str(v)
                        brief_args[k] = (s[:200] + "...") if len(s) > 200 else s
                    logger.info("Tool start: %s args=%s", name, brief_args)
                    yield sse_event("tool_start", {"tool": name, "args": brief_args})
                continue

            # ------------------------------------------
            # 工具结束
            # ------------------------------------------
            if kind == "on_tool_end":
                if name == "generate_response":
                    continue
                output = data.get("output", "")
                result_str = str(output)[:500] if output else ""
                logger.info("Tool end: %s", name)
                yield sse_event("tool_end", {"tool": name, "result": result_str})
                continue

    except Exception as e:
        logger.exception("Error streaming agent events: %s", e)
        yield sse_event("error", {
            "message": f"服务异常: {type(e).__name__}: {str(e)}"
        })

    # ------------------------------------------
    # 兜底：agent 没有调用 generate_response，将 thinking 内容作为正式回复
    # ------------------------------------------
    if not message_delivered and thinking_buf:
        fallback_content = "".join(thinking_buf)
        logger.info("Fallback: converting thinking to message (%d chars)",
                     len(fallback_content))
        yield sse_event("message", {"content": fallback_content})
        yield sse_event("message_end", {"response_type": "info"})

    logger.info("Stream completed: thread_id=%s", thread_id)
    yield sse_event("done", {"thread_id": thread_id})
