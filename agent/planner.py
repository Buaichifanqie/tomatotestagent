from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from testagent.agent.context import AgentType
from testagent.agent.loop import agent_loop
from testagent.agent.todo import TodoManager
from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from testagent.agent.context import ContextAssembler
    from testagent.llm.base import ILLMProvider

logger = get_logger(__name__)


class PlannerAgent:
    """Planner Agent: 需求解析、策略生成、任务编排 (128K 上下文窗口)"""

    AGENT_TYPE: AgentType = AgentType.PLANNER
    CONTEXT_WINDOW: int = 128_000

    def __init__(self, llm: ILLMProvider, context_assembler: ContextAssembler) -> None:
        self._llm = llm
        self._context_assembler = context_assembler
        self._todo = TodoManager()

    @property
    def todo(self) -> TodoManager:
        return self._todo

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        执行规划任务:
        1. assemble context for Planner
        2. 启动 agent_loop (空 messages 启动, task prompt 作为第一条消息)
        3. 解析 LLM 输出为结构化测试计划
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
            "PlannerAgent starting execution",
            extra={"extra_data": {"task_keys": list(task.keys()), "tools_count": len(context.tools)}},
        )

        result_messages = await agent_loop(
            messages=messages,
            tools=context.tools,
            system=context.system_prompt,
            llm_provider=self._llm,
        )

        plan = self._extract_plan(result_messages)

        logger.info(
            "PlannerAgent execution completed",
            extra={"extra_data": {"plan_keys": list(plan.keys())}},
        )

        return {
            "agent_type": self.AGENT_TYPE.value,
            "plan": plan,
            "message_count": len(result_messages),
        }

    def _extract_plan(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        assistant_messages = [m for m in messages if m.get("role") == "assistant"]
        if not assistant_messages:
            return {"test_tasks": [], "strategy": "no_output"}

        last_assistant = assistant_messages[-1]
        content = last_assistant.get("content", "")

        if isinstance(content, list):
            text_parts = [
                block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
            ]
            content = "\n".join(text_parts)

        return {
            "test_tasks": [],
            "strategy": content[:2000] if isinstance(content, str) else str(content)[:2000],
        }
