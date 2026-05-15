from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import testagent.agent.loop as loop_module
from testagent.agent.loop import agent_loop, dispatch_tool, register_tool_handler
from testagent.agent.tools import handle_load_skill
from testagent.gateway.mcp_registry import MCPRegistry
from testagent.llm.base import ILLMProvider, LLMResponse
from testagent.models.skill import SkillDefinition
from testagent.skills.parser import MarkdownParser
from testagent.skills.registry import SkillRegistry
from testagent.skills.validator import SkillValidator


@pytest.mark.asyncio
async def test_agent_calls_api_tool() -> None:
    """
    验证 Agent Loop 可调用 API MCP 工具并记录审计日志。

    1. 创建 Mock LLM Provider(模拟返回 tool_use 响应)
    2. 通过 dispatch_fn 注册模拟 API MCP Server 工具
    3. Agent Loop 接收到 tool_use → 调用 api_request → 返回结果
    4. 验证工具调用经过审计日志记录
    """
    dispatch_records: list[dict[str, Any]] = []

    async def _mock_api_dispatch(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        dispatch_records.append({"tool_name": tool_name, "tool_input": tool_input})
        if tool_name == "api_request":
            return {"status": "ok", "code": 200, "body": '{"health": "pass"}'}
        return {"error": f"Unknown tool: {tool_name}"}

    mock_llm = MagicMock(spec=ILLMProvider)
    mock_llm.chat = AsyncMock(
        side_effect=[
            LLMResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": "call_001",
                        "name": "api_request",
                        "input": {"method": "GET", "url": "/api/health"},
                    }
                ],
                stop_reason="tool_use",
                usage={"input_tokens": 15, "output_tokens": 8},
            ),
            LLMResponse(
                content=[{"type": "text", "text": "API health check passed: status 200 OK."}],
                stop_reason="end_turn",
                usage={"input_tokens": 30, "output_tokens": 12},
            ),
        ]
    )
    mock_llm.embed = AsyncMock(return_value=[0.1] * 10)
    mock_llm.embed_batch = AsyncMock(return_value=[[0.1] * 10])

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Check API health status"},
    ]

    with patch.object(loop_module.logger, "debug") as mock_debug:
        result = await agent_loop(
            messages=messages,
            tools=[{"name": "api_request", "description": "Send an API request"}],
            system="You are the Executor Agent. Use tools to execute tests.",
            llm_provider=mock_llm,
            dispatch_fn=_mock_api_dispatch,
        )

    assert mock_llm.chat.call_count == 2, f"Expected 2 LLM calls (tool_use + end_turn), got {mock_llm.chat.call_count}"

    assert len(dispatch_records) == 1, f"Expected 1 tool dispatch, got {len(dispatch_records)}"
    record = dispatch_records[0]
    assert record["tool_name"] == "api_request"
    assert record["tool_input"]["method"] == "GET"
    assert record["tool_input"]["url"] == "/api/health"

    assert isinstance(result, list), "agent_loop should return a list of messages"
    assert len(result) >= 4, (
        f"Expected >= 4 messages (user + tool_use assistant + tool_result user + final assistant), got {len(result)}"
    )

    tool_result_msgs = [m for m in result if m["role"] == "user" and isinstance(m.get("content"), list)]
    assert len(tool_result_msgs) >= 1, "should contain tool result messages"

    final_assistant = [m for m in result if m["role"] == "assistant"][-1]
    final_content = final_assistant["content"]
    assert isinstance(final_content, list)
    assert any(block.get("type") == "text" and "status 200" in block.get("text", "") for block in final_content), (
        "final assistant response should reference the API result"
    )

    agent_loop_calls = [
        args[0]
        for args, _ in mock_debug.call_args_list
        if args and isinstance(args[0], str) and "Agent loop round completed" in args[0]
    ]
    assert len(agent_loop_calls) >= 2, (
        f"Expected >= 2 'Agent loop round completed' debug log entries, got {len(agent_loop_calls)}"
    )
    tool_use_rounds = [
        call
        for call in mock_debug.call_args_list
        if call.args
        and isinstance(call.args[0], str)
        and "Agent loop round completed" in call.args[0]
        and call.kwargs.get("extra", {}).get("extra_data", {}).get("stop_reason") == "tool_use"
    ]
    assert len(tool_use_rounds) >= 1, "Expected at least one audit log entry with stop_reason='tool_use'"


@pytest.mark.asyncio
async def test_agent_calls_tool_via_registered_handler() -> None:
    """验证通过 register_tool_handler 注册的工具可被 agent_loop 调用并记录审计日志。"""
    call_record: list[dict[str, Any]] = []

    async def _handle_api_request(tool_input: dict[str, Any]) -> dict[str, Any]:
        call_record.append(tool_input)
        return {"status_code": 200, "body": '{"result": "success"}'}

    register_tool_handler("api_request", _handle_api_request)

    mock_llm = MagicMock(spec=ILLMProvider)
    mock_llm.chat = AsyncMock(
        side_effect=[
            LLMResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": "call_002",
                        "name": "api_request",
                        "input": {"method": "POST", "url": "/api/login"},
                    }
                ],
                stop_reason="tool_use",
                usage={"input_tokens": 15, "output_tokens": 8},
            ),
            LLMResponse(
                content=[{"type": "text", "text": "Login API test passed."}],
                stop_reason="end_turn",
                usage={"input_tokens": 25, "output_tokens": 8},
            ),
        ]
    )
    mock_llm.embed = AsyncMock(return_value=[0.1] * 10)
    mock_llm.embed_batch = AsyncMock(return_value=[[0.1] * 10])

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Test login API endpoint"},
    ]

    result = await agent_loop(
        messages=messages,
        tools=[{"name": "api_request", "description": "Send API request"}],
        system="You are the Executor Agent.",
        llm_provider=mock_llm,
        dispatch_fn=dispatch_tool,
    )

    assert len(call_record) == 1, f"Expected 1 handler invocation, got {len(call_record)}"
    assert call_record[0]["method"] == "POST"
    assert call_record[0]["url"] == "/api/login"

    assert mock_llm.chat.call_count == 2

    final_assistant = [m for m in result if m["role"] == "assistant"][-1]
    final_content = final_assistant["content"]
    assert any(
        "Login API test passed" in block.get("text", "")
        for block in final_content
        if isinstance(block, dict) and block.get("type") == "text"
    ), "final response should reference the login test"


@pytest.mark.asyncio
async def test_skill_two_layer_injection() -> None:
    """
    验证 Skill 两层注入机制。

    1. 创建测试 SKILL.md 文件(内容字符串)
    2. MarkdownParser 解析为 meta + body
    3. SkillRegistry 注册 SkillDefinition
    4. Layer 1: get_descriptions() 返回短描述(~100 tokens/skill)
    5. Layer 2: handle_load_skill("test_skill") 返回完整正文
    """
    skill_md = """---
name: test_skill
version: "1.0.0"
description: 集成测试 Skill,验证核心两层注入流程
trigger: "test.*skill|integration.*test"
required_mcp_servers:
  - mock_api_server
required_rag_collections:
  - mock_docs
---

## 目标

对两层注入机制执行集成测试,验证 Skill 正确解析、注册和加载。

## 操作流程

1. 使用 MarkdownParser 解析 SKILL.md 格式内容
2. 创建 SkillDefinition 并注册到 SkillRegistry
3. 验证 Layer 1 短描述注入
4. 验证 Layer 2 完整正文加载

## 断言策略

- Layer 1 描述包含 skill name,version,description
- Layer 2 返回完整 body,包含所有 Markdown 章节

## 失败处理

- 解析失败:阻断测试
- 注入缺失:标记为 Skill Engine 缺陷
"""
    parser = MarkdownParser()
    meta, body = parser.parse(skill_md)

    assert meta["name"] == "test_skill"
    assert meta["version"] == "1.0.0"
    assert "## 目标" in body
    assert "## 操作流程" in body
    assert "## 断言策略" in body
    assert "## 失败处理" in body

    skill = SkillDefinition(
        name=str(meta["name"]),
        version=str(meta["version"]),
        description=str(meta["description"]),
        trigger_pattern=str(meta.get("trigger", "")),
        required_mcp_servers=list(meta.get("required_mcp_servers", [])),  # type: ignore[call-overload]
        required_rag_collections=list(meta.get("required_rag_collections", [])),  # type: ignore[call-overload]
        body=body,
    )

    registry = SkillRegistry()
    assert registry.count() == 0
    registry.register(skill)
    assert registry.count() == 1

    descriptions = registry.get_descriptions()
    assert "test_skill" in descriptions, (
        f"Layer 1 descriptions should contain skill name 'test_skill', got: {descriptions}"
    )
    assert "v1.0.0" in descriptions, f"Layer 1 descriptions should contain version 'v1.0.0', got: {descriptions}"
    assert "集成测试 Skill" in descriptions, (
        f"Layer 1 descriptions should contain description text, got: {descriptions}"
    )
    assert "[trigger:" in descriptions, f"Layer 1 descriptions should contain trigger pattern hint, got: {descriptions}"

    loaded = await handle_load_skill(registry, {"name": "test_skill"})
    assert loaded["found"] is True, f"Layer 2 load_skill should find 'test_skill', got: {loaded}"
    assert loaded["name"] == "test_skill"
    assert loaded["version"] == "1.0.0"
    assert loaded["description"] == "集成测试 Skill,验证核心两层注入流程"
    assert loaded["trigger_pattern"] == "test.*skill|integration.*test"
    assert "## 目标" in loaded["body"], f"Layer 2 body should contain '## 目标' section, got: {loaded['body'][:200]}"
    assert "## 操作流程" in loaded["body"]
    assert "## 断言策略" in loaded["body"]
    assert "## 失败处理" in loaded["body"]

    not_found = await handle_load_skill(registry, {"name": "nonexistent"})
    assert not_found["found"] is False, "load_skill for unknown skill should return found=False"
    assert "Available skills" in not_found.get("error", ""), "error should list available skills when skill not found"

    by_name = registry.get_by_name("test_skill")
    assert by_name is not None, "get_by_name should return the registered skill"
    assert by_name.name == "test_skill"
    assert by_name.body == body

    all_skills = registry.list_all()
    assert len(all_skills) == 1
    assert all_skills[0].name == "test_skill"


def test_degraded_skill_handling() -> None:
    """
    验证 Skill 降级处理。

    1. 创建一个 required_mcp_servers 包含未注册 Server 的 Skill meta
    2. 通过 SkillValidator 校验
    3. 验证 Skill 被标记为 degraded
    4. 验证 Skill 仍可加载但提示降级状态(ValidationResult.warnings)
    """
    mock_mcp_registry = MagicMock(spec=MCPRegistry)
    mock_mcp_registry.is_registered = MagicMock(return_value=False)

    validator = SkillValidator(mcp_registry=mock_mcp_registry)

    valid_meta: dict[str, object] = {
        "name": "degraded_test_skill",
        "version": "1.0.0",
        "description": "A skill that depends on an unregistered MCP server",
        "trigger": "degraded.*test",
        "required_mcp_servers": ["nonexistent_mcp_server"],
        "required_rag_collections": ["test_collection"],
    }
    valid_result = validator.validate(valid_meta)
    assert valid_result.valid is True, (
        f"Skill with missing MCP server should still be valid (degraded not invalid), errors: {valid_result.errors}"
    )
    assert valid_result.degraded is True, "Skill with unregistered required_mcp_servers should be marked as degraded"
    assert len(valid_result.warnings) >= 1, (
        f"Expected at least 1 warning for unregistered MCP server, got: {valid_result.warnings}"
    )

    warning_contains_server = any("nonexistent_mcp_server" in w for w in valid_result.warnings)
    assert warning_contains_server, (
        f"Warning should mention the unregistered server 'nonexistent_mcp_server', got: {valid_result.warnings}"
    )

    warning_contains_degraded = any("degraded" in w.lower() for w in valid_result.warnings)
    assert warning_contains_degraded, f"Warning should mention 'degraded' status, got: {valid_result.warnings}"

    registered_mock_registry = MagicMock(spec=MCPRegistry)
    registered_mock_registry.is_registered = MagicMock(return_value=True)
    registered_validator = SkillValidator(mcp_registry=registered_mock_registry)

    healthy_meta: dict[str, object] = {
        "name": "healthy_skill",
        "version": "2.0.0",
        "description": "A skill with all MCP servers registered",
        "trigger": "healthy.*test",
        "required_mcp_servers": ["registered_server"],
        "required_rag_collections": ["test_collection"],
    }
    healthy_result = registered_validator.validate(healthy_meta)
    assert healthy_result.valid is True
    assert healthy_result.degraded is False, "Skill with all registered MCP servers should not be degraded"
    assert len(healthy_result.warnings) == 0, (
        f"Skill with all registered MCP servers should have no warnings, got: {healthy_result.warnings}"
    )

    invalid_meta: dict[str, object] = {
        "name": "",
        "version": "",
        "description": "",
        "trigger": "",
        "required_mcp_servers": [],
        "required_rag_collections": [],
    }
    invalid_result = validator.validate(invalid_meta)
    assert invalid_result.valid is False, "Skill with all required fields empty should be invalid"
    assert len(invalid_result.errors) > 0, (
        f"Should have validation errors for empty required fields, got: {invalid_result.errors}"
    )
