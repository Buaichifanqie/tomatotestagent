from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from testagent.common.logging import get_logger
from testagent.config.settings import get_settings
from testagent.gateway.celery_app import celery_app

if TYPE_CHECKING:
    from testagent.models.plan import TestTask
    from testagent.models.result import TestResult

logger = get_logger(__name__)


def _result_to_dict(result: TestResult) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "status": result.status,
        "duration_ms": result.duration_ms,
        "assertion_results": result.assertion_results,
        "logs": result.logs,
        "screenshot_url": result.screenshot_url,
        "video_url": result.video_url,
        "artifacts": result.artifacts,
    }


def _build_task(task_id: str, task_config: dict[str, Any]) -> TestTask:
    from testagent.models.plan import TestTask as TestTaskModel

    return TestTaskModel(
        id=task_id,
        plan_id=task_config.get("plan_id", ""),
        task_type=task_config.get("task_type", "api_test"),
        skill_ref=task_config.get("skill_ref"),
        task_config=task_config,
        isolation_level=task_config.get("isolation_level", "docker"),
        priority=task_config.get("priority", 0),
        status="running",
        retry_count=task_config.get("retry_count", 0),
    )


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=2,
    acks_late=True,
    reject_on_worker_lost=True,
    queue="execution",
    soft_time_limit=300,
    time_limit=330,
)
def execute_test_task(
    self: Any,
    task_id: str,
    task_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if task_config is None:
        task_config = {}

    async def _run() -> dict[str, Any]:
        from testagent.harness.orchestrator import HarnessOrchestrator

        task = _build_task(task_id, task_config)
        orchestrator = HarnessOrchestrator()
        result: TestResult = await orchestrator.dispatch_with_retry(task)
        return _result_to_dict(result)

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception(
            "Test task execution failed",
            extra={"task_id": task_id, "error": str(exc)},
        )
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1)) from exc


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=2,
    acks_late=True,
    queue="planning",
    soft_time_limit=120,
    time_limit=150,
)
def execute_planning_task(
    self: Any,
    session_id: str,
    input_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if input_context is None:
        input_context = {}

    async def _run() -> dict[str, Any]:
        from testagent.agent.context import ContextAssembler
        from testagent.agent.planner import PlannerAgent
        from testagent.llm.openai_provider import OpenAIProvider

        settings = get_settings()
        llm = OpenAIProvider(settings=settings)
        context_assembler = ContextAssembler(settings=settings)
        agent = PlannerAgent(llm=llm, context_assembler=context_assembler)
        result = await agent.execute(input_context)
        return result

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception(
            "Planning task failed",
            extra={"session_id": session_id, "error": str(exc)},
        )
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1)) from exc


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=2,
    acks_late=True,
    queue="analysis",
    soft_time_limit=120,
    time_limit=150,
)
def execute_analysis_task(
    self: Any,
    session_id: str,
    failed_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if failed_results is None:
        failed_results = []

    async def _run() -> dict[str, Any]:
        from testagent.agent.analyzer import AnalyzerAgent
        from testagent.agent.context import ContextAssembler
        from testagent.llm.openai_provider import OpenAIProvider

        settings = get_settings()
        llm = OpenAIProvider(settings=settings)
        context_assembler = ContextAssembler(settings=settings)
        agent = AnalyzerAgent(llm=llm, context_assembler=context_assembler)
        input_data = {
            "session_id": session_id,
            "failed_results": failed_results,
        }
        result = await agent.execute(input_data)
        return result

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception(
            "Analysis task failed",
            extra={"session_id": session_id, "error": str(exc)},
        )
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1)) from exc
