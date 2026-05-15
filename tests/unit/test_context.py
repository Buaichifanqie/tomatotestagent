from __future__ import annotations

from dataclasses import fields
from typing import Any

import pytest

from testagent.agent.context import AgentType, AssembledContext, ContextAssembler
from testagent.config.settings import TestAgentSettings
from testagent.rag.pipeline import RAGPipeline, RAGResult


class MockRAGPipeline(RAGPipeline):
    __test__ = False

    def __init__(self) -> None:
        pass

    async def query(
        self,
        query_text: str,
        collection: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[RAGResult]:
        _ = query_text, filters
        results_map: dict[str, list[RAGResult]] = {
            "req_docs": [
                RAGResult(doc_id="req-001", content="User login flow requirements", score=0.95),
                RAGResult(doc_id="req-002", content="OAuth2 authentication spec", score=0.88),
            ],
            "api_docs": [
                RAGResult(doc_id="api-001", content="POST /api/v2/login endpoint", score=0.92),
            ],
            "defect_history": [
                RAGResult(doc_id="def-001", content="Login timeout on slow network", score=0.85),
                RAGResult(doc_id="def-002", content="Session token refresh bug", score=0.78),
                RAGResult(doc_id="def-003", content="Rate limiting too aggressive", score=0.71),
            ],
            "test_reports": [
                RAGResult(doc_id="rpt-001", content="Regression run #42: 3 failures", score=0.90),
            ],
            "locator_library": [
                RAGResult(doc_id="loc-001", content="#login-button CSS selector", score=0.96),
                RAGResult(doc_id="loc-002", content=".username-input XPath locator", score=0.89),
            ],
            "failure_patterns": [
                RAGResult(doc_id="fp-001", content="Timeout failures on CI runners", score=0.82),
            ],
        }
        return results_map.get(collection, [])


class MockRAGPipelineFailing(MockRAGPipeline):
    __test__ = False

    async def query(
        self,
        query_text: str,
        collection: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[RAGResult]:
        msg = f"RAG query failed for collection: {collection}"
        raise RuntimeError(msg)


class TestAgentType:
    def test_enum_values(self) -> None:
        assert AgentType.PLANNER.value == "planner"
        assert AgentType.EXECUTOR.value == "executor"
        assert AgentType.ANALYZER.value == "analyzer"

    def test_enum_members(self) -> None:
        assert set(AgentType.__members__) == {"PLANNER", "EXECUTOR", "ANALYZER"}

    def test_enum_values_are_strings(self) -> None:
        assert isinstance(AgentType.PLANNER.value, str)
        assert isinstance(AgentType.EXECUTOR.value, str)
        assert isinstance(AgentType.ANALYZER.value, str)


class TestAssembledContext:
    def test_default_construction(self) -> None:
        ctx = AssembledContext()
        assert ctx.system_prompt == ""
        assert ctx.tools == []
        assert ctx.rag_context == []
        assert ctx.skill_hints == []

    def test_partial_construction(self) -> None:
        ctx = AssembledContext(
            system_prompt="test prompt",
            tools=[{"name": "tool1"}],
        )
        assert ctx.system_prompt == "test prompt"
        assert ctx.tools == [{"name": "tool1"}]
        assert ctx.rag_context == []
        assert ctx.skill_hints == []

    def test_full_construction(self) -> None:
        ctx = AssembledContext(
            system_prompt="full prompt",
            tools=[{"name": "t1"}, {"name": "t2"}],
            rag_context=["doc1", "doc2"],
            skill_hints=[{"name": "s1", "description": "desc1"}],
        )
        assert ctx.system_prompt == "full prompt"
        assert len(ctx.tools) == 2
        assert len(ctx.rag_context) == 2
        assert len(ctx.skill_hints) == 1

    def test_dataclass_fields(self) -> None:
        field_names = {f.name for f in fields(AssembledContext)}
        assert field_names == {"system_prompt", "tools", "rag_context", "skill_hints"}

    def test_immutable_by_default(self) -> None:
        ctx = AssembledContext()
        ctx.system_prompt = "modified"
        assert ctx.system_prompt == "modified"


class TestContextAssembler:
    @pytest.fixture
    def settings(self) -> TestAgentSettings:
        return TestAgentSettings()

    @pytest.fixture
    def assembler(self, settings: TestAgentSettings) -> ContextAssembler:
        return ContextAssembler(settings)

    @pytest.fixture
    def rag_assembler(self, settings: TestAgentSettings) -> ContextAssembler:
        return ContextAssembler(settings, rag_pipeline=MockRAGPipeline())

    @pytest.fixture
    def failing_rag_assembler(self, settings: TestAgentSettings) -> ContextAssembler:
        return ContextAssembler(settings, rag_pipeline=MockRAGPipelineFailing())

    @pytest.mark.asyncio
    async def test_assemble_returns_assembled_context(
        self,
        assembler: ContextAssembler,
    ) -> None:
        result = await assembler.assemble(AgentType.PLANNER)
        assert isinstance(result, AssembledContext)

    @pytest.mark.asyncio
    async def test_assemble_system_prompt_not_empty(
        self,
        assembler: ContextAssembler,
    ) -> None:
        for agent_type in AgentType:
            result = await assembler.assemble(agent_type)
            assert result.system_prompt, f"system_prompt should not be empty for {agent_type}"

    @pytest.mark.asyncio
    async def test_assemble_assembly_order(
        self,
        assembler: ContextAssembler,
    ) -> None:
        result = await assembler.assemble(AgentType.PLANNER)
        prompt = result.system_prompt

        agent_pos = prompt.index("# Agent Identity")
        soul_pos = prompt.index("# Behavioral Guidelines")
        tools_pos = prompt.index("# Available MCP Tools")

        assert agent_pos < soul_pos < tools_pos, (
            f"Expected AGENTS -> SOUL -> TOOLS in order, got AGENTS={agent_pos}, SOUL={soul_pos}, TOOLS={tools_pos}"
        )

    @pytest.mark.asyncio
    async def test_assemble_sections_are_separated(
        self,
        assembler: ContextAssembler,
    ) -> None:
        result = await assembler.assemble(AgentType.EXECUTOR)
        assert "\n\n" in result.system_prompt

    @pytest.mark.asyncio
    async def test_assemble_rag_context_empty_by_default(
        self,
        assembler: ContextAssembler,
    ) -> None:
        result = await assembler.assemble(AgentType.ANALYZER)
        assert result.rag_context == []

    @pytest.mark.asyncio
    async def test_assemble_skill_hints_empty_by_default(
        self,
        assembler: ContextAssembler,
    ) -> None:
        result = await assembler.assemble(AgentType.PLANNER)
        assert result.skill_hints == []

    @pytest.mark.asyncio
    async def test_assemble_tools_list(
        self,
        assembler: ContextAssembler,
    ) -> None:
        planner_result = await assembler.assemble(AgentType.PLANNER)
        assert len(planner_result.tools) > 0
        assert all(isinstance(t, dict) for t in planner_result.tools)

        executor_result = await assembler.assemble(AgentType.EXECUTOR)
        assert len(executor_result.tools) > 0

        analyzer_result = await assembler.assemble(AgentType.ANALYZER)
        assert len(analyzer_result.tools) > 0

    @pytest.mark.asyncio
    async def test_assemble_with_rag_query_no_pipeline(
        self,
        assembler: ContextAssembler,
    ) -> None:
        result = await assembler.assemble(
            AgentType.PLANNER,
            rag_query="test query",
        )
        assert isinstance(result, AssembledContext)
        assert result.rag_context == []
        assert "# Retrieved Knowledge" not in result.system_prompt

    def test_build_agents_section_planner(self, assembler: ContextAssembler) -> None:
        section = assembler._build_agents_section(AgentType.PLANNER)
        assert "# Agent Identity" in section
        assert "Planner Agent" in section
        assert "test planning specialist" in section
        assert "128K context window" in section
        assert "highest priority" in section

    def test_build_agents_section_executor(self, assembler: ContextAssembler) -> None:
        section = assembler._build_agents_section(AgentType.EXECUTOR)
        assert "# Agent Identity" in section
        assert "Executor Agent" in section
        assert "test execution specialist" in section
        assert "32K context window" in section
        assert "self-healing" in section

    def test_build_agents_section_analyzer(self, assembler: ContextAssembler) -> None:
        section = assembler._build_agents_section(AgentType.ANALYZER)
        assert "# Agent Identity" in section
        assert "Analyzer Agent" in section
        assert "test analysis specialist" in section
        assert "64K context window" in section
        assert "root cause analysis" in section

    def test_build_soul_section_planner(self, assembler: ContextAssembler) -> None:
        section = assembler._build_soul_section(AgentType.PLANNER)
        assert "# Behavioral Guidelines" in section
        assert "maximum test coverage" in section
        assert "high-risk modules" in section
        assert "planning → executing → analyzing" in section
        assert "requirement docs" in section

    def test_build_soul_section_executor(self, assembler: ContextAssembler) -> None:
        section = assembler._build_soul_section(AgentType.EXECUTOR)
        assert "# Behavioral Guidelines" in section
        assert "self-healing" in section
        assert "timeout constraints" in section
        assert "isolated sandbox" in section

    def test_build_soul_section_analyzer(self, assembler: ContextAssembler) -> None:
        section = assembler._build_soul_section(AgentType.ANALYZER)
        assert "# Behavioral Guidelines" in section
        assert "bug / flaky / environment / configuration" in section
        assert "critical / major / minor / trivial" in section
        assert "root cause" in section

    def test_build_soul_section_common_principles(
        self,
        assembler: ContextAssembler,
    ) -> None:
        for agent_type in AgentType:
            section = assembler._build_soul_section(agent_type)
            assert "API keys" in section
            assert "PII" in section
            assert "Gateway message protocol" in section
            assert "empty initial message history" in section

    def test_build_tools_section_planner(self, assembler: ContextAssembler) -> None:
        section = assembler._build_tools_section(AgentType.PLANNER)
        assert "# Available MCP Tools" in section
        assert "Jira Server" in section
        assert "Git Server" in section
        assert "Strategy Skills" in section
        assert "RAG Query" in section
        assert "Phase 3" in section

    def test_build_tools_section_executor(self, assembler: ContextAssembler) -> None:
        section = assembler._build_tools_section(AgentType.EXECUTOR)
        assert "# Available MCP Tools" in section
        assert "Playwright Server" in section
        assert "API Server" in section
        assert "Harness Runner" in section

    def test_build_tools_section_analyzer(self, assembler: ContextAssembler) -> None:
        section = assembler._build_tools_section(AgentType.ANALYZER)
        assert "# Available MCP Tools" in section
        assert "Jira Server" in section
        assert "Git Server" in section
        assert "Analysis Skills" in section
        assert "failure pattern" in section

    def test_get_tools_for_agent_planner(self, assembler: ContextAssembler) -> None:
        tools = assembler._get_tools_for_agent(AgentType.PLANNER)
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"jira_server", "git_server", "rag_query"}

    def test_get_tools_for_agent_executor(self, assembler: ContextAssembler) -> None:
        tools = assembler._get_tools_for_agent(AgentType.EXECUTOR)
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"api_server", "playwright_server", "harness_runner"}

    def test_get_tools_for_agent_analyzer(self, assembler: ContextAssembler) -> None:
        tools = assembler._get_tools_for_agent(AgentType.ANALYZER)
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"jira_server", "git_server", "rag_query"}

    def test_tools_dict_structure(self, assembler: ContextAssembler) -> None:
        for agent_type in AgentType:
            for tool in assembler._get_tools_for_agent(agent_type):
                assert "name" in tool
                assert "type" in tool
                assert "description" in tool

    @pytest.mark.asyncio
    async def test_load_skill_hints_returns_empty(
        self,
        assembler: ContextAssembler,
    ) -> None:
        for agent_type in AgentType:
            hints = await assembler._load_skill_hints(agent_type)
            assert hints == []

    @pytest.mark.asyncio
    async def test_build_rag_context_no_query(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler._build_rag_context(AgentType.PLANNER, None)
        assert result == []

    @pytest.mark.asyncio
    async def test_build_rag_context_no_pipeline(
        self,
        assembler: ContextAssembler,
    ) -> None:
        result = await assembler._build_rag_context(
            AgentType.PLANNER,
            "find test cases for login",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_assemble_different_agent_types_produce_different_prompts(
        self,
        assembler: ContextAssembler,
    ) -> None:
        planner_ctx = await assembler.assemble(AgentType.PLANNER)
        executor_ctx = await assembler.assemble(AgentType.EXECUTOR)
        analyzer_ctx = await assembler.assemble(AgentType.ANALYZER)

        assert planner_ctx.system_prompt != executor_ctx.system_prompt
        assert executor_ctx.system_prompt != analyzer_ctx.system_prompt
        assert planner_ctx.system_prompt != analyzer_ctx.system_prompt

        planner_names = {t["name"] for t in planner_ctx.tools}
        executor_names = {t["name"] for t in executor_ctx.tools}
        analyzer_names = {t["name"] for t in analyzer_ctx.tools}

        assert planner_names != executor_names
        assert executor_names != analyzer_names

    @pytest.mark.asyncio
    async def test_build_rag_context_planner_accessible_collections(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler._build_rag_context(
            AgentType.PLANNER,
            "login authentication",
        )
        assert len(result) > 0

        planner_text = "\n".join(result)
        assert "req_docs" in planner_text
        assert "api_docs" in planner_text
        assert "defect_history" in planner_text
        assert "locator_library" not in planner_text
        assert "failure_patterns" not in planner_text
        assert "test_reports" not in planner_text

    @pytest.mark.asyncio
    async def test_build_rag_context_executor_accessible_collections(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler._build_rag_context(
            AgentType.EXECUTOR,
            "login button",
        )
        assert len(result) > 0

        executor_text = "\n".join(result)
        assert "api_docs" in executor_text
        assert "locator_library" in executor_text
        assert "req_docs" not in executor_text
        assert "defect_history" not in executor_text
        assert "test_reports" not in executor_text
        assert "failure_patterns" not in executor_text

    @pytest.mark.asyncio
    async def test_build_rag_context_analyzer_accessible_collections(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler._build_rag_context(
            AgentType.ANALYZER,
            "login failure analysis",
        )
        assert len(result) > 0

        analyzer_text = "\n".join(result)
        assert "defect_history" in analyzer_text
        assert "test_reports" in analyzer_text
        assert "failure_patterns" in analyzer_text
        assert "req_docs" not in analyzer_text
        assert "api_docs" not in analyzer_text
        assert "locator_library" not in analyzer_text

    @pytest.mark.asyncio
    async def test_build_rag_context_result_format(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler._build_rag_context(
            AgentType.PLANNER,
            "login",
        )
        assert len(result) >= 1

        section_text = result[0]
        assert section_text.startswith("##")
        assert "score:" in section_text
        assert "[req-001]" in section_text or "[api-001]" in section_text or "[def-001]" in section_text

    @pytest.mark.asyncio
    async def test_assemble_with_rag_pipeline_includes_knowledge_section(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler.assemble(
            AgentType.PLANNER,
            rag_query="login authentication",
        )
        assert "# Retrieved Knowledge" in result.system_prompt
        assert "req_docs" in result.system_prompt
        assert "api_docs" in result.system_prompt

    @pytest.mark.asyncio
    async def test_assemble_with_rag_context_maintains_section_order(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler.assemble(
            AgentType.PLANNER,
            rag_query="login authentication",
        )
        prompt = result.system_prompt

        agent_pos = prompt.index("# Agent Identity")
        soul_pos = prompt.index("# Behavioral Guidelines")
        tools_pos = prompt.index("# Available MCP Tools")
        rag_pos = prompt.index("# Retrieved Knowledge")

        assert agent_pos < soul_pos < tools_pos < rag_pos, (
            f"Expected AGENTS -> SOUL -> TOOLS -> RAG in order, "
            f"got AGENTS={agent_pos}, SOUL={soul_pos}, TOOLS={tools_pos}, RAG={rag_pos}"
        )

    @pytest.mark.asyncio
    async def test_build_rag_context_empty_query(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler._build_rag_context(AgentType.PLANNER, "")
        assert result == []

    @pytest.mark.asyncio
    async def test_build_rag_context_whitespace_query(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler._build_rag_context(AgentType.PLANNER, "   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_build_rag_context_query_failure_degradation(
        self,
        failing_rag_assembler: ContextAssembler,
    ) -> None:
        result = await failing_rag_assembler._build_rag_context(
            AgentType.PLANNER,
            "login authentication",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_assemble_rag_context_degradation_empty_system_prompt(
        self,
        failing_rag_assembler: ContextAssembler,
    ) -> None:
        result = await failing_rag_assembler.assemble(
            AgentType.PLANNER,
            rag_query="login authentication",
        )
        assert result.rag_context == []
        assert "# Retrieved Knowledge" not in result.system_prompt

    @pytest.mark.asyncio
    async def test_build_rag_context_planner_gets_all_three(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler._build_rag_context(
            AgentType.PLANNER,
            "login",
        )
        section_text = "\n".join(result)
        assert section_text.count("## ") == 3
        assert "req_docs" in section_text
        assert "api_docs" in section_text
        assert "defect_history" in section_text

    @pytest.mark.asyncio
    async def test_build_rag_context_executor_gets_two(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler._build_rag_context(
            AgentType.EXECUTOR,
            "login",
        )
        section_text = "\n".join(result)
        assert section_text.count("## ") == 2
        assert "api_docs" in section_text
        assert "locator_library" in section_text

    @pytest.mark.asyncio
    async def test_build_rag_context_analyzer_gets_three(
        self,
        rag_assembler: ContextAssembler,
    ) -> None:
        result = await rag_assembler._build_rag_context(
            AgentType.ANALYZER,
            "login",
        )
        section_text = "\n".join(result)
        assert section_text.count("## ") == 3
        assert "defect_history" in section_text
        assert "test_reports" in section_text
        assert "failure_patterns" in section_text
