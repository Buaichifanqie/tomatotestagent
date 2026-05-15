from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.agent.analyzer import AnalyzerAgent
from testagent.agent.context import AgentType, AssembledContext, ContextAssembler
from testagent.agent.executor import ExecutorAgent
from testagent.agent.planner import PlannerAgent
from testagent.agent.todo import TodoItem, TodoManager
from testagent.config.settings import TestAgentSettings
from testagent.llm.base import ILLMProvider, LLMResponse


def _make_mock_llm_provider(responses: list[LLMResponse]) -> MagicMock:
    provider = MagicMock(spec=ILLMProvider)
    provider.chat = AsyncMock(side_effect=responses)
    provider.embed = AsyncMock(return_value=[0.1] * 10)
    provider.embed_batch = AsyncMock(return_value=[[0.1] * 10])
    return provider


def _make_text_response(text: str = "Done", stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        content=[{"type": "text", "text": text}],
        stop_reason=stop_reason,
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def _make_mock_assembled_context(
    system_prompt: str = "You are a test agent",
    tools: list[dict[str, Any]] | None = None,
    rag_context: list[str] | None = None,
) -> AssembledContext:
    return AssembledContext(
        system_prompt=system_prompt,
        tools=tools or [{"name": "mock_tool", "type": "mcp", "description": "Mock tool"}],
        rag_context=rag_context or [],
        skill_hints=[],
    )


class TestTodoItem:
    def test_default_construction(self) -> None:
        item = TodoItem(content="Test task")
        assert item.content == "Test task"
        assert item.status == "pending"
        assert item.priority == 0
        assert len(item.id) > 0

    def test_custom_construction(self) -> None:
        item = TodoItem(id="custom-id", content="High priority task", status="in_progress", priority=5)
        assert item.id == "custom-id"
        assert item.content == "High priority task"
        assert item.status == "in_progress"
        assert item.priority == 5

    def test_unique_ids(self) -> None:
        item1 = TodoItem(content="Task 1")
        item2 = TodoItem(content="Task 2")
        assert item1.id != item2.id

    def test_model_dump(self) -> None:
        item = TodoItem(id="test-id", content="Task", status="pending", priority=3)
        data = item.model_dump()
        assert data["id"] == "test-id"
        assert data["content"] == "Task"
        assert data["status"] == "pending"
        assert data["priority"] == 3


class TestTodoManagerAdd:
    def test_add_returns_id(self) -> None:
        mgr = TodoManager()
        todo_id = mgr.add("First task")
        assert isinstance(todo_id, str)
        assert len(todo_id) > 0

    def test_add_with_default_priority(self) -> None:
        mgr = TodoManager()
        todo_id = mgr.add("Default priority task")
        items = mgr.to_dict()["items"]
        added = [i for i in items if i["id"] == todo_id]
        assert len(added) == 1
        assert added[0]["priority"] == 0

    def test_add_with_custom_priority(self) -> None:
        mgr = TodoManager()
        todo_id = mgr.add("High priority task", priority=10)
        items = mgr.to_dict()["items"]
        added = [i for i in items if i["id"] == todo_id]
        assert added[0]["priority"] == 10

    def test_add_multiple_items(self) -> None:
        mgr = TodoManager()
        id1 = mgr.add("Task 1")
        id2 = mgr.add("Task 2")
        id3 = mgr.add("Task 3")
        assert id1 != id2 != id3
        assert len(mgr.to_dict()["items"]) == 3


class TestTodoManagerUpdate:
    def test_update_status_to_in_progress(self) -> None:
        mgr = TodoManager()
        todo_id = mgr.add("Task")
        mgr.update(todo_id, "in_progress")
        items = mgr.to_dict()["items"]
        updated = [i for i in items if i["id"] == todo_id]
        assert updated[0]["status"] == "in_progress"

    def test_update_status_to_completed(self) -> None:
        mgr = TodoManager()
        todo_id = mgr.add("Task")
        mgr.update(todo_id, "completed")
        items = mgr.to_dict()["items"]
        updated = [i for i in items if i["id"] == todo_id]
        assert updated[0]["status"] == "completed"

    def test_update_nonexistent_id_does_not_raise(self) -> None:
        mgr = TodoManager()
        mgr.update("nonexistent-id", "completed")

    def test_update_invalid_status_does_not_change(self) -> None:
        mgr = TodoManager()
        todo_id = mgr.add("Task")
        mgr.update(todo_id, "invalid_status")
        items = mgr.to_dict()["items"]
        updated = [i for i in items if i["id"] == todo_id]
        assert updated[0]["status"] == "pending"


class TestTodoManagerGetPending:
    def test_get_pending_returns_pending_items(self) -> None:
        mgr = TodoManager()
        mgr.add("Pending task 1")
        mgr.add("Pending task 2")
        pending = mgr.get_pending()
        assert len(pending) == 2
        assert all(item.status == "pending" for item in pending)

    def test_get_pending_excludes_completed(self) -> None:
        mgr = TodoManager()
        id1 = mgr.add("Will complete")
        mgr.add("Will stay pending")
        mgr.update(id1, "completed")
        pending = mgr.get_pending()
        assert len(pending) == 1
        assert pending[0].status == "pending"

    def test_get_pending_excludes_in_progress(self) -> None:
        mgr = TodoManager()
        id1 = mgr.add("In progress task")
        mgr.add("Pending task")
        mgr.update(id1, "in_progress")
        pending = mgr.get_pending()
        assert len(pending) == 1
        assert pending[0].status == "pending"

    def test_get_pending_empty_when_all_completed(self) -> None:
        mgr = TodoManager()
        todo_id = mgr.add("Only task")
        mgr.update(todo_id, "completed")
        assert mgr.get_pending() == []

    def test_get_pending_empty_when_no_items(self) -> None:
        mgr = TodoManager()
        assert mgr.get_pending() == []


class TestTodoManagerGetNext:
    def test_get_next_returns_highest_priority_pending(self) -> None:
        mgr = TodoManager()
        mgr.add("Low priority", priority=1)
        mgr.add("High priority", priority=10)
        mgr.add("Medium priority", priority=5)
        next_item = mgr.get_next()
        assert next_item is not None
        assert next_item.content == "High priority"
        assert next_item.priority == 10

    def test_get_next_returns_none_when_no_pending(self) -> None:
        mgr = TodoManager()
        todo_id = mgr.add("Only task")
        mgr.update(todo_id, "completed")
        assert mgr.get_next() is None

    def test_get_next_returns_none_when_empty(self) -> None:
        mgr = TodoManager()
        assert mgr.get_next() is None

    def test_get_next_skips_non_pending(self) -> None:
        mgr = TodoManager()
        id1 = mgr.add("In progress", priority=10)
        mgr.add("Pending low", priority=1)
        mgr.update(id1, "in_progress")
        next_item = mgr.get_next()
        assert next_item is not None
        assert next_item.content == "Pending low"


class TestTodoManagerFormatForPrompt:
    def test_format_empty_manager(self) -> None:
        mgr = TodoManager()
        result = mgr.format_for_prompt()
        assert "No tasks tracked" in result

    def test_format_shows_pending_marker(self) -> None:
        mgr = TodoManager()
        mgr.add("Pending task")
        result = mgr.format_for_prompt()
        assert "[ ]" in result
        assert "Pending task" in result

    def test_format_shows_in_progress_marker(self) -> None:
        mgr = TodoManager()
        todo_id = mgr.add("In progress task")
        mgr.update(todo_id, "in_progress")
        result = mgr.format_for_prompt()
        assert "[~]" in result
        assert "In progress task" in result

    def test_format_shows_completed_marker(self) -> None:
        mgr = TodoManager()
        todo_id = mgr.add("Completed task")
        mgr.update(todo_id, "completed")
        result = mgr.format_for_prompt()
        assert "[x]" in result
        assert "Completed task" in result

    def test_format_shows_priority(self) -> None:
        mgr = TodoManager()
        mgr.add("Task", priority=5)
        result = mgr.format_for_prompt()
        assert "P5" in result

    def test_format_includes_header(self) -> None:
        mgr = TodoManager()
        mgr.add("Task")
        result = mgr.format_for_prompt()
        assert "# Current Task Progress" in result

    def test_format_sorted_by_priority_descending(self) -> None:
        mgr = TodoManager()
        mgr.add("Low", priority=1)
        mgr.add("High", priority=10)
        result = mgr.format_for_prompt()
        high_pos = result.index("High")
        low_pos = result.index("Low")
        assert high_pos < low_pos


class TestTodoManagerToDict:
    def test_to_dict_empty(self) -> None:
        mgr = TodoManager()
        result = mgr.to_dict()
        assert result == {"items": []}

    def test_to_dict_with_items(self) -> None:
        mgr = TodoManager()
        mgr.add("Task 1", priority=3)
        mgr.add("Task 2", priority=1)
        result = mgr.to_dict()
        assert len(result["items"]) == 2
        assert all("id" in item for item in result["items"])
        assert all("content" in item for item in result["items"])
        assert all("status" in item for item in result["items"])
        assert all("priority" in item for item in result["items"])


def _make_mock_context_assembler(context: AssembledContext | None = None) -> MagicMock:
    assembler = MagicMock(spec=ContextAssembler)
    ctx = context or _make_mock_assembled_context()
    assembler.assemble = AsyncMock(return_value=ctx)
    return assembler


class TestPlannerAgent:
    @pytest.fixture
    def settings(self) -> TestAgentSettings:
        return TestAgentSettings()

    @pytest.mark.asyncio
    async def test_execute_returns_dict_with_plan(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider(
            [_make_text_response("Generated test plan with 5 tasks", stop_reason="end_turn")]
        )
        mock_assembler = _make_mock_context_assembler()
        agent = PlannerAgent(llm=mock_llm, context_assembler=mock_assembler)

        result = await agent.execute({"task_type": "plan", "requirement": "Login feature"})

        assert isinstance(result, dict)
        assert result["agent_type"] == "planner"
        assert "plan" in result
        assert result["message_count"] > 0

    @pytest.mark.asyncio
    async def test_execute_calls_context_assembler(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Plan generated", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = PlannerAgent(llm=mock_llm, context_assembler=mock_assembler)

        await agent.execute({"task_type": "plan"})

        mock_assembler.assemble.assert_awaited_once()
        call_kwargs = mock_assembler.assemble.call_args
        assert call_kwargs.kwargs["agent_type"] == AgentType.PLANNER

    @pytest.mark.asyncio
    async def test_execute_starts_with_empty_messages(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Plan", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = PlannerAgent(llm=mock_llm, context_assembler=mock_assembler)

        result = await agent.execute({"task_type": "plan"})

        assert result["message_count"] >= 2
        first_msg_call = mock_llm.chat.call_args
        messages_arg = first_msg_call.kwargs.get("messages") or first_msg_call.args[0] if first_msg_call.args else None
        if messages_arg is None:
            messages_arg = first_msg_call.kwargs["messages"]

        task_prompt_found = any(
            isinstance(m.get("content"), str) and "task_type" in m.get("content", "") for m in messages_arg
        )
        assert task_prompt_found

    @pytest.mark.asyncio
    async def test_execute_passes_rag_query(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Plan with RAG", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = PlannerAgent(llm=mock_llm, context_assembler=mock_assembler)

        await agent.execute({"task_type": "plan", "rag_query": "login API docs"})

        call_kwargs = mock_assembler.assemble.call_args.kwargs
        assert call_kwargs["rag_query"] == "login API docs"

    @pytest.mark.asyncio
    async def test_execute_agent_type_is_planner(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Plan", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = PlannerAgent(llm=mock_llm, context_assembler=mock_assembler)

        assert agent.AGENT_TYPE == AgentType.PLANNER
        assert agent.CONTEXT_WINDOW == 128_000

    @pytest.mark.asyncio
    async def test_execute_has_todo_manager(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Plan", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = PlannerAgent(llm=mock_llm, context_assembler=mock_assembler)

        assert isinstance(agent.todo, TodoManager)

    @pytest.mark.asyncio
    async def test_execute_includes_rag_context_in_messages(self, settings: TestAgentSettings) -> None:
        context_with_rag = _make_mock_assembled_context(rag_context=["Requirement doc content", "API specification"])
        mock_llm = _make_mock_llm_provider([_make_text_response("Plan with RAG context", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler(context_with_rag)
        agent = PlannerAgent(llm=mock_llm, context_assembler=mock_assembler)

        await agent.execute({"task_type": "plan"})

        first_call_messages = mock_llm.chat.call_args.kwargs["messages"]
        rag_msg_found = any(
            isinstance(m.get("content"), str) and "[RAG Context]" in m.get("content", "") for m in first_call_messages
        )
        assert rag_msg_found


class TestExecutorAgent:
    @pytest.fixture
    def settings(self) -> TestAgentSettings:
        return TestAgentSettings()

    @pytest.mark.asyncio
    async def test_execute_returns_dict_with_result(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Test execution completed", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = ExecutorAgent(llm=mock_llm, context_assembler=mock_assembler)

        result = await agent.execute({"task_type": "execute", "test_id": "TC-001"})

        assert isinstance(result, dict)
        assert result["agent_type"] == "executor"
        assert "result" in result
        assert result["message_count"] > 0

    @pytest.mark.asyncio
    async def test_execute_calls_context_assembler_with_executor_type(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Executed", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = ExecutorAgent(llm=mock_llm, context_assembler=mock_assembler)

        await agent.execute({"task_type": "execute"})

        call_kwargs = mock_assembler.assemble.call_args.kwargs
        assert call_kwargs["agent_type"] == AgentType.EXECUTOR

    @pytest.mark.asyncio
    async def test_execute_starts_with_empty_messages(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Execution result", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = ExecutorAgent(llm=mock_llm, context_assembler=mock_assembler)

        result = await agent.execute({"task_type": "execute"})

        assert result["message_count"] >= 2

    @pytest.mark.asyncio
    async def test_execute_agent_type_is_executor(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("OK", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = ExecutorAgent(llm=mock_llm, context_assembler=mock_assembler)

        assert agent.AGENT_TYPE == AgentType.EXECUTOR
        assert agent.CONTEXT_WINDOW == 32_000

    @pytest.mark.asyncio
    async def test_execute_has_todo_manager(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("OK", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = ExecutorAgent(llm=mock_llm, context_assembler=mock_assembler)

        assert isinstance(agent.todo, TodoManager)

    @pytest.mark.asyncio
    async def test_execute_passes_rag_query(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Done", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = ExecutorAgent(llm=mock_llm, context_assembler=mock_assembler)

        await agent.execute({"task_type": "execute", "rag_query": "locator library"})

        call_kwargs = mock_assembler.assemble.call_args.kwargs
        assert call_kwargs["rag_query"] == "locator library"

    @pytest.mark.asyncio
    async def test_execute_result_status_completed(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("All tests passed", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = ExecutorAgent(llm=mock_llm, context_assembler=mock_assembler)

        result = await agent.execute({"task_type": "execute"})
        assert result["result"]["status"] == "completed"


class TestAnalyzerAgent:
    @pytest.fixture
    def settings(self) -> TestAgentSettings:
        return TestAgentSettings()

    @pytest.mark.asyncio
    async def test_execute_returns_dict_with_analysis(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Failure classified as bug", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = AnalyzerAgent(llm=mock_llm, context_assembler=mock_assembler)

        result = await agent.execute({"task_type": "analyze", "failure_data": {}})

        assert isinstance(result, dict)
        assert result["agent_type"] == "analyzer"
        assert "analysis" in result
        assert result["message_count"] > 0

    @pytest.mark.asyncio
    async def test_execute_calls_context_assembler_with_analyzer_type(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Analyzed", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = AnalyzerAgent(llm=mock_llm, context_assembler=mock_assembler)

        await agent.execute({"task_type": "analyze"})

        call_kwargs = mock_assembler.assemble.call_args.kwargs
        assert call_kwargs["agent_type"] == AgentType.ANALYZER

    @pytest.mark.asyncio
    async def test_execute_starts_with_empty_messages(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Analysis result", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = AnalyzerAgent(llm=mock_llm, context_assembler=mock_assembler)

        result = await agent.execute({"task_type": "analyze"})

        assert result["message_count"] >= 2

    @pytest.mark.asyncio
    async def test_execute_agent_type_is_analyzer(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("OK", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = AnalyzerAgent(llm=mock_llm, context_assembler=mock_assembler)

        assert agent.AGENT_TYPE == AgentType.ANALYZER
        assert agent.CONTEXT_WINDOW == 64_000

    @pytest.mark.asyncio
    async def test_execute_has_todo_manager(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("OK", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = AnalyzerAgent(llm=mock_llm, context_assembler=mock_assembler)

        assert isinstance(agent.todo, TodoManager)

    @pytest.mark.asyncio
    async def test_execute_passes_rag_query(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("Done", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()
        agent = AnalyzerAgent(llm=mock_llm, context_assembler=mock_assembler)

        await agent.execute({"task_type": "analyze", "rag_query": "defect history"})

        call_kwargs = mock_assembler.assemble.call_args.kwargs
        assert call_kwargs["rag_query"] == "defect history"

    @pytest.mark.asyncio
    async def test_execute_analysis_contains_summary(self, settings: TestAgentSettings) -> None:
        mock_llm = _make_mock_llm_provider(
            [_make_text_response("Root cause: race condition in async handler", stop_reason="end_turn")]
        )
        mock_assembler = _make_mock_context_assembler()
        agent = AnalyzerAgent(llm=mock_llm, context_assembler=mock_assembler)

        result = await agent.execute({"task_type": "analyze"})
        assert "summary" in result["analysis"]
        assert "race condition" in result["analysis"]["summary"]


class TestAgentContextIsolation:
    @pytest.mark.asyncio
    async def test_planner_does_not_share_messages_with_executor(self) -> None:
        planner_llm = _make_mock_llm_provider([_make_text_response("Planner output", stop_reason="end_turn")])
        executor_llm = _make_mock_llm_provider([_make_text_response("Executor output", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()

        planner = PlannerAgent(llm=planner_llm, context_assembler=mock_assembler)
        executor = ExecutorAgent(llm=executor_llm, context_assembler=mock_assembler)

        await planner.execute({"task_type": "plan"})
        await executor.execute({"task_type": "execute"})

        planner_messages = planner_llm.chat.call_args.kwargs["messages"]
        executor_messages = executor_llm.chat.call_args.kwargs["messages"]

        planner_contents = [m.get("content", "") for m in planner_messages if isinstance(m.get("content"), str)]
        executor_contents = [m.get("content", "") for m in executor_messages if isinstance(m.get("content"), str)]

        assert not any("Planner output" in c for c in executor_contents)
        assert not any("Executor output" in c for c in planner_contents)

    @pytest.mark.asyncio
    async def test_each_agent_has_own_todo_manager(self) -> None:
        mock_llm = _make_mock_llm_provider([_make_text_response("OK", stop_reason="end_turn")])
        mock_assembler = _make_mock_context_assembler()

        planner = PlannerAgent(llm=mock_llm, context_assembler=mock_assembler)
        executor = ExecutorAgent(llm=mock_llm, context_assembler=mock_assembler)
        analyzer = AnalyzerAgent(llm=mock_llm, context_assembler=mock_assembler)

        planner.todo.add("Planner task")
        executor.todo.add("Executor task")
        analyzer.todo.add("Analyzer task")

        assert len(planner.todo.get_pending()) == 1
        assert len(executor.todo.get_pending()) == 1
        assert len(analyzer.todo.get_pending()) == 1

        assert planner.todo.get_next() is not None
        assert executor.todo.get_next() is not None
        assert analyzer.todo.get_next() is not None
        assert planner.todo.get_next().content == "Planner task"  # type: ignore[union-attr]
        assert executor.todo.get_next().content == "Executor task"  # type: ignore[union-attr]
        assert analyzer.todo.get_next().content == "Analyzer task"  # type: ignore[union-attr]
