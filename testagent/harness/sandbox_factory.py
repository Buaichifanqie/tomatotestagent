from __future__ import annotations

import enum
from typing import ClassVar

from testagent.common.errors import SandboxError
from testagent.common.logging import get_logger
from testagent.harness.docker_sandbox import DockerSandbox
from testagent.harness.local_runner import LocalProcessSandbox
from testagent.harness.microvm_sandbox import MicroVMSandbox

logger = get_logger(__name__)


class IsolationLevel(enum.StrEnum):
    """Sandbox isolation levels (ADR-004)."""

    DOCKER = "docker"
    MICROVM = "microvm"  # V1.0
    LOCAL = "local"


class SandboxFactoryError(SandboxError):
    pass


class SandboxFactory:
    """Factory pattern -- creates the appropriate sandbox implementation
    based on the requested isolation level.

    Strategy Pattern is exposed via :meth:`decide_isolation` for runtime
    switching based on task type.
    """

    _registry: ClassVar[dict[IsolationLevel, type]] = {}

    @classmethod
    def register(cls, level: IsolationLevel, sandbox_cls: type) -> None:
        cls._registry[level] = sandbox_cls
        logger.info(
            "Registered sandbox implementation",
            extra={"level": level.value, "cls": sandbox_cls.__name__},
        )

    @classmethod
    def create(cls, level: IsolationLevel | str) -> object:
        """Return a sandbox instance for the given isolation level."""
        if isinstance(level, str):
            try:
                level = IsolationLevel(level)
            except ValueError:
                raise SandboxFactoryError(
                    f"Unknown isolation level: {level}",
                    code="UNKNOWN_ISOLATION_LEVEL",
                    details={"level": level},
                ) from None
        sandbox_cls = cls._registry.get(level)
        if sandbox_cls is None:
            raise SandboxFactoryError(
                f"No sandbox registered for isolation level: {level.value}",
                code="UNKNOWN_ISOLATION_LEVEL",
                details={"level": level.value},
            )
        return sandbox_cls()

    @staticmethod
    def decide_isolation(task_type: str, *, force_local: bool = False) -> IsolationLevel:
        """Determine the appropriate isolation level for a task type.

        Decision priority (ADR-004):
          1. User explicit override via *force_local* (dev-only)
          2. Task type -> isolation mapping

        Args:
            task_type: One of ``api_test``, ``web_test``, ``app_test``.
            force_local: If *True*, return LOCAL (development / debug only).

        Returns:
            The matching :class:`IsolationLevel`.

        Raises:
            SandboxFactoryError: If *task_type* is unknown and no
                sensible default applies.
        """
        if force_local:
            logger.warning(
                "Forcing LOCAL isolation -- NOT suitable for production / CI",
                extra={"task_type": task_type},
            )
            return IsolationLevel.LOCAL

        mapping: dict[str, IsolationLevel] = {
            "api_test": IsolationLevel.DOCKER,
            "web_test": IsolationLevel.DOCKER,
            "app_test": IsolationLevel.MICROVM,
        }

        level = mapping.get(task_type)
        if level is None:
            raise SandboxFactoryError(
                f"Unknown task type '{task_type}' -- cannot determine isolation level",
                code="UNKNOWN_TASK_TYPE",
                details={"task_type": task_type},
            )
        return level


# ---------------------------------------------------------------------------
# Auto-register built-in implementations on module import
# ---------------------------------------------------------------------------
SandboxFactory.register(IsolationLevel.DOCKER, DockerSandbox)
SandboxFactory.register(IsolationLevel.LOCAL, LocalProcessSandbox)
SandboxFactory.register(IsolationLevel.MICROVM, MicroVMSandbox)
