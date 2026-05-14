from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from testagent.agent.context import AgentType
from testagent.agent.loop import agent_loop
from testagent.agent.todo import TodoManager
from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from testagent.agent.context import ContextAssembler
    from testagent.agent.root_cause import RootCauseAnalyzer
    from testagent.llm.base import ILLMProvider

logger = get_logger(__name__)


class AnalyzerAgent:
    """Analyzer Agent: 失败分类、根因分析、缺陷归档 (64K 上下文窗口)"""

    AGENT_TYPE: AgentType = AgentType.ANALYZER
    CONTEXT_WINDOW: int = 64_000

    def __init__(
        self,
        llm: ILLMProvider,
        context_assembler: ContextAssembler,
        root_cause_analyzer: RootCauseAnalyzer | None = None,
    ) -> None:
        self._llm = llm
        self._context_assembler = context_assembler
        self._root_cause_analyzer = root_cause_analyzer
        self._todo = TodoManager()

    @property
    def todo(self) -> TodoManager:
        return self._todo

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        执行分析任务:
        1. assemble context for Analyzer
        2. 启动 agent_loop (空 messages 启动, task prompt 作为第一条消息)
        3. 生成分析报告和缺陷记录
        4. 如果存在缺陷且配置了 RootCauseAnalyzer，执行根因分析
        """
        rag_query = task.get("rag_query")
        context = await self._context_assembler.assemble(
            agent_type=self.AGENT_TYPE,
            rag_query=rag_query,
        )

        task_prompt = json.dumps(task, ensure_ascii=False, default=str)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": task_prompt},
        ]

        if context.rag_context:
            rag_text = "\n".join(context.rag_context)
            messages.append({"role": "user", "content": f"[RAG Context]\n{rag_text}\n[End RAG Context]"})

        logger.info(
            "AnalyzerAgent starting execution",
            extra={"extra_data": {"task_keys": list(task.keys()), "tools_count": len(context.tools)}},
        )

        result_messages = await agent_loop(
            messages=messages,
            tools=context.tools,
            system=context.system_prompt,
            llm_provider=self._llm,
        )

        analysis = self._generate_analysis(result_messages)

        defects = analysis.get("defects", [])
        if defects and self._root_cause_analyzer is not None:
            analysis = await self._enrich_with_root_cause(analysis, task)

        logger.info(
            "AnalyzerAgent execution completed",
            extra={"extra_data": {"defect_count": len(defects)}},
        )

        return {
            "agent_type": self.AGENT_TYPE.value,
            "analysis": analysis,
            "message_count": len(result_messages),
        }

    async def _enrich_with_root_cause(
        self,
        analysis: dict[str, Any],
        task: dict[str, Any],
    ) -> dict[str, Any]:
        root_cause_analyzer = self._root_cause_analyzer
        assert root_cause_analyzer is not None

        defects = analysis.get("defects", [])
        test_results_map: dict[str, dict[str, Any]] = {
            tr.get("result_id", tr.get("id", "")): tr
            for tr in task.get("test_results", [])
        }

        enriched_defects: list[dict[str, Any]] = []
        for defect_data in defects:
            result_id = defect_data.get("result_id", "")
            test_result_data = test_results_map.get(result_id)

            if test_result_data:
                try:
                    from testagent.models.result import TestResult
                    test_result = TestResult(**test_result_data)
                    result = await root_cause_analyzer.analyze(defect_data, test_result)
                    defect_data["root_cause"] = result.to_dict()
                except Exception as exc:
                    logger.warning(
                        "Root cause analysis failed for defect",
                        extra={"extra_data": {"defect_id": defect_data.get("id", ""), "error": str(exc)}},
                    )

            enriched_defects.append(defect_data)

        analysis["defects"] = enriched_defects
        return analysis

    def _generate_analysis(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        assistant_messages = [m for m in messages if m.get("role") == "assistant"]
        if not assistant_messages:
            return {"defects": [], "summary": "no_output", "classification": ""}

        last_assistant = assistant_messages[-1]
        content = last_assistant.get("content", "")

        if isinstance(content, list):
            text_parts = [
                block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
            ]
            content = "\n".join(text_parts)

        return {
            "defects": [],
            "summary": content[:2000] if isinstance(content, str) else str(content)[:2000],
            "classification": "",
        }
