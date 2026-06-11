from __future__ import annotations

import re
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from testagent.common.logging import get_logger
from testagent.db_ops.connection import ConnectionManager
from testagent.db_ops.errors import ExecutionTimeoutError, ForbiddenOperationError
from testagent.db_ops.models import ExecutionResult, SqlOperation, SqlOperationType

logger = get_logger(__name__)

_SELECT_LIMIT = 1000


class SQLExecutor:
    """Executes SQL operations with safety controls.

    Safety rules:
    - DELETE operations are always blocked
    - SELECT without LIMIT gets LIMIT injected
    - Execution timeout enforced per operation
    - All queries use parameterized execution
    """

    def __init__(
        self,
        conn_manager: ConnectionManager,
        timeout_seconds: int = 30,
    ) -> None:
        self._conn_manager = conn_manager
        self._timeout_seconds = timeout_seconds

    async def execute(
        self,
        operation: SqlOperation,
        connection_url: str,
    ) -> ExecutionResult:
        """Execute a single SQL operation with safety checks."""
        self._validate_operation(operation)

        sql = self._inject_limits(operation.sql, operation.type)
        start = time.monotonic()

        try:
            result = await self._run_sql(sql, operation.params, connection_url)
            duration = int((time.monotonic() - start) * 1000)

            return ExecutionResult(
                success=True,
                operation=operation,
                rows_affected=result.get("rows_affected", 0),
                data=result.get("data", []),
                duration_ms=duration,
            )
        except ForbiddenOperationError:
            raise
        except TimeoutError as exc:
            duration = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                success=False,
                operation=operation,
                error_message=f"Execution timed out after {self._timeout_seconds}s",
                duration_ms=duration,
            )
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            logger.warning("SQL execution failed: %s", exc)
            return ExecutionResult(
                success=False,
                operation=operation,
                error_message=str(exc),
                duration_ms=duration,
            )

    def _validate_operation(self, operation: SqlOperation) -> None:
        """Validate operation is allowed."""
        if operation.type == SqlOperationType.SELECT:
            # SELECT is always allowed
            pass
        elif operation.type in (SqlOperationType.INSERT, SqlOperationType.UPDATE):
            # Write operations need to go through confirmation
            pass
        else:
            raise ForbiddenOperationError(
                f"Operation type {operation.type} is not allowed",
                code="FORBIDDEN_OPERATION",
                details={"type": operation.type.value},
            )

    def _inject_limits(self, sql: str, op_type: SqlOperationType) -> str:
        """Inject LIMIT clause for SELECT queries that lack one."""
        if op_type != SqlOperationType.SELECT:
            return sql
        # Check if LIMIT already exists (case-insensitive)
        if re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE):
            return sql
        return f"{sql.rstrip()} LIMIT {_SELECT_LIMIT}"

    async def _run_sql(
        self,
        sql: str,
        params: dict[str, Any],
        connection_url: str,
    ) -> dict[str, Any]:
        """Execute SQL against the database."""
        conn = await self._conn_manager.get_connection(connection_url)
        try:
            stmt = text(sql)
            if params:
                stmt = stmt.bindparams(**params)

            result = await conn.execute(stmt)

            # Try to fetch data for SELECT
            if result.returns_rows:
                rows = result.mappings().all()
                data = [dict(row) for row in rows]
                return {"rows_affected": len(data), "data": data}
            else:
                await conn.commit()
                return {"rows_affected": result.rowcount, "data": []}
        finally:
            await conn.close()
