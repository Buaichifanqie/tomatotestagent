from __future__ import annotations

import time
from typing import ClassVar, Protocol, runtime_checkable

from testagent.common.errors import HarnessError
from testagent.common.logging import get_logger
from testagent.models.result import TestResult

logger = get_logger(__name__)


class RunnerError(HarnessError):
    pass


class UnknownTaskTypeError(RunnerError):
    def __init__(self, task_type: str) -> None:
        self.task_type = task_type
        super().__init__(
            f"Unknown task type: {task_type}",
            code="UNKNOWN_TASK_TYPE",
            details={"task_type": task_type},
        )


@runtime_checkable
class IRunner(Protocol):
    async def setup(self, config: dict[str, object]) -> None: ...

    async def execute(self, test_script: str) -> TestResult: ...

    async def teardown(self) -> None: ...

    async def collect_results(self) -> TestResult: ...


class BaseRunner:
    runner_type: str = ""

    async def setup(self, config: dict[str, object]) -> None:
        raise NotImplementedError

    async def execute(self, test_script: str) -> TestResult:
        raise NotImplementedError

    async def teardown(self) -> None:
        raise NotImplementedError

    async def collect_results(self) -> TestResult:
        raise NotImplementedError

    def _validate_config(self, config: dict[str, object], required_keys: list[str]) -> None:
        missing = [k for k in required_keys if k not in config]
        if missing:
            msg = f"Missing required config keys: {missing}"
            raise RunnerError(msg, code="MISSING_CONFIG", details={"missing_keys": missing})

    def _make_result(
        self,
        status: str,
        *,
        task_id: str = "",
        duration_ms: float = 0.0,
        assertion_results: dict[str, object] | None = None,
        logs: str = "",
        artifacts: dict[str, object] | None = None,
    ) -> TestResult:
        return TestResult(
            task_id=task_id,
            status=status,
            duration_ms=duration_ms,
            assertion_results=assertion_results or {},
            logs=logs,
            artifacts=artifacts or {},
        )

    def _now_ms(self) -> float:
        return time.monotonic() * 1000


class RunnerFactory:
    _runners: ClassVar[dict[str, type[BaseRunner]]] = {}

    @classmethod
    def register(cls, runner_type: str, runner_cls: type[BaseRunner]) -> None:
        logger.info("Registering runner", extra={"runner_type": runner_type, "runner_cls": runner_cls.__name__})
        cls._runners[runner_type] = runner_cls

    @classmethod
    def get_runner(cls, task_type: str) -> BaseRunner:
        runner_cls = cls._runners.get(task_type)
        if runner_cls is None:
            raise UnknownTaskTypeError(task_type)
        logger.debug("Creating runner", extra={"task_type": task_type, "runner_cls": runner_cls.__name__})
        return runner_cls()
