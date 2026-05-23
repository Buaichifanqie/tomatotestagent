from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from testagent.llm.base import ILLMProvider, LLMResponse

logger = get_logger(__name__)

_JSON_DUMPS = json.dumps
_IDENTITY_RE_INJECTION_THRESHOLD = 5

# ── 上下文管理常量 ─────────────────────────────────────────
_TOKEN_THRESHOLD = 60000         # chars, ~15K tokens — 触发 auto-compact 的阈值
_TOOL_RESULT_TRUNCATE = 3000     # chars, 单条 tool result 最大长度
_PAGE_SOURCE_TRUNCATE = 2500     # chars, XML 页面源码截断长度
_MICROCOMPACT_TOOL_CUT = 800     # chars, microcompact 时 tool result 保留长度
_KEEP_HEAD = 1                   # auto-compact 保留的前几条消息
_KEEP_TAIL = 3                   # auto-compact 保留的后几条消息


def _normalize_tool_args(args: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool call arguments from various LLM output formats.

    Some LLMs wrap arguments in a "raw" JSON string field or use other
    non-standard formats. This function detects and unwraps them.
    """
    if not isinstance(args, dict):
        return args

    # Case 1: {"raw": "{\"key\": \"val\", ...}"} — raw JSON string wrapper
    if "raw" in args and len(args) == 1:
        raw_val = args["raw"]
        if isinstance(raw_val, str):
            try:
                parsed = json.loads(raw_val)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass

    # Case 2: {"arguments": "{\"key\": \"val\"}", "name": "..."} — structured wrapper
    if "arguments" in args and isinstance(args["arguments"], str):
        try:
            parsed = json.loads(args["arguments"])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    return args

TOOL_HANDLERS: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {}


def register_tool_handler(tool_name: str, handler: Callable[..., Awaitable[dict[str, Any]]]) -> None:
    """Register a tool handler that dispatch_tool will route to."""
    TOOL_HANDLERS[tool_name] = handler
    logger.debug(
        "Tool handler registered",
        extra={"extra_data": {"tool_name": tool_name, "handler": handler.__name__}},
    )


async def agent_loop(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str,
    llm_provider: ILLMProvider,
    dispatch_fn: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    max_rounds: int = 50,
    token_threshold: int = _TOKEN_THRESHOLD,
    progress_callback: Callable[[dict[str, Any], list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    """
    核心 ReAct Loop 实现。

    循环体永远不变:
    1. microcompact(messages) -- 每轮去冗余
    2. 若 estimate_tokens > threshold -> auto_compact(messages) -- 超阈值摘要
    3. llm_provider.chat() -- LLM 调用
    4. if stop_reason != "tool_use" -> return -- 单退出条件
    5. dispatch_fn() -- 工具调用
    6. 追加 tool_results -> 继续循环

    预算耗尽恢复:
    若 LLM 调用抛出 LLMTokenLimitError(BUDGET_EXHAUSTED):
    - 立即压缩消息（保留最近 2 轮）
    - 重置预算管理器
    - 重试 LLM 调用
    """
    from testagent.common.errors import LLMTokenLimitError

    _dispatch = dispatch_fn or _default_dispatch_fn

    for _round in range(max_rounds):
        microcompact(messages)

        if estimate_tokens(messages) > token_threshold:
            messages[:] = auto_compact(messages)
            identity_re_injection(system, messages)

        try:
            response: LLMResponse = await llm_provider.chat(
                system=system,
                messages=messages,
                tools=tools,
            )
        except LLMTokenLimitError as exc:
            if exc.code == "BUDGET_EXHAUSTED":
                logger.warning(
                    "Token budget exhausted, performing emergency compact...",
                    extra={"extra_data": {"detail": str(exc)}},
                )
                # 紧急压缩：保留最近 2 轮的消息
                if len(messages) > 6:
                    tail_count = 6
                elif len(messages) > 3:
                    tail_count = 3
                else:
                    tail_count = 0

                if tail_count:
                    head = messages[:1]
                    tail = messages[-tail_count:]
                    summary = _build_summary_text(messages[1:-tail_count])
                    messages[:] = head
                    messages.append({
                        "role": "user",
                        "content": f"[Compressed Summary of previous turns]\n{summary}\n[End Summary]",
                    })
                    messages.extend(tail)
                # 重置预算
                llm_provider.reset_budget()
                logger.info("Budget reset after emergency compact, retrying...")
                # 重试 LLM 调用
                response = await llm_provider.chat(
                    system=system,
                    messages=messages,
                    tools=tools,
                )
            else:
                raise

        # ── 转换为 OpenAI 兼容的 assistant 消息格式 ──
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            if block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": str(block.get("id", "")),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name", "")),
                        "arguments": _JSON_DUMPS(block.get("input", {}), ensure_ascii=False),
                    },
                })

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": "\n".join(text_parts) or None,
        }
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        # Preserve any provider-specific fields (e.g. DeepSeek reasoning_content)
        if response.raw_message:
            for key in ("reasoning_content",):
                val = response.raw_message.get(key)
                if val is not None:
                    assistant_msg[key] = val
        messages.append(assistant_msg)

        logger.debug(
            "Agent loop round completed",
            extra={
                "extra_data": {
                    "round": _round + 1,
                    "stop_reason": response.stop_reason,
                    "usage": response.usage,
                }
            },
        )

        if not tool_calls:
            # No more tool calls — final round
            if progress_callback:
                progress_callback({"assistant": assistant_msg, "final": True}, [])
            return messages

        # ── 调用工具并追加 tool 结果消息 ──
        tool_results: list[dict[str, Any]] = []
        for tc in tool_calls:
            tool_name = str(tc["function"]["name"])
            raw_args = tc["function"]["arguments"]
            try:
                if isinstance(raw_args, str):
                    import json as _json

                    parsed_input: dict[str, Any] = _json.loads(raw_args)
                else:
                    parsed_input = dict(raw_args)
                # Some LLMs wrap arguments in a "raw" JSON string field
                parsed_input = _normalize_tool_args(parsed_input)
                result = await _dispatch(tool_name, parsed_input)
            except Exception as exc:
                result = {"error": str(exc), "tool_name": tool_name}
                logger.error(
                    "Tool dispatch failed",
                    extra={"extra_data": {"tool": tool_name, "error": str(exc)}},
                )
            # Truncate large tool results to avoid exceeding context window
            result_str = _JSON_DUMPS(result, ensure_ascii=False)
            if len(result_str) > _TOOL_RESULT_TRUNCATE:
                result_str = result_str[:_TOOL_RESULT_TRUNCATE - 3] + "..."
            tool_results.append(result)
            messages.append({
                "role": "tool",
                "tool_call_id": str(tc["id"]),
                "content": result_str,
            })

        if progress_callback:
            progress_callback({"assistant": assistant_msg, "tool_calls": tool_calls}, tool_results)

    return messages


def microcompact(messages: list[dict[str, Any]]) -> None:
    """每轮的去冗余压缩（原地修改，无 API 调用）

    - 移除空白 content
    - 截断过长的 tool result
    - 压缩 XML/JSON 页面源码
    """
    for msg in messages:
        content = msg.get("content")

        # Tool result 消息: content 是字符串，截断过长的
        if msg.get("role") == "tool" and isinstance(content, str):
            if len(content) > _MICROCOMPACT_TOOL_CUT:
                half = _MICROCOMPACT_TOOL_CUT // 2
                msg["content"] = content[:half] + f"\n... (truncated {len(content) - 2 * half} chars) ...\n" + content[-half:]
            continue

        if isinstance(content, str):
            content = content.strip()
            if not content:
                msg["content"] = None
            else:
                msg["content"] = content
        elif isinstance(content, list):
            compacted: list[dict[str, Any]] = []
            for block in content:
                if isinstance(block, dict):
                    compacted_block = _compact_tool_block(block)
                    if compacted_block is not None:
                        compacted.append(compacted_block)
            msg["content"] = compacted


def auto_compact(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """消息列表压缩：将历史消息（除首尾）替换为结构化摘要（无 API 调用）

    保留:
    - 第一条消息（原始用户 query）
    - 最后 KEEP_TAIL 条消息（最近的交互上下文）
    中间部分 → 结构化摘要
    """
    if len(messages) <= _KEEP_HEAD + _KEEP_TAIL + 1:
        return list(messages)

    head = messages[:_KEEP_HEAD]
    middle = messages[_KEEP_HEAD:-_KEEP_TAIL]
    tail = messages[-_KEEP_TAIL:]

    summary_text = _build_summary_text(middle)

    compressed: list[dict[str, Any]] = list(head)
    compressed.append({
        "role": "user",
        "content": f"[Conversation Summary]\n{summary_text}\n[End Summary]",
    })
    compressed.extend(tail)

    logger.info(
        "Auto-compact applied",
        extra={
            "extra_data": {
                "original_count": len(messages),
                "compressed_count": len(compressed),
                "summary_len": len(summary_text),
            }
        },
    )

    return compressed


def identity_re_injection(system: str, messages: list[dict[str, Any]]) -> None:
    """压缩后如果 messages 过短, 重新注入 Agent 身份块防止忘记自己是谁"""
    if len(messages) >= _IDENTITY_RE_INJECTION_THRESHOLD:
        return

    has_system_block = any(
        isinstance(msg, dict)
        and msg.get("role") == "user"
        and isinstance(msg.get("content"), str)
        and "[Agent Identity]" in str(msg.get("content"))
        for msg in messages
    )

    if has_system_block:
        return

    identity_prompt = (
        "[Agent Identity]\n"
        f"You are TestAgent, an AI test intelligence agent. Your core directive:\n{system}\n"
        "[End Identity]"
    )
    messages.insert(0, {"role": "user", "content": identity_prompt})


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """估算 messages 的 token 数（简单实现: len(json.dumps) // 4）"""
    try:
        raw = _JSON_DUMPS(messages, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Token estimation failed",
            extra={"extra_data": {"error": str(exc)}},
        )
        return 0
    return len(raw) // 4


async def dispatch_tool(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Dispatch tool call to the registered handler, or return an error if unknown."""
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        logger.warning(
            "Unknown tool called",
            extra={"extra_data": {"tool_name": tool_name}},
        )
        return {"error": f"Unknown tool: {tool_name}", "tool_name": tool_name}
    logger.debug(
        "Dispatching tool",
        extra={"extra_data": {"tool_name": tool_name}},
    )
    return await handler(tool_input)


async def _default_dispatch_fn(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return await dispatch_tool(tool_name, tool_input)


def _compact_tool_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """压缩单个 tool block, 去除冗余字段"""
    block_type = block.get("type")
    if block_type == "text":
        text = str(block.get("text", ""))
        if not text.strip():
            return None
        return {"type": "text", "text": text.strip()}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "name": block.get("name", ""),
            "input": block.get("input", {}),
        }
    if block_type == "tool_result":
        content = block.get("content", "")
        if isinstance(content, str) and len(content) > 500:
            content = content[:497] + "..."
        return {
            "type": "tool_result",
            "name": block.get("name", ""),
            "content": content,
        }
    return block


def _build_summary_text(messages: list[dict[str, Any]]) -> str:
    """从消息列表生成结构化摘要（无 API 调用）

    聚焦于：
    - 用户的意图和目标
    - Assistant 的推理和决策
    - 调用了哪些工具及其关键结果（不含冗余输出）
    """
    if not messages:
        return "No messages."

    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")

        if role == "user" and isinstance(content, str):
            # 保留用户意图
            text = content.strip()
            # 跳过系统注入的大段文本
            if text.startswith("[Conversation Summary]") or text.startswith("[Agent Identity]"):
                parts.append(f"User: [system context]")
            elif text.startswith("[Compressed Summary"):
                parts.append(f"User: [previous summary]")
            else:
                parts.append(f"User: {text[:300]}")

        elif role == "assistant":
            texts: list[str] = []
            tools_used: list[str] = []

            if isinstance(content, str) and content:
                texts.append(content[:300])
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict):
                        if b.get("type") == "text":
                            t = str(b.get("text", ""))[:200]
                            if t:
                                texts.append(t)
                        elif b.get("type") == "tool_use":
                            tools_used.append(str(b.get("name", "")))

            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args = fn.get("arguments", "")
                    if name:
                        # Compact args — just show key params
                        if isinstance(args, str) and len(args) > 80:
                            args = args[:77] + "..."
                        tools_used.append(f"{name}({args})")

            line_parts_list: list[str] = []
            if texts:
                line_parts_list.append(texts[0])
            if tools_used:
                line_parts_list.append(f"[{', '.join(tools_used[:5])}]")
            parts.append(f"Assistant: {' | '.join(line_parts_list)}")

        elif role == "tool":
            # 只保留 tool result 的关键信息，不保留完整输出
            content_str = str(content)[:120] if isinstance(content, str) else str(content)[:120]
            # 只保留前 120 字符作为摘要
            parts.append(f"Tool: {content_str[:120]}")

    return "\n".join(parts)
