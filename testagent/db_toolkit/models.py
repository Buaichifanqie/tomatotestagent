from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Environment(str, Enum):
    TEST = "test"
    PRODUCTION = "production"


class SqlOpType(str, Enum):
    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


@dataclass(frozen=True)
class DbEnv:
    level: Environment
    connection_url: str
    detected_by: str  # "config" | "url_pattern" | "default"
    allow_write: bool = field(init=False)
    allow_delete: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "allow_write", self.level == Environment.TEST)
        object.__setattr__(self, "allow_delete", self.level == Environment.TEST)


@dataclass
class ExecutionResult:
    success: bool
    rows_affected: int = 0
    data: list[dict[str, Any]] = field(default_factory=list)
    error_message: str = ""
    duration_ms: int = 0
