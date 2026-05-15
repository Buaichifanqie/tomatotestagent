from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.agent.loop import (
    TOOL_HANDLERS,
    _build_summary_text,
    _compact_tool_block,
    agent_loop,
    auto_compact,
    dispatch_tool,
    estimate_tokens,
    identity_re_injection,
    microcompact,
    register_tool_handler,
)
from testagent.agent.tools import create_skill_tool, handle_load_skill, register_mcp_tools
from testagent.gateway.mcp_registry import MCPServerInfo
from testagent.llm.base import ILLMProvider, LLMResponse
from testagent.skills.registry import SkillRegistry


def _make_mock_llm_provider(responses: list[LLMResponse]) -> MagicMock:
    provider = MagicMock(spec=ILLMProvider)
    provider.chat = AsyncMock(side_effect=responses)
    return provider


def _make_text_response(text: str = "Done", stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        content=[{"type": "text", "text": text}],
        stop_reason=stop_reason,
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def _make_tool_use_response(
    tool_name: str = "search",
    tool_input: dict[str, Any] | None = None,
    text: str = "Calling tool...",
) -> LLMResponse:
    return LLMResponse(
        content=[
            {"type": "text", "text": text},
            {"type": "tool_use", "name": tool_name, "input": tool_input or {}},
        ],
        stop_reason="tool_use",
        usage={"input_tokens": 15, "output_tokens": 10},
    )


class TestEstimateTokens:
    def test_estimate_empty_messages(self) -> None:
        result = estimate_tokens([])
        assert result == 0 or result >= 0

    def test_estimate_simple_messages(self) -> None:
        messages = [{"role": "user", "content": "Hello"}]
        result = estimate_tokens(messages)
        assert result > 0

    def test_estimate_scales_with_message_count(self) -> None:
        small = estimate_tokens([{"role": "user", "content": "Hi"}])
        large = estimate_tokens([{"role": "user", "content": "A" * 10000}])
        assert large > small

    def test_estimate_returns_integer(self) -> None:
        messages = [{"role": "user", "content": "Test message"}]
        result = estimate_tokens(messages)
        assert isinstance(result, int)

    def test_estimate_non_json_serializable_returns_zero(self) -> None:
        class BadObj:
            def __repr__(self) -> str:
                raise TypeError("cannot serialize")

        messages: list[dict[str, Any]] = [{"role": "user", "content": BadObj()}]
        with patch("testagent.agent.loop.logger"):
            result = estimate_tokens(messages)
        assert result == 0


class TestMicrocompact:
    def test_empty_messages(self) -> None:
        messages: list[dict[str, Any]] = []
        microcompact(messages)
        assert messages == []

    def test_strips_whitespace_string_content(self) -> None:
        messages = [{"role": "user", "content": "  hello world  \n"}]
        microcompact(messages)
        assert messages[0]["content"] == "hello world"

    def test_removes_empty_text_blocks(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "   "},
                    {"type": "text", "text": "valid text"},
                    {"type": "text", "text": ""},
                ],
            }
        ]
        microcompact(messages)
        assert len(messages[0]["content"]) == 1
        assert messages[0]["content"][0]["text"] == "valid text"  # type: ignore[index]

    def test_compacts_tool_use_blocks(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_123",
                        "name": "search",
                        "input": {"query": "test"},
                        "extra_field": "should_be_removed",
                    },
                ],
            }
        ]
        microcompact(messages)
        block = messages[0]["content"][0]
        assert block["type"] == "tool_use"  # type: ignore[index]
        assert block["name"] == "search"  # type: ignore[index]
        assert block["input"] == {"query": "test"}  # type: ignore[index]
        assert "id" not in block
        assert "extra_field" not in block

    def test_truncates_long_tool_result(self) -> None:
        long_content = "x" * 1000
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "name": "search", "content": long_content},
                ],
            }
        ]
        microcompact(messages)
        result_content = messages[0]["content"][0]["content"]  # type: ignore[index]
        assert len(result_content) <= 500
        assert result_content.endswith("...")

    def test_preserves_non_list_content(self) -> None:
        messages = [{"role": "system", "content": "system prompt"}]
        microcompact(messages)
        assert messages[0]["content"] == "system prompt"


class TestCompactToolBlock:
    def test_text_block_normal(self) -> None:
        block = {"type": "text", "text": "  hello  "}
        result = _compact_tool_block(block)
        assert result is not None
        assert result["text"] == "hello"

    def test_text_block_whitespace_only_returns_none(self) -> None:
        block = {"type": "text", "text": "   "}
        result = _compact_tool_block(block)
        assert result is None

    def test_tool_use_block_strips_extras(self) -> None:
        block = {
            "type": "tool_use",
            "id": "tc_1",
            "name": "run_test",
            "input": {"url": "http://example.com"},
            "display_name": "Run Test",
        }
        result = _compact_tool_block(block)
        assert result is not None
        assert result == {
            "type": "tool_use",
            "name": "run_test",
            "input": {"url": "http://example.com"},
        }

    def test_tool_result_long_content_truncated(self) -> None:
        block = {
            "type": "tool_result",
            "name": "search",
            "content": "x" * 600,
        }
        result = _compact_tool_block(block)
        assert result is not None
        assert len(str(result["content"])) <= 503

    def test_unknown_block_type_preserved(self) -> None:
        block = {"type": "custom_block", "data": "value"}
        result = _compact_tool_block(block)
        assert result == block


class TestAutoCompact:
    def test_messages_below_threshold_unchanged(self) -> None:
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "How are you?"},
        ]
        mock_llm = _make_mock_llm_provider([])
        result = auto_compact(messages, mock_llm, "system")
        assert len(result) == 3

    def test_compresses_large_message_list(self) -> None:
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "msg6"},
            {"role": "user", "content": "msg7"},
        ]
        mock_llm = _make_mock_llm_provider([])
        result = auto_compact(messages, mock_llm, "system")
        assert len(result) < len(messages)

    def test_compressed_contains_summary_block(self) -> None:
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
            {"role": "user", "content": "msg5"},
        ]
        mock_llm = _make_mock_llm_provider([])
        result = auto_compact(messages, mock_llm, "system")
        summary_messages = [
            m for m in result if isinstance(m.get("content"), str) and "Conversation Summary" in str(m["content"])
        ]
        assert len(summary_messages) == 1

    def test_preserves_tail_messages(self) -> None:
        tail_messages = [
            {"role": "user", "content": "important question"},
            {"role": "assistant", "content": "important answer"},
        ]
        messages = [
            {"role": "user", "content": "old1"},
            {"role": "assistant", "content": "old2"},
            {"role": "user", "content": "old3"},
            *tail_messages,
        ]
        mock_llm = _make_mock_llm_provider([])
        result = auto_compact(messages, mock_llm, "system")
        assert result[-2:] == tail_messages

    def test_returns_copy_for_very_short_messages(self) -> None:
        messages = [{"role": "user", "content": "only one"}]
        mock_llm = _make_mock_llm_provider([])
        result = auto_compact(messages, mock_llm, "system")
        assert result == messages
        assert result is not messages


class TestIdentityReInjection:
    def test_no_injection_when_messages_above_threshold(self) -> None:
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
            {"role": "user", "content": "msg5"},
        ]
        identity_re_injection("You are a tester", messages)
        assert len(messages) == 5

    def test_injects_identity_when_below_threshold(self) -> None:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "only message"},
        ]
        identity_re_injection("You are a test agent", messages)
        assert len(messages) == 2
        assert "Agent Identity" in str(messages[0].get("content", ""))

    def test_no_duplicate_injection(self) -> None:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "[Agent Identity]\nYou are a tester\n[End Identity]"},
            {"role": "assistant", "content": "I understand"},
        ]
        identity_re_injection("You are a test agent", messages)
        assert len(messages) == 2

    def test_injected_contains_system_directive(self) -> None:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "single message"},
        ]
        system = "You must always follow test protocol"
        identity_re_injection(system, messages)
        assert system in str(messages[0].get("content", ""))

    def test_injection_uses_user_role(self) -> None:
        messages: list[dict[str, Any]] = [
            {"role": "assistant", "content": "hello"},
        ]
        identity_re_injection("system directive", messages)
        assert messages[0]["role"] == "user"


class TestTOOLHANDLERS:
    def setup_method(self) -> None:
        TOOL_HANDLERS.clear()

    def test_is_mutable_dict(self) -> None:
        assert isinstance(TOOL_HANDLERS, dict)

    def test_register_adds_handler(self) -> None:
        async def handler(args: dict[str, object]) -> dict[str, object]:
            return {"ok": True}

        register_tool_handler("my_tool", handler)
        assert "my_tool" in TOOL_HANDLERS
        assert TOOL_HANDLERS["my_tool"] is handler

    def test_register_multiple_handlers(self) -> None:
        async def handler_a(args: dict[str, object]) -> dict[str, object]:
            return {"a": 1}

        async def handler_b(args: dict[str, object]) -> dict[str, object]:
            return {"b": 2}

        register_tool_handler("tool_a", handler_a)
        register_tool_handler("tool_b", handler_b)
        assert len(TOOL_HANDLERS) == 2


class TestRegisterToolHandler:
    def setup_method(self) -> None:
        TOOL_HANDLERS.clear()

    def test_overwrites_existing_handler(self) -> None:
        async def handler_old(args: dict[str, object]) -> dict[str, object]:
            return {"version": "old"}

        async def handler_new(args: dict[str, object]) -> dict[str, object]:
            return {"version": "new"}

        register_tool_handler("overwrite_tool", handler_old)
        register_tool_handler("overwrite_tool", handler_new)
        assert TOOL_HANDLERS["overwrite_tool"] is handler_new


class TestDispatchTool:
    def setup_method(self) -> None:
        TOOL_HANDLERS.clear()

    @pytest.mark.asyncio
    async def test_dispatch_to_registered_handler(self) -> None:
        async def search_handler(args: dict[str, object]) -> dict[str, object]:
            return {"result": f"found: {args.get('query', '')}"}

        register_tool_handler("search", search_handler)
        result = await dispatch_tool("search", {"query": "hello"})
        assert result == {"result": "found: hello"}

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        result = await dispatch_tool("nonexistent_tool", {"key": "value"})
        assert result["error"] == "Unknown tool: nonexistent_tool"
        assert result["tool_name"] == "nonexistent_tool"

    @pytest.mark.asyncio
    async def test_dispatch_with_no_handlers_registered(self) -> None:
        result = await dispatch_tool("any_tool", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_handler_receives_correct_input(self) -> None:
        received: dict[str, object] = {}

        async def capture_handler(args: dict[str, object]) -> dict[str, object]:
            received.update(args)
            return {"status": "ok"}

        register_tool_handler("capture", capture_handler)
        await dispatch_tool("capture", {"a": 1, "b": [2, 3]})
        assert received == {"a": 1, "b": [2, 3]}


class TestBuildSummaryText:
    def test_empty_messages(self) -> None:
        result = _build_summary_text([])
        assert "No messages" in result

    def test_summary_includes_role_and_content(self) -> None:
        messages = [
            {"role": "user", "content": "Run the API test"},
            {"role": "assistant", "content": "Starting test execution"},
        ]
        result = _build_summary_text(messages)
        assert "user:" in result
        assert "Run the API test" in result
        assert "assistant:" in result
        assert "Starting test execution" in result

    def test_handles_list_content(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will search"},
                    {"type": "tool_use", "name": "search", "input": {}},
                ],
            },
        ]
        result = _build_summary_text(messages)
        assert "I will search" in result
        assert "[tool:search]" in result

    def test_truncates_long_content(self) -> None:
        long_text = "A" * 500
        messages = [{"role": "user", "content": long_text}]
        result = _build_summary_text(messages)
        assert len(result.split("\n")[0]) < 300


class TestAgentLoopNormalExit:
    @pytest.mark.asyncio
    async def test_exits_when_stop_reason_is_not_tool_use(self) -> None:
        mock_llm = _make_mock_llm_provider(
            [
                _make_text_response("Test complete", stop_reason="end_turn"),
            ]
        )
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "Run the test"},
        ]
        result = await agent_loop(
            messages=messages,
            tools=[],
            system="You are a test agent",
            llm_provider=mock_llm,
        )
        assert mock_llm.chat.call_count == 1
        assert len(result) == 2
        assert result[-1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_returns_messages_with_assistant_response(self) -> None:
        mock_llm = _make_mock_llm_provider(
            [
                _make_text_response("Hello, how can I help?", stop_reason="end_turn"),
            ]
        )
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "Hi"},
        ]
        result = await agent_loop(
            messages=messages,
            tools=[],
            system="You are helpful",
            llm_provider=mock_llm,
        )
        assistant_msgs = [m for m in result if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        content = assistant_msgs[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"

    @pytest.mark.asyncio
    async def test_passes_tools_to_llm_provider(self) -> None:
        mock_llm = _make_mock_llm_provider(
            [
                _make_text_response("Done", stop_reason="end_turn"),
            ]
        )
        tools = [
            {"name": "search", "description": "Search the web", "parameters": {}},
        ]
        await agent_loop(
            messages=[{"role": "user", "content": "search for test"}],
            tools=tools,
            system="You are helpful",
            llm_provider=mock_llm,
        )
        call_kwargs = mock_llm.chat.call_args.kwargs
        assert call_kwargs["tools"] == tools

    @pytest.mark.asyncio
    async def test_passes_system_prompt_to_llm(self) -> None:
        mock_llm = _make_mock_llm_provider(
            [
                _make_text_response("Done", stop_reason="end_turn"),
            ]
        )
        system_prompt = "You are a test agent specialized in API testing"
        await agent_loop(
            messages=[{"role": "user", "content": "Run test"}],
            tools=[],
            system=system_prompt,
            llm_provider=mock_llm,
        )
        call_kwargs = mock_llm.chat.call_args.kwargs
        assert call_kwargs["system"] == system_prompt


class TestAgentLoopToolCalling:
    @pytest.mark.asyncio
    async def test_dispatches_tool_and_continues_loop(self) -> None:
        mock_llm = _make_mock_llm_provider(
            [
                _make_tool_use_response("search", {"query": "test"}),
                _make_text_response("Search results received", stop_reason="end_turn"),
            ]
        )

        dispatch_results = []

        async def dispatch_fn(name: str, input_data: dict[str, Any]) -> dict[str, Any]:
            dispatch_results.append((name, input_data))
            return {"result": f"found results for {input_data.get('query')}"}

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "Search for test"},
        ]
        result = await agent_loop(
            messages=messages,
            tools=[{"name": "search", "description": "Search"}],
            system="You are helpful",
            llm_provider=mock_llm,
            dispatch_fn=dispatch_fn,
        )
        assert mock_llm.chat.call_count == 2
        assert len(dispatch_results) == 1
        assert dispatch_results[0][0] == "search"
        assert dispatch_results[0][1] == {"query": "test"}
        user_msgs = [m for m in result if m["role"] == "user"]
        assert len(user_msgs) >= 2

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_single_response(self) -> None:
        mock_llm = _make_mock_llm_provider(
            [
                LLMResponse(
                    content=[
                        {"type": "tool_use", "name": "tool_a", "input": {"k": "v1"}},
                        {"type": "tool_use", "name": "tool_b", "input": {"k": "v2"}},
                    ],
                    stop_reason="tool_use",
                    usage={"input_tokens": 20, "output_tokens": 15},
                ),
                _make_text_response("All tools done", stop_reason="end_turn"),
            ]
        )

        calls: list[tuple[str, dict[str, object]]] = []

        async def dispatch_fn(name: str, input_data: dict[str, Any]) -> dict[str, Any]:
            calls.append((name, input_data))
            return {"status": "ok"}

        await agent_loop(
            messages=[{"role": "user", "content": "Run all tools"}],
            tools=[],
            system="You are helpful",
            llm_provider=mock_llm,
            dispatch_fn=dispatch_fn,
        )
        assert len(calls) == 2
        assert calls[0][0] == "tool_a"
        assert calls[1][0] == "tool_b"

    @pytest.mark.asyncio
    async def test_handles_tool_dispatch_error_gracefully(self) -> None:
        mock_llm = _make_mock_llm_provider(
            [
                _make_tool_use_response("broken_tool", {"arg": 1}),
                _make_text_response("Recovered from error", stop_reason="end_turn"),
            ]
        )

        async def dispatch_fn(name: str, input_data: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError(f"Tool {name} failed")

        result = await agent_loop(
            messages=[{"role": "user", "content": "Use broken tool"}],
            tools=[],
            system="You are helpful",
            llm_provider=mock_llm,
            dispatch_fn=dispatch_fn,
        )
        user_msgs = [m for m in result if m["role"] == "user"]
        tool_result_msg = user_msgs[-1]
        content = tool_result_msg["content"]
        assert isinstance(content, list)
        assert "error" in content[0]


class TestAgentLoopMaxRounds:
    @pytest.mark.asyncio
    async def test_exits_after_max_rounds(self) -> None:
        always_tool_use = _make_tool_use_response("search", {"q": "loop"})
        mock_llm = _make_mock_llm_provider([always_tool_use] * 100)

        async def dispatch_fn(name: str, input_data: dict[str, Any]) -> dict[str, Any]:
            return {"result": "found"}

        await agent_loop(
            messages=[{"role": "user", "content": "Search forever"}],
            tools=[],
            system="You are helpful",
            llm_provider=mock_llm,
            dispatch_fn=dispatch_fn,
            max_rounds=5,
        )
        assert mock_llm.chat.call_count == 5

    @pytest.mark.asyncio
    async def test_uses_default_max_rounds_50(self) -> None:
        always_tool_use = _make_tool_use_response("noop", {})
        mock_llm = _make_mock_llm_provider([always_tool_use] * 60)

        async def dispatch_fn(name: str, input_data: dict[str, Any]) -> dict[str, Any]:
            return {"status": "ok"}

        await agent_loop(
            messages=[{"role": "user", "content": "Loop"}],
            tools=[],
            system="You are helpful",
            llm_provider=mock_llm,
            dispatch_fn=dispatch_fn,
        )
        assert mock_llm.chat.call_count == 50

    @pytest.mark.asyncio
    async def test_no_tool_call_loop_with_max_rounds_one(self) -> None:
        mock_llm = _make_mock_llm_provider(
            [
                _make_text_response("Done", stop_reason="end_turn"),
            ]
        )
        result = await agent_loop(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            system="You are helpful",
            llm_provider=mock_llm,
            max_rounds=1,
        )
        assert mock_llm.chat.call_count == 1
        assert result[-1]["role"] == "assistant"


class TestAgentLoopCompression:
    @pytest.mark.asyncio
    async def test_microcompact_called_before_each_chat(self) -> None:
        mock_llm = _make_mock_llm_provider(
            [
                _make_text_response("First", stop_reason="end_turn"),
            ]
        )
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "  trim me  "},
        ]
        await agent_loop(
            messages=messages,
            tools=[],
            system="You are helpful",
            llm_provider=mock_llm,
        )
        assert messages[0].get("content") == "trim me"

    @pytest.mark.asyncio
    async def test_auto_compact_triggers_when_over_threshold(self) -> None:
        huge_chunk = "B" * 100000
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": huge_chunk},
            {"role": "assistant", "content": huge_chunk},
            {"role": "user", "content": huge_chunk},
            {"role": "assistant", "content": huge_chunk},
            {"role": "user", "content": huge_chunk},
            {"role": "assistant", "content": huge_chunk},
            {"role": "user", "content": huge_chunk},
        ]
        original_estimate = estimate_tokens(messages)

        mock_llm = _make_mock_llm_provider(
            [
                _make_text_response("Compressed response", stop_reason="end_turn"),
            ]
        )

        await agent_loop(
            messages=messages,
            tools=[],
            system="You are helpful",
            llm_provider=mock_llm,
            token_threshold=1000,
        )
        assert estimate_tokens(messages) < original_estimate


class TestAgentLoopEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_initial_messages(self) -> None:
        mock_llm = _make_mock_llm_provider(
            [
                _make_text_response("What would you like to test?", stop_reason="end_turn"),
            ]
        )
        result = await agent_loop(
            messages=[],
            tools=[],
            system="You are a test agent",
            llm_provider=mock_llm,
        )
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_default_dispatch_fn_called_when_none_provided(self) -> None:
        mock_llm = _make_mock_llm_provider(
            [
                _make_tool_use_response("unknown_tool", {"test": True}),
                _make_text_response("Done", stop_reason="end_turn"),
            ]
        )
        result = await agent_loop(
            messages=[{"role": "user", "content": "Use unknown tool"}],
            tools=[{"name": "unknown_tool", "description": "A test tool"}],
            system="You are helpful",
            llm_provider=mock_llm,
        )
        user_msgs = [m for m in result if m["role"] == "user"]
        tool_result = user_msgs[-1]["content"]
        assert isinstance(tool_result, list)
        assert "Unknown tool" in str(tool_result[0])


class TestCreateSkillTool:
    def test_returns_tool_definition_dict(self) -> None:
        registry = SkillRegistry()
        tool = create_skill_tool(registry)
        assert isinstance(tool, dict)
        assert tool["name"] == "load_skill"
        assert "description" in tool
        assert "input_schema" in tool

    def test_input_schema_requires_name(self) -> None:
        registry = SkillRegistry()
        tool = create_skill_tool(registry)
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert "name" in schema["properties"]
        assert schema["required"] == ["name"]

    def test_description_is_not_empty(self) -> None:
        registry = SkillRegistry()
        tool = create_skill_tool(registry)
        assert len(tool["description"]) > 20


class TestHandleLoadSkill:
    @pytest.mark.asyncio
    async def test_returns_skill_body_when_found(self) -> None:
        from testagent.models.skill import SkillDefinition

        registry = SkillRegistry()
        skill = SkillDefinition(
            name="api_smoke_test",
            version="1.0.0",
            description="API smoke test",
            trigger_pattern=r"api.*smoke",
            required_mcp_servers=["api_server"],
            required_rag_collections=[],
            body="## Steps\n\n1. Send GET request\n2. Validate response",
        )
        registry.register(skill)

        result = await handle_load_skill(registry, {"name": "api_smoke_test"})
        assert result["found"] is True
        assert result["name"] == "api_smoke_test"
        assert result["version"] == "1.0.0"
        assert result["description"] == "API smoke test"
        assert "Send GET request" in result["body"]

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_skill(self) -> None:
        registry = SkillRegistry()
        result = await handle_load_skill(registry, {"name": "nonexistent"})
        assert result["found"] is False
        assert "error" in result
        assert "nonexistent" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_error_for_empty_name(self) -> None:
        registry = SkillRegistry()
        result = await handle_load_skill(registry, {"name": ""})
        assert result["found"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_error_for_missing_name_param(self) -> None:
        registry = SkillRegistry()
        result = await handle_load_skill(registry, {})
        assert result["found"] is False
        assert "error" in result


class TestRegisterMCPTools:
    @pytest.mark.asyncio
    async def test_returns_empty_list_for_no_servers(self) -> None:
        registry = AsyncMock()
        registry.list_servers = AsyncMock(return_value=[])
        tools = await register_mcp_tools(registry)
        assert tools == []

    @pytest.mark.asyncio
    async def test_collects_tools_from_all_servers(self) -> None:
        server1 = MCPServerInfo(
            name="api_server",
            command="echo",
            tools=[
                {"name": "get_user", "description": "Get user by ID", "input_schema": {}},
                {"name": "list_users", "description": "List all users", "input_schema": {}},
            ],
        )
        server2 = MCPServerInfo(
            name="db_server",
            command="echo",
            tools=[
                {"name": "query_db", "description": "Execute SQL query", "input_schema": {}},
            ],
        )
        registry = AsyncMock()
        registry.list_servers = AsyncMock(return_value=[server1, server2])
        tools = await register_mcp_tools(registry)
        assert len(tools) == 3
        names = {t["name"] for t in tools}
        assert names == {"get_user", "list_users", "query_db"}

    @pytest.mark.asyncio
    async def test_each_tool_has_required_keys(self) -> None:
        server = MCPServerInfo(
            name="test_server",
            command="echo",
            tools=[
                {"name": "ping", "description": "Ping the server", "input_schema": {"type": "object"}},
            ],
        )
        registry = AsyncMock()
        registry.list_servers = AsyncMock(return_value=[server])
        tools = await register_mcp_tools(registry)
        assert len(tools) == 1
        tool = tools[0]
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool


class TestIntegrationRegisteredHandlerInAgentLoop:
    @pytest.mark.asyncio
    async def test_agent_loop_calls_registered_handler(self) -> None:
        TOOL_HANDLERS.clear()

        async def search_handler(args: dict[str, object]) -> dict[str, object]:
            return {"result": f"found: {args.get('q', '')}"}

        register_tool_handler("search", search_handler)

        mock_llm = _make_mock_llm_provider(
            [
                _make_tool_use_response("search", {"q": "test query"}),
                _make_text_response("Search done", stop_reason="end_turn"),
            ]
        )

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "Search for test query"},
        ]
        result = await agent_loop(
            messages=messages,
            tools=[{"name": "search", "description": "Search tool"}],
            system="You are helpful",
            llm_provider=mock_llm,
        )
        user_msgs = [m for m in result if m["role"] == "user"]
        tool_result = user_msgs[-1]["content"]
        assert isinstance(tool_result, list)
        assert "found: test query" in str(tool_result[0])

    @pytest.mark.asyncio
    async def test_agent_loop_unknown_tool_returns_error(self) -> None:
        TOOL_HANDLERS.clear()

        mock_llm = _make_mock_llm_provider(
            [
                _make_tool_use_response("unregistered_tool", {"x": 1}),
                _make_text_response("Done", stop_reason="end_turn"),
            ]
        )

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "Call unregistered tool"},
        ]
        result = await agent_loop(
            messages=messages,
            tools=[{"name": "unregistered_tool", "description": "Not registered"}],
            system="You are helpful",
            llm_provider=mock_llm,
        )
        user_msgs = [m for m in result if m["role"] == "user"]
        tool_result = user_msgs[-1]["content"]
        assert isinstance(tool_result, list)
        assert "Unknown tool: unregistered_tool" in str(tool_result[0])
