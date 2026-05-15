from __future__ import annotations

import os
import time
import uuid
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from testagent.harness.sandbox import ISandbox

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
    async def setup(
        self,
        config: dict[str, object],
        sandbox: ISandbox | None = None,
        sandbox_id: str | None = None,
    ) -> None: ...
    async def execute(self, test_script: str) -> TestResult: ...
    async def teardown(self) -> None: ...
    async def collect_results(self) -> TestResult: ...


class BaseRunner:
    runner_type: str = ""

    def __init__(self) -> None:
        self._sandbox: ISandbox | None = None
        self._sandbox_id: str | None = None
        self._sandbox_tmpdir: str | None = None

    async def setup(
        self,
        config: dict[str, object],
        sandbox: ISandbox | None = None,
        sandbox_id: str | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._sandbox_id = sandbox_id
        if sandbox is not None and sandbox_id is not None:
            self._sandbox_tmpdir = await sandbox.get_tmpdir(sandbox_id)

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

    @property
    def _in_docker_mode(self) -> bool:
        return self._sandbox is not None

    async def _write_script(self, script_content: str, filename: str | None = None) -> str:
        """Write a script to the sandbox temp directory.

        Returns the in-container path (``/tmp/testagent/<filename>``).
        Only available when running in Docker mode.
        """
        if not self._sandbox or not self._sandbox_id:
            raise RunnerError(
                "Cannot write script without a sandbox reference",
                code="DOCKER_MODE_REQUIRED",
            )

        from testagent.harness.sandbox import ISandbox

        sandbox = self._sandbox
        if not isinstance(sandbox, ISandbox):
            raise RunnerError("Sandbox does not conform to ISandbox protocol", code="INVALID_SANDBOX")

        if filename is None:
            filename = f"test_{uuid.uuid4().hex[:12]}.py"
        tmpdir = await sandbox.get_tmpdir(self._sandbox_id)
        self._sandbox_tmpdir = tmpdir
        host_path = os.path.join(tmpdir, filename)
        with open(host_path, "w", encoding="utf-8") as f:
            f.write(script_content)
        container_path = f"/tmp/testagent/{filename}"
        logger.debug(
            "Wrote script for Docker execution",
            extra={"host_path": host_path, "container_path": container_path},
        )
        return container_path

    async def _run_in_sandbox(self, command: str, timeout: int | None = None) -> dict[str, object]:
        """Execute a command inside the sandbox.

        Only available when running in Docker mode.
        """
        if not self._sandbox or not self._sandbox_id:
            raise RunnerError(
                "Cannot run in sandbox without a sandbox reference",
                code="DOCKER_MODE_REQUIRED",
            )

        from testagent.harness.sandbox import ISandbox

        sandbox = self._sandbox
        if not isinstance(sandbox, ISandbox):
            raise RunnerError("Sandbox does not conform to ISandbox protocol", code="INVALID_SANDBOX")

        return await sandbox.execute(self._sandbox_id, command, timeout=timeout or 60)

    def _generate_docker_exec_script(self, test_script: str) -> str:
        """Generate a standalone Python script for execution inside the Docker container.

        Subclasses must override this to generate the appropriate script
        for their test type (API vs Web).
        """
        raise NotImplementedError

    def _parse_docker_output(self, output: dict[str, object]) -> TestResult:
        """Parse the stdout/stderr from a Docker execution into a TestResult.

        Subclasses must override this to parse the output appropriate
        for their test type.
        """
        raise NotImplementedError


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
