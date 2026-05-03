from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from testagent.common.logging import get_logger
from testagent.rag.collections import CollectionManager

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings
    from testagent.models.session import TestSession
    from testagent.rag.pipeline import RAGPipeline

logger = get_logger(__name__)


class AgentType(Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    ANALYZER = "analyzer"


@dataclass
class AssembledContext:
    system_prompt: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    rag_context: list[str] = field(default_factory=list)
    skill_hints: list[dict[str, Any]] = field(default_factory=list)


class ContextAssembler:
    def __init__(
        self,
        settings: TestAgentSettings,
        rag_pipeline: RAGPipeline | None = None,
    ) -> None:
        self._settings = settings
        self._rag_pipeline = rag_pipeline
        self._collection_manager = CollectionManager()

    async def assemble(
        self,
        agent_type: AgentType,
        session: TestSession | None = None,
        rag_query: str | None = None,
    ) -> AssembledContext:
        agents_section = self._build_agents_section(agent_type)
        soul_section = self._build_soul_section(agent_type)
        tools_section = self._build_tools_section(agent_type)
        tools = self._get_tools_for_agent(agent_type)
        skill_hints = await self._load_skill_hints(agent_type)
        rag_context = await self._build_rag_context(agent_type, rag_query)

        system_parts: list[str] = [agents_section, soul_section, tools_section]

        if skill_hints:
            hints_text = "\n".join(f"- {s.get('name', 'unknown')}: {s.get('description', '')}" for s in skill_hints)
            system_parts.append(f"# Available Skills\n\n{hints_text}")

        if rag_context:
            rag_section = "\n\n".join(rag_context)
            system_parts.append(f"# Retrieved Knowledge\n\n{rag_section}")

        return AssembledContext(
            system_prompt="\n\n".join(system_parts),
            tools=tools,
            rag_context=rag_context,
            skill_hints=skill_hints,
        )

    def _build_agents_section(self, agent_type: AgentType) -> str:
        lines: list[str] = [
            "# Agent Identity",
        ]

        if agent_type == AgentType.PLANNER:
            lines.append(
                "You are the Planner Agent, a test planning specialist. "
                "Your role is to analyze requirements, generate test strategies, "
                "and orchestrate tasks across the testing lifecycle. "
                "You have a 128K context window and operate with the highest "
                "priority among all agents."
            )

        elif agent_type == AgentType.EXECUTOR:
            lines.append(
                "You are the Executor Agent, a test execution specialist. "
                "Your role is to execute tests in sandboxed environments, "
                "perform self-healing when failures occur, "
                "and collect detailed results and logs. "
                "You have a 32K context window."
            )

        elif agent_type == AgentType.ANALYZER:
            lines.append(
                "You are the Analyzer Agent, a test analysis specialist. "
                "Your role is to classify test failures, perform root cause analysis, "
                "and archive defects to the knowledge base. "
                "You have a 64K context window."
            )

        return "\n".join(lines)

    def _build_soul_section(self, agent_type: AgentType) -> str:
        lines: list[str] = [
            "# Behavioral Guidelines",
            "",
            "You must adhere to the following core principles:",
        ]

        if agent_type == AgentType.PLANNER:
            lines.extend(
                [
                    "",
                    "- Pursue maximum test coverage, prioritizing high-risk modules",
                    "- Ensure comprehensive test strategies covering happy path, boundary values, and error scenarios",
                    "- Follow the session state machine: planning → executing → analyzing",
                    "- Use RAG knowledge from requirement docs, API docs, and defect history",
                    "- Assign tasks to Executor Agents with clear instructions",
                ]
            )

        elif agent_type == AgentType.EXECUTOR:
            lines.extend(
                [
                    "",
                    "- Prioritize completing tests efficiently",
                    "- When a test failure occurs, attempt self-healing before reporting",
                    "- Collect detailed results including screenshots, logs, and timing data",
                    "- Respect configured timeout constraints for each test type",
                    "- Operate in isolated sandbox environments for security",
                ]
            )

        elif agent_type == AgentType.ANALYZER:
            lines.extend(
                [
                    "",
                    "- Classify failures precisely: bug / flaky / environment / configuration",
                    "- Better to analyze thoroughly than miss a defect classification",
                    "- Assess defect severity: critical / major / minor / trivial",
                    "- Write analysis findings back to the RAG knowledge base",
                    "- Investigate code changes via Git to identify root causes",
                ]
            )

        lines.extend(
            [
                "",
                "- Never log or expose API keys, passwords, or PII data",
                "- Use structured communication via the Gateway message protocol",
                "- Operate with empty initial message history for full context isolation",
            ]
        )

        return "\n".join(lines)

    def _build_tools_section(self, agent_type: AgentType) -> str:
        lines: list[str] = [
            "# Available MCP Tools",
            "",
        ]

        if agent_type == AgentType.PLANNER:
            lines.extend(
                [
                    "- **MCP Jira Server**: Access requirements and issue tracking",
                    "- **MCP Git Server**: Access code repository and change history",
                    "- **Strategy Skills**: Test strategy formulation and planning",
                    "- **RAG Query**: Retrieve knowledge from requirement docs, API docs, and defect history",
                ]
            )

        elif agent_type == AgentType.EXECUTOR:
            lines.extend(
                [
                    "- **MCP Playwright Server**: Web browser automation and interaction",
                    "- **MCP API Server**: HTTP API testing and validation",
                    "- **Harness Runner**: Sandboxed test execution environment",
                    "- **RAG Query**: Retrieve locator library and environment configuration",
                ]
            )

        elif agent_type == AgentType.ANALYZER:
            lines.extend(
                [
                    "- **MCP Jira Server**: Create and update defect tickets",
                    "- **MCP Git Server**: Investigate code changes for root cause",
                    "- **Analysis Skills**: Failure pattern matching and classification",
                    "- **RAG Query**: Retrieve defect history and failure pattern library",
                ]
            )

        lines.extend(
            [
                "",
                "Note: This is a placeholder tool description. Detailed tool schemas will be loaded in Phase 3.",
            ]
        )

        return "\n".join(lines)

    def _get_tools_for_agent(self, agent_type: AgentType) -> list[dict[str, Any]]:
        if agent_type == AgentType.PLANNER:
            return [
                {"name": "jira_server", "type": "mcp", "description": "Jira issue tracking"},
                {"name": "git_server", "type": "mcp", "description": "Git repository access"},
                {"name": "rag_query", "type": "mcp", "description": "Knowledge base retrieval"},
            ]
        if agent_type == AgentType.EXECUTOR:
            return [
                {"name": "playwright_server", "type": "mcp", "description": "Web browser automation"},
                {"name": "api_server", "type": "mcp", "description": "HTTP API testing"},
                {"name": "harness_runner", "type": "harness", "description": "Sandbox execution"},
            ]
        if agent_type == AgentType.ANALYZER:
            return [
                {"name": "jira_server", "type": "mcp", "description": "Defect tracking"},
                {"name": "git_server", "type": "mcp", "description": "Code change investigation"},
                {"name": "rag_query", "type": "mcp", "description": "Knowledge base retrieval"},
            ]
        return []

    async def _load_skill_hints(self, agent_type: AgentType) -> list[dict[str, Any]]:
        _ = agent_type
        logger.debug(
            "Skill hints loading placeholder",
            extra={"extra_data": {"agent_type": agent_type.value}},
        )
        return []

    async def _build_rag_context(
        self,
        agent_type: AgentType,
        rag_query: str | None,
    ) -> list[str]:
        if not rag_query or not rag_query.strip() or not self._rag_pipeline:
            return []

        agent_name = agent_type.value
        collections = self._collection_manager.get_accessible_collections(agent_name)

        if not collections:
            logger.debug(
                "No accessible RAG collections for agent type",
                extra={"extra_data": {"agent_type": agent_name}},
            )
            return []

        logger.debug(
            "Querying RAG collections",
            extra={
                "extra_data": {
                    "agent_type": agent_name,
                    "collections": collections,
                    "rag_query": rag_query,
                }
            },
        )

        context_parts: list[str] = []
        for collection in collections:
            try:
                results = await self._rag_pipeline.query(
                    query_text=rag_query,
                    collection=collection,
                    top_k=3,
                )
            except Exception:
                logger.warning(
                    "RAG query failed for collection",
                    extra={"extra_data": {"collection": collection, "agent_type": agent_name}},
                    exc_info=True,
                )
                continue

            if not results:
                continue

            collection_desc = self._collection_manager.get_description(collection)
            header = f"## {collection} — {collection_desc}" if collection_desc else f"## {collection}"
            items: list[str] = [header]
            for r in results:
                items.append(f"- [{r.doc_id}] (score: {r.score:.3f}) {r.content}")

            context_parts.append("\n".join(items))

        return context_parts
