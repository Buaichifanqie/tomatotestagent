from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from testagent.common.logging import get_logger
from testagent.db_ops.confirm_ui import ConfirmUI
from testagent.db_ops.connection import ConnectionManager
from testagent.db_ops.errors import ConfirmationRejectedError, DbOpsError, SQLGenerationError
from testagent.db_ops.models import DbConfig, DbOpsResult, ExecutionResult, SqlOperationType
from testagent.db_ops.schema import SchemaInspector
from testagent.db_ops.sql_generator import SQLGenerator
from testagent.db_ops.sql_executor import SQLExecutor

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


class DecisionLoop:
    """AI decision loop orchestrator for database operations.

    Flow:
    1. Analyze context and generate SQL via LLM
    2. For write operations: show confirmation UI
    3. Execute the SQL
    4. Feed results back to LLM for next iteration
    5. Repeat until goal achieved or max iterations reached
    """

    def __init__(
        self,
        config: DbConfig,
        llm: Any,  # ILLMProvider
        conn_manager: ConnectionManager,
        confirm_ui: ConfirmUI | None = None,
    ) -> None:
        self._config = config
        self._generator = SQLGenerator(llm)
        self._executor = SQLExecutor(conn_manager, config.timeout_seconds)
        self._inspector = SchemaInspector(conn_manager)
        self._conn_manager = conn_manager
        self._confirm_ui = confirm_ui or ConfirmUI()

    async def run(
        self,
        intent: str,
        connection_url: str,
        test_context: str = "",
    ) -> DbOpsResult:
        """Run the AI decision loop for a given intent.

        Args:
            intent: What the AI wants to accomplish (natural language)
            connection_url: Database connection URL
            test_context: Current test case context for the LLM

        Returns:
            DbOpsResult with all executed operations and cleanup plan
        """
        operations: list[ExecutionResult] = []
        previous_results = ""

        # Get schema context
        try:
            schema = await self._inspector.get_full_schema(connection_url)
            schema_context = self._inspector.format_schema_for_prompt(schema)
        except Exception as exc:
            return DbOpsResult(
                success=False,
                error_message=f"Schema inspection failed: {exc}",
            )

        for iteration in range(1, self._config.max_iterations + 1):
            logger.info("Decision loop iteration %d/%d", iteration, self._config.max_iterations)

            try:
                # Step 1: Generate SQL
                operation = await self._generator.generate(
                    intent=intent,
                    schema_context=schema_context,
                    test_context=test_context,
                    iteration=iteration,
                    previous_results=previous_results,
                )

                # Step 2: Confirmation for write operations
                if operation.type in (SqlOperationType.INSERT, SqlOperationType.UPDATE):
                    if self._config.confirm_writes:
                        confirmed = self._confirm_ui.confirm_operation(operation)
                        if not confirmed:
                            raise ConfirmationRejectedError(
                                "User rejected the operation",
                                code="USER_REJECTED",
                            )

                    # Add is_test flag for inserts if configured
                    if self._config.is_test_flag and operation.type == SqlOperationType.INSERT:
                        operation = self._add_is_test_flag(operation, schema)

                # Step 3: Execute
                result = await self._executor.execute(operation, connection_url)
                operations.append(result)

                # Display result
                self._confirm_ui.show_result(
                    success=result.success,
                    operation=operation,
                    rows_affected=result.rows_affected,
                    data=result.data,
                    error_message=result.error_message,
                    duration_ms=result.duration_ms,
                )

                if not result.success:
                    previous_results += f"\n迭代 {iteration}: 失败 - {result.error_message}"
                    continue

                # Step 4: Check if goal achieved
                if operation.type == SqlOperationType.SELECT and result.data:
                    # SELECT with data = likely goal achieved
                    return DbOpsResult(
                        success=True,
                        operations=operations,
                        iterations_used=iteration,
                        total_duration_ms=sum(r.duration_ms for r in operations),
                    )

                if operation.type in (SqlOperationType.INSERT, SqlOperationType.UPDATE):
                    # Write operation succeeded = goal achieved
                    return DbOpsResult(
                        success=True,
                        operations=operations,
                        iterations_used=iteration,
                        total_duration_ms=sum(r.duration_ms for r in operations),
                    )

                previous_results += f"\n迭代 {iteration}: 成功 - {result.rows_affected} 行受影响"

            except ConfirmationRejectedError:
                return DbOpsResult(
                    success=False,
                    operations=operations,
                    iterations_used=iteration,
                    total_duration_ms=sum(r.duration_ms for r in operations),
                    error_message="用户拒绝了操作",
                )
            except SQLGenerationError as exc:
                previous_results += f"\n迭代 {iteration}: SQL 生成失败 - {exc.message}"
                logger.warning("SQL generation failed: %s", exc)
                continue
            except DbOpsError as exc:
                return DbOpsResult(
                    success=False,
                    operations=operations,
                    iterations_used=iteration,
                    total_duration_ms=sum(r.duration_ms for r in operations),
                    error_message=exc.message,
                )

        # Max iterations reached
        return DbOpsResult(
            success=False,
            operations=operations,
            iterations_used=self._config.max_iterations,
            total_duration_ms=sum(r.duration_ms for r in operations),
            error_message=f"达到最大迭代次数 ({self._config.max_iterations})",
        )

    def _add_is_test_flag(self, operation: Any, schema: dict[str, Any]) -> Any:
        """Add is_test=1 to INSERT operations if the table has the column."""
        # Extract table name from INSERT INTO <table>
        import re
        match = re.search(r"INSERT\s+INTO\s+(\w+)", operation.sql, re.IGNORECASE)
        if not match:
            return operation

        table_name = match.group(1)
        table_info = schema.get(table_name, {})
        has_is_test = table_info.get("has_is_test", False)

        if not has_is_test:
            return operation

        # Add is_test to the SQL
        sql = operation.sql
        if "(" in sql and ")" in sql:
            # INSERT INTO table (col1, col2) VALUES (:v1, :v2)
            # Add is_test column
            sql = sql.replace(")", ", is_test)", 1)  # Add to columns
            # Find the last VALUES (...) and add the param
            last_paren = sql.rfind(")")
            if last_paren != -1:
                sql = sql[:last_paren] + ", 1" + sql[last_paren:]

        params = dict(operation.params)
        return operation.model_copy(update={"sql": sql, "params": params})
