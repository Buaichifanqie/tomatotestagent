from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from testagent.common.errors import HarnessError
from testagent.common.logging import get_logger
from testagent.harness.runners.base import IRunner, RunnerFactory
from testagent.harness.sandbox import ISandbox
from testagent.harness.sandbox_factory import IsolationLevel, SandboxFactory, SandboxFactoryError

if TYPE_CHECKING:
    from testagent.models.plan import TestTask
    from testagent.models.result import TestResult

logger = get_logger(__name__)


class OrchestratorError(HarnessError):
    pass


class HarnessOrchestrator:
    """Harness 执行引擎编排器 (ADR-004 / ADR-005).

    Responsibilities:
      - Decide isolation level per task (user explicit > task type auto).
      - Dispatch a task through the full sandbox + runner lifecycle.
      - Retry with exponential backoff (2s → 4s → 8s, max 3 attempts).
    """

    def __init__(
        self,
        sandbox_factory: type[SandboxFactory] | None = None,
        runner_factory: type[RunnerFactory] | None = None,
    ) -> None:
        self._sandbox_factory = sandbox_factory or SandboxFactory
        self._runner_factory = runner_factory or RunnerFactory

    # ------------------------------------------------------------------
    # Isolation decision
    # ------------------------------------------------------------------

    def decide_isolation(self, task: TestTask) -> IsolationLevel:
        """Determine the isolation level for *task*.

        Decision priority (ADR-004 / AGENTS.md):
          1. User explicit override via ``task.isolation_level``.
          2. Automatic decision based on ``task.task_type``.

        Raises:
            OrchestratorError: If the user-specified level is not a valid
                :class:`IsolationLevel` value.
        """
        if task.isolation_level:
            try:
                return IsolationLevel(task.isolation_level)
            except ValueError:
                raise OrchestratorError(
                    f"Invalid isolation level '{task.isolation_level}' on task {task.id}",
                    code="INVALID_ISOLATION_LEVEL",
                    details={
                        "task_id": task.id,
                        "isolation_level": task.isolation_level,
                        "task_type": task.task_type,
                    },
                ) from None

        try:
            return SandboxFactory.decide_isolation(task.task_type)
        except SandboxFactoryError:
            logger.warning(
                "Unknown task type, defaulting to LOCAL isolation",
                extra={"task_type": task.task_type, "task_id": task.id},
            )
            return IsolationLevel.LOCAL

    # ------------------------------------------------------------------
    # Single dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, task: TestTask) -> TestResult:
        """Execute *task* through the full sandbox + runner lifecycle.

        Steps (AGENTS.md Harness 执行引擎规则):
          1. ``decide_isolation(task)``
          2. ``sandbox = sandbox_factory.create(level)``
          3. ``runner = runner_factory.get_runner(task.task_type)``
          4. ``runner.setup()``
          5. ``runner.execute()`` — with timeout protection
          6. ``runner.collect_results()``
          7. ``runner.teardown()``
          8. ``sandbox.destroy()``

        Raises:
            OrchestratorError: On any failure during the dispatch lifecycle.
        """
        level = self.decide_isolation(task)
        sandbox_instance = self._sandbox_factory.create(level)

        if not isinstance(sandbox_instance, ISandbox):
            raise OrchestratorError(
                f"Sandbox {type(sandbox_instance).__name__} does not conform to ISandbox protocol",
                code="INVALID_SANDBOX",
                details={"level": level.value},
            )

        sandbox_id = await sandbox_instance.create(task.task_config)

        runner: IRunner | None = None
        try:
            runner = self._runner_factory.get_runner(task.task_type)

            await runner.setup(task.task_config)

            test_script: str = (
                json.dumps(task.task_config) if isinstance(task.task_config, dict) else str(task.task_config)
            )
            await runner.execute(test_script)

            result = await runner.collect_results()
        except Exception as e:
            raise OrchestratorError(
                f"Dispatch failed for task {task.id}: {e}",
                code="DISPATCH_FAILED",
                details={
                    "task_id": task.id,
                    "task_type": task.task_type,
                    "error": str(e),
                },
            ) from e
        finally:
            if runner is not None:
                try:
                    await runner.teardown()
                except Exception:
                    logger.exception(
                        "Runner teardown failed",
                        extra={"task_id": task.id, "task_type": task.task_type},
                    )
            try:
                await sandbox_instance.destroy(sandbox_id)
            except Exception:
                logger.exception(
                    "Sandbox destroy failed",
                    extra={"task_id": task.id, "sandbox_id": sandbox_id},
                )

        return result

    # ------------------------------------------------------------------
    # Dispatch with retry (exponential backoff)
    # ------------------------------------------------------------------

    async def dispatch_with_retry(self, task: TestTask) -> TestResult:
        """Dispatch *task* with exponential-backoff retry.

        Retry strategy (AGENTS.md Do #8):
          - Attempts:   3 total (1 initial + 2 retries)
          - Backoff:    2s → 4s → 8s
          - Max retries: 3

        Raises:
            OrchestratorError: After all 3 attempts have been exhausted.
        """
        last_exception: Exception | None = None

        for attempt in range(3):
            try:
                logger.info(
                    "Dispatching task",
                    extra={
                        "task_id": task.id,
                        "task_type": task.task_type,
                        "attempt": attempt + 1,
                    },
                )
                return await self.dispatch(task)
            except Exception as e:
                last_exception = e
                logger.warning(
                    "Dispatch attempt failed",
                    extra={
                        "task_id": task.id,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "remaining_retries": 2 - attempt,
                    },
                )

                if attempt < 2:
                    delay = 2 * (2**attempt)
                    logger.info(
                        "Waiting before retry",
                        extra={
                            "task_id": task.id,
                            "delay_ms": delay * 1000,
                            "next_attempt": attempt + 2,
                        },
                    )
                    await asyncio.sleep(delay)

        raise OrchestratorError(
            f"Task {task.id} failed after 3 attempts. Last error: {last_exception}",
            code="MAX_RETRIES_EXCEEDED",
            details={
                "task_id": task.id,
                "task_type": task.task_type,
                "max_attempts": 3,
                "last_error": str(last_exception),
            },
        ) from last_exception
