from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from testagent.agent.context import AgentType, AssembledContext, ContextAssembler
from testagent.agent.loop import agent_loop
from testagent.agent.planner import PlannerAgent

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from testagent.config.settings import TestAgentSettings


@pytest.mark.asyncio
async def test_minimal_agent_session(
    test_settings: TestAgentSettings,
    mock_llm_provider: MagicMock,
) -> None:
    """Minimal Agent session integration test.

    1. Create TestAgentSettings (with mock LLM config)
    2. Create LLMProvider (mock returns fixed response)
    3. Create ContextAssembler
    4. Create PlannerAgent
    5. Execute agent_loop, verifying:
       - loop starts and exits normally
       - message format is correct
       - context assembly order is correct
    """
    context_assembler = ContextAssembler(settings=test_settings)

    agent = PlannerAgent(llm=mock_llm_provider, context_assembler=context_assembler)

    result = await agent.execute({"task_type": "plan", "requirement": "User login feature"})

    assert isinstance(result, dict), "execute() should return a dict"
    assert result["agent_type"] == "planner", "agent_type should be 'planner'"
    assert "plan" in result, "result should contain 'plan' key"
    assert result["message_count"] > 0, "should have at least one message"

    plan = result["plan"]
    assert isinstance(plan, dict), "plan should be a dict"
    assert "strategy" in plan, "plan should contain 'strategy'"
    assert "test_tasks" in plan, "plan should contain 'test_tasks'"


@pytest.mark.asyncio
async def test_agent_loop_normal_exit(mock_llm_provider: MagicMock) -> None:
    """Verify agent_loop starts and exits normally with correct message format."""
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Generate a test plan for login API"},
    ]

    result = await agent_loop(
        messages=messages,
        tools=[{"name": "rag_query", "type": "mcp", "description": "Knowledge base retrieval"}],
        system="You are the Planner Agent",
        llm_provider=mock_llm_provider,
    )

    assert isinstance(result, list), "agent_loop should return a list of messages"
    assert len(result) >= 2, "should contain at least initial user message + assistant response"

    assert result[-1]["role"] == "assistant", "last message should be from assistant"
    assistant_content = result[-1]["content"]
    assert isinstance(assistant_content, list), "assistant content should be a list of blocks"
    assert any(block.get("type") == "text" for block in assistant_content), (
        "assistant content should contain at least one text block"
    )

    user_messages = [m for m in result if m["role"] == "user"]
    assert len(user_messages) >= 1, "should retain at least one user message"

    assistant_messages = [m for m in result if m["role"] == "assistant"]
    assert len(assistant_messages) >= 1, "should have at least one assistant message"


@pytest.mark.asyncio
async def test_context_assembler_assemble_order(test_settings: TestAgentSettings) -> None:
    """Verify context assembly order: AGENTS -> SOUL -> TOOLS -> Skills -> RAG."""
    assembler = ContextAssembler(settings=test_settings)
    context = await assembler.assemble(agent_type=AgentType.PLANNER)

    assert isinstance(context, AssembledContext), "assemble() should return AssembledContext"
    assert context.system_prompt, "system_prompt should not be empty"
    assert context.tools, "tools list should not be empty"

    prompt = context.system_prompt

    agents_pos = prompt.find("# Agent Identity")
    soul_pos = prompt.find("# Behavioral Guidelines")
    tools_pos = prompt.find("# Available MCP Tools")

    assert agents_pos != -1, "system_prompt should contain '# Agent Identity' section"
    assert soul_pos != -1, "system_prompt should contain '# Behavioral Guidelines' section"
    assert tools_pos != -1, "system_prompt should contain '# Available MCP Tools' section"

    assert agents_pos < soul_pos, "Agent Identity should come before Behavioral Guidelines"
    assert soul_pos < tools_pos, "Behavioral Guidelines should come before Available MCP Tools"


@pytest.mark.asyncio
async def test_context_assembler_planner_identity(test_settings: TestAgentSettings) -> None:
    """Verify Planner Agent identity injection contains key information."""
    assembler = ContextAssembler(settings=test_settings)
    context = await assembler.assemble(agent_type=AgentType.PLANNER)

    prompt = context.system_prompt
    assert "Planner Agent" in prompt, "system_prompt should identify as Planner Agent"
    assert "128K" in prompt, "system_prompt should mention 128K context window"

    tool_names = [t["name"] for t in context.tools]
    assert "jira_server" in tool_names, "Planner should have jira_server tool"
    assert "git_server" in tool_names, "Planner should have git_server tool"
    assert "rag_query" in tool_names, "Planner should have rag_query tool"


@pytest.mark.asyncio
async def test_agent_loop_with_tool_use(mock_tool_use_llm_provider: MagicMock) -> None:
    """Verify agent_loop continues and exits normally after tool calls."""
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Plan login API tests"},
    ]

    result = await agent_loop(
        messages=messages,
        tools=[{"name": "rag_query", "type": "mcp", "description": "Knowledge base retrieval"}],
        system="You are the Planner Agent",
        llm_provider=mock_tool_use_llm_provider,
    )

    assert mock_tool_use_llm_provider.chat.call_count == 2, "LLM should be called twice (tool_use + end_turn)"

    assert len(result) >= 3, "should have user msg + tool_use assistant + tool result + final assistant"

    tool_result_msgs = [m for m in result if m["role"] == "user" and isinstance(m.get("content"), list)]
    assert len(tool_result_msgs) >= 1, "should contain tool result messages"

    final_assistant = [m for m in result if m["role"] == "assistant"][-1]
    final_content = final_assistant["content"]
    assert isinstance(final_content, list), "final assistant content should be a list"
    assert any(block.get("type") == "text" for block in final_content), "final assistant content should contain text"


@pytest.mark.asyncio
async def test_planner_agent_context_isolation(
    test_settings: TestAgentSettings,
    mock_llm_provider: MagicMock,
) -> None:
    """Verify Planner Agent starts with empty messages for context isolation."""
    context_assembler = ContextAssembler(settings=test_settings)
    agent = PlannerAgent(llm=mock_llm_provider, context_assembler=context_assembler)

    result = await agent.execute({"task_type": "plan", "requirement": "Feature X"})

    call_kwargs = mock_llm_provider.chat.call_args.kwargs
    messages_passed = call_kwargs.get("messages", [])

    task_prompt_found = any(
        isinstance(m.get("content"), str) and "task_type" in m.get("content", "") for m in messages_passed
    )
    assert task_prompt_found, "first message should contain the task prompt"

    system_passed = call_kwargs.get("system", "")
    assert "# Agent Identity" in system_passed, "system prompt should contain Agent Identity section"
    assert "# Behavioral Guidelines" in system_passed, "system prompt should contain Behavioral Guidelines section"
    assert "# Available MCP Tools" in system_passed, "system prompt should contain Available MCP Tools section"

    assert result["agent_type"] == "planner"
    assert "plan" in result
    assert result["message_count"] >= 2, "should have task prompt + assistant response"


@pytest.mark.asyncio
async def test_full_pipeline_settings_to_agent_result(
    test_settings: TestAgentSettings,
    mock_llm_provider: MagicMock,
    async_db_session: Any,
) -> None:
    """Full pipeline integration: Settings -> ContextAssembler -> PlannerAgent -> agent_loop -> result."""
    context_assembler = ContextAssembler(settings=test_settings)
    agent = PlannerAgent(llm=mock_llm_provider, context_assembler=context_assembler)

    result = await agent.execute(
        {
            "task_type": "plan",
            "requirement": "E2E test for user registration flow",
            "rag_query": "registration API docs",
        }
    )

    assert isinstance(result, dict)
    assert result["agent_type"] == "planner"
    assert "plan" in result
    assert "strategy" in result["plan"]
    assert result["message_count"] > 0

    mock_llm_provider.chat.assert_awaited()
    call_kwargs = mock_llm_provider.chat.call_args.kwargs
    assert "tools" in call_kwargs, "chat should receive tools"
    assert isinstance(call_kwargs["tools"], list), "tools should be a list"
    assert len(call_kwargs["tools"]) > 0, "Planner should have at least one tool"
