from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# -- Enums --------------------------------------------------------------------


class SqlOperationType(str, Enum):
    """Allowed SQL operation types. DELETE is forbidden by design."""

    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"


# -- Configuration ------------------------------------------------------------


class DbConfig(BaseModel):
    """Configuration for the AI Database Operation Engine."""

    connection_url: str = ""  # e.g. "mysql+aiomysql://user:pass@host/db"
    max_iterations: int = Field(default=5, ge=1, le=20)
    timeout_seconds: int = Field(default=30, ge=5)
    confirm_writes: bool = True
    allowed_operations: list[SqlOperationType] = Field(
        default_factory=lambda: [
            SqlOperationType.SELECT,
            SqlOperationType.INSERT,
            SqlOperationType.UPDATE,
        ]
    )
    is_test_flag: bool = True


# -- SQL Operation & Result ---------------------------------------------------


class SqlOperation(BaseModel):
    """A single SQL operation to execute."""

    type: SqlOperationType
    sql: str
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    is_cleanup: bool = False


class ExecutionResult(BaseModel):
    """Result of executing one SqlOperation."""

    success: bool
    operation: SqlOperation
    rows_affected: int = 0
    data: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str = ""
    duration_ms: int = 0


# -- Cleanup & Aggregate Results ----------------------------------------------


class CleanupPlan(BaseModel):
    """A set of SQL operations to undo test data after execution."""

    operations: list[SqlOperation] = Field(default_factory=list)
    description: str = ""


class DbOpsResult(BaseModel):
    """Aggregate result of a full db_ops AI decision loop."""

    success: bool
    operations: list[ExecutionResult] = Field(default_factory=list)
    cleanup_plan: CleanupPlan | None = None
    iterations_used: int = 0
    total_duration_ms: int = 0
    error_message: str = ""
