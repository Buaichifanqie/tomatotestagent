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


class ExecutorAgent:
    """Executor Agent: 测试执行、自愈修复、结果收集 (32K 上下文窗口)"""

    AGENT_TYPE: AgentType = AgentType.EXECUTOR
    CONTEXT_WINDOW: int = 32_000

    def __init__(self, llm: ILLMProvider, context_assembler: ContextAssembler) -> None:
        self._llm = llm
        self._context_assembler = context_assembler
        self._todo = TodoManager()

    @property
    def todo(self) -> TodoManager:
        return self._todo

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        执行测试任务:
        1. assemble context for Executor
        2. 启动 agent_loop (空 messages 启动, task prompt 作为第一条消息)
        3. 收集执行结果
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
            "ExecutorAgent starting execution",
            extra={"extra_data": {"task_keys": list(task.keys()), "tools_count": len(context.tools)}},
        )

        result_messages = await agent_loop(
            messages=messages,
            tools=context.tools,
            system=context.system_prompt,
            llm_provider=self._llm,
        )

        execution_result = self._collect_results(result_messages)

        logger.info(
            "ExecutorAgent execution completed",
            extra={"extra_data": {"status": execution_result.get("status")}},
        )

        return {
            "agent_type": self.AGENT_TYPE.value,
            "result": execution_result,
            "message_count": len(result_messages),
        }

    def _collect_results(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        assistant_messages = [m for m in messages if m.get("role") == "assistant"]
        if not assistant_messages:
            return {"status": "no_output", "details": "", "logs": ""}

        last_assistant = assistant_messages[-1]
        content = last_assistant.get("content", "")

        if isinstance(content, list):
            text_parts = [
                block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
            ]
            content = "\n".join(text_parts)

        return {
            "status": "completed",
            "details": content[:2000] if isinstance(content, str) else str(content)[:2000],
            "logs": "",
        }
