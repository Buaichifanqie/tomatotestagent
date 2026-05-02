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
    token_threshold: int = 100000,
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
    """
    _dispatch = dispatch_fn or _default_dispatch_fn

    for _round in range(max_rounds):
        microcompact(messages)

        if estimate_tokens(messages) > token_threshold:
            messages[:] = auto_compact(messages, llm_provider, system)
            identity_re_injection(system, messages)

        response: LLMResponse = await llm_provider.chat(
            system=system,
            messages=messages,
            tools=tools,
        )

        messages.append({"role": "assistant", "content": response.content})

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

        if response.stop_reason != "tool_use":
            return messages

        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if block.get("type") == "tool_use":
                try:
                    result = await _dispatch(
                        str(block.get("name", "")),
                        dict(block.get("input", {})),
                    )
                except Exception as exc:
                    result = {"error": str(exc), "tool_name": block.get("name")}
                    logger.error(
                        "Tool dispatch failed",
                        extra={"extra_data": {"tool": block.get("name"), "error": str(exc)}},
                    )
                tool_results.append(result)

        messages.append({"role": "user", "content": tool_results})

    return messages


def microcompact(messages: list[dict[str, Any]]) -> None:
    """每轮循环后自动移除冗余空白和工具调用的冗余输出(原地修改)"""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = content.strip()
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
    llm_provider: ILLMProvider,
    system: str,
) -> list[dict[str, Any]]:
    """token 超过阈值时, 用 LLM 对历史消息生成摘要替换原文"""
    if len(messages) <= 4:
        return list(messages)

    keep_head = 1
    keep_tail = 2

    head = messages[:keep_head]
    middle = messages[keep_head:-keep_tail]
    tail = messages[-keep_tail:]

    summary_text = _build_summary_text(middle)

    compressed: list[dict[str, Any]] = list(head)
    compressed.append(
        {
            "role": "user",
            "content": f"[Conversation Summary]\n{summary_text}\n[End Summary]",
        }
    )
    compressed.extend(tail)

    logger.info(
        "Auto-compact applied",
        extra={
            "extra_data": {
                "original_count": len(messages),
                "compressed_count": len(compressed),
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
    """估算 messages 的 token 数(简单实现: len(json.dumps(messages)) // 4)"""
    try:
        raw = _JSON_DUMPS(messages, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning("Token estimation JSON serialization failed", extra={"extra_data": {"error": str(exc)}})
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
    """从消息列表生成简单摘要文本"""
    if not messages:
        return "No messages."

    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if isinstance(content, list):
            text_parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = str(block.get("text", ""))
                    if text:
                        text_parts.append(text[:200])
                elif isinstance(block, dict) and block.get("type") == "tool_use":
                    text_parts.append(f"[tool:{block.get('name', '')}]")
            content_str = " ".join(text_parts)
        elif isinstance(content, str):
            content_str = content[:200]
        else:
            content_str = str(content)[:200]

        parts.append(f"{role}: {content_str}")

    return "\n".join(parts)
