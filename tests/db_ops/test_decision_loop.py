"""Tests for testagent.db_ops.decision_loop — DecisionLoop."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.db_ops.decision_loop import DecisionLoop
from testagent.db_ops.errors import ConfirmationRejectedError, DbOpsError, SQLGenerationError
from testagent.db_ops.models import (
    DbConfig,
    DbOpsResult,
    ExecutionResult,
    SqlOperation,
    SqlOperationType,
)


def _make_config(**overrides) -> DbConfig:
    defaults = {
        "connection_url": "mysql+aiomysql://user:pass@host/db",
        "max_iterations": 3,
        "timeout_seconds": 30,
        "confirm_writes": False,
        "is_test_flag": False,
    }
    defaults.update(overrides)
    return DbConfig(**defaults)


def _make_operation(
    op_type: SqlOperationType = SqlOperationType.SELECT,
    sql: str = "SELECT 1",
    params: dict | None = None,
) -> SqlOperation:
    return SqlOperation(type=op_type, sql=sql, params=params or {})


def _make_exec_result(success: bool = True, op: SqlOperation | None = None, **kwargs) -> ExecutionResult:
    if op is None:
        op = _make_operation()
    defaults = {"success": success, "operation": op, "rows_affected": 0, "duration_ms": 10}
    defaults.update(kwargs)
    return ExecutionResult(**defaults)


class TestDecisionLoopRun:
    @pytest.mark.asyncio
    async def test_schema_inspection_failure(self):
        config = _make_config()
        conn_mgr = AsyncMock()
        llm = AsyncMock()

        loop = DecisionLoop(config, llm, conn_mgr)
        loop._inspector.get_full_schema = AsyncMock(side_effect=RuntimeError("db down"))

        result = await loop.run("test", "mysql://host/db")
        assert result.success is False
        assert "Schema inspection failed" in result.error_message

    @pytest.mark.asyncio
    async def test_select_with_data_returns_success(self):
        config = _make_config()
        conn_mgr = AsyncMock()
        llm = AsyncMock()

        loop = DecisionLoop(config, llm, conn_mgr)
        loop._inspector.get_full_schema = AsyncMock(return_value={})
        loop._inspector.format_schema_for_prompt = MagicMock(return_value="schema")

        select_op = _make_operation(SqlOperationType.SELECT, "SELECT * FROM users")
        loop._generator.generate = AsyncMock(return_value=select_op)

        select_result = _make_exec_result(
            success=True,
            op=select_op,
            data=[{"id": 1, "name": "Alice"}],
            rows_affected=1,
        )
        loop._executor.execute = AsyncMock(return_value=select_result)
        loop._confirm_ui.show_result = MagicMock()

        result = await loop.run("list users", "mysql://host/db")
        assert result.success is True
        assert result.iterations_used == 1
        assert len(result.operations) == 1

    @pytest.mark.asyncio
    async def test_max_iterations_reached(self):
        config = _make_config(max_iterations=2)
        conn_mgr = AsyncMock()
        llm = AsyncMock()

        loop = DecisionLoop(config, llm, conn_mgr)
        loop._inspector.get_full_schema = AsyncMock(return_value={})
        loop._inspector.format_schema_for_prompt = MagicMock(return_value="schema")

        # Each iteration returns a SELECT with no data (doesn't trigger success)
        select_op = _make_operation(SqlOperationType.SELECT, "SELECT * FROM t WHERE x=1")
        loop._generator.generate = AsyncMock(return_value=select_op)

        select_result = _make_exec_result(success=True, op=select_op, rows_affected=0, data=[])
        loop._executor.execute = AsyncMock(return_value=select_result)
        loop._confirm_ui.show_result = MagicMock()

        result = await loop.run("find data", "mysql://host/db")
        assert result.success is False
        assert "最大迭代次数" in result.error_message
        assert result.iterations_used == 2
        assert len(result.operations) == 2

    @pytest.mark.asyncio
    async def test_user_rejection_stops_loop(self):
        config = _make_config(confirm_writes=True, max_iterations=5)
        conn_mgr = AsyncMock()
        llm = AsyncMock()
        confirm_ui = MagicMock()
        confirm_ui.confirm_operation.return_value = False  # user rejects

        loop = DecisionLoop(config, llm, conn_mgr, confirm_ui=confirm_ui)
        loop._inspector.get_full_schema = AsyncMock(return_value={})
        loop._inspector.format_schema_for_prompt = MagicMock(return_value="schema")

        insert_op = _make_operation(SqlOperationType.INSERT, "INSERT INTO t VALUES (1)")
        loop._generator.generate = AsyncMock(return_value=insert_op)
        loop._confirm_ui.show_result = MagicMock()

        result = await loop.run("insert data", "mysql://host/db")
        assert result.success is False
        assert "用户拒绝" in result.error_message
        assert result.iterations_used == 1

    @pytest.mark.asyncio
    async def test_sql_generation_error_continues(self):
        config = _make_config(max_iterations=3)
        conn_mgr = AsyncMock()
        llm = AsyncMock()

        loop = DecisionLoop(config, llm, conn_mgr)
        loop._inspector.get_full_schema = AsyncMock(return_value={})
        loop._inspector.format_schema_for_prompt = MagicMock(return_value="schema")

        # First two iterations: SQL generation fails; third: SELECT succeeds
        select_op = _make_operation(SqlOperationType.SELECT, "SELECT 1")
        loop._generator.generate = AsyncMock(
            side_effect=[
                SQLGenerationError("bad json", code="INVALID_JSON"),
                SQLGenerationError("empty sql", code="EMPTY_SQL"),
                select_op,
            ]
        )

        select_result = _make_exec_result(
            success=True,
            op=select_op,
            data=[{"1": 1}],
            rows_affected=1,
        )
        loop._executor.execute = AsyncMock(return_value=select_result)
        loop._confirm_ui.show_result = MagicMock()

        result = await loop.run("test", "mysql://host/db")
        assert result.success is True
        assert result.iterations_used == 3

    @pytest.mark.asyncio
    async def test_db_ops_error_stops_loop(self):
        config = _make_config(max_iterations=5)
        conn_mgr = AsyncMock()
        llm = AsyncMock()

        loop = DecisionLoop(config, llm, conn_mgr)
        loop._inspector.get_full_schema = AsyncMock(return_value={})
        loop._inspector.format_schema_for_prompt = MagicMock(return_value="schema")

        select_op = _make_operation(SqlOperationType.SELECT, "SELECT 1")
        loop._generator.generate = AsyncMock(return_value=select_op)

        loop._executor.execute = AsyncMock(
            side_effect=DbOpsError("forbidden", code="FORBIDDEN")
        )
        loop._confirm_ui.show_result = MagicMock()

        result = await loop.run("test", "mysql://host/db")
        assert result.success is False
        assert "forbidden" in result.error_message

    @pytest.mark.asyncio
    async def test_failed_execution_continues(self):
        config = _make_config(max_iterations=2)
        conn_mgr = AsyncMock()
        llm = AsyncMock()

        loop = DecisionLoop(config, llm, conn_mgr)
        loop._inspector.get_full_schema = AsyncMock(return_value={})
        loop._inspector.format_schema_for_prompt = MagicMock(return_value="schema")

        select_op = _make_operation(SqlOperationType.SELECT, "SELECT * FROM t")
        loop._generator.generate = AsyncMock(return_value=select_op)

        # First iteration fails, second succeeds with data
        fail_result = _make_exec_result(
            success=False,
            op=select_op,
            error_message="syntax error",
        )
        success_result = _make_exec_result(
            success=True,
            op=select_op,
            data=[{"id": 1}],
            rows_affected=1,
        )
        loop._executor.execute = AsyncMock(side_effect=[fail_result, success_result])
        loop._confirm_ui.show_result = MagicMock()

        result = await loop.run("query", "mysql://host/db")
        assert result.success is True
        assert len(result.operations) == 2

    @pytest.mark.asyncio
    async def test_is_test_flag_added_to_insert(self):
        config = _make_config(is_test_flag=True, confirm_writes=False)
        conn_mgr = AsyncMock()
        llm = AsyncMock()

        loop = DecisionLoop(config, llm, conn_mgr)
        schema = {
            "users": {
                "columns": [],
                "has_is_test": True,
            }
        }
        loop._inspector.get_full_schema = AsyncMock(return_value=schema)
        loop._inspector.format_schema_for_prompt = MagicMock(return_value="schema")

        insert_op = _make_operation(
            SqlOperationType.INSERT,
            "INSERT INTO users (name) VALUES (:name)",
            params={"name": "test"},
        )
        loop._generator.generate = AsyncMock(return_value=insert_op)

        insert_result = _make_exec_result(success=True, op=insert_op, rows_affected=1)
        loop._executor.execute = AsyncMock(return_value=insert_result)
        loop._confirm_ui.show_result = MagicMock()

        await loop.run("insert user", "mysql://host/db")

        # Verify executor was called with modified SQL containing is_test
        call_args = loop._executor.execute.call_args
        executed_op = call_args[0][0]
        assert "is_test" in executed_op.sql

    @pytest.mark.asyncio
    async def test_total_duration_calculated(self):
        config = _make_config(max_iterations=2)
        conn_mgr = AsyncMock()
        llm = AsyncMock()

        loop = DecisionLoop(config, llm, conn_mgr)
        loop._inspector.get_full_schema = AsyncMock(return_value={})
        loop._inspector.format_schema_for_prompt = MagicMock(return_value="schema")

        select_op = _make_operation(SqlOperationType.SELECT, "SELECT 1")
        loop._generator.generate = AsyncMock(return_value=select_op)

        result1 = _make_exec_result(success=True, op=select_op, rows_affected=0, duration_ms=100, data=[])
        result2 = _make_exec_result(success=True, op=select_op, rows_affected=1, duration_ms=200, data=[{"1": 1}])
        loop._executor.execute = AsyncMock(side_effect=[result1, result2])
        loop._confirm_ui.show_result = MagicMock()

        result = await loop.run("test", "mysql://host/db")
        assert result.total_duration_ms == 300

    @pytest.mark.asyncio
    async def test_confirm_writes_shows_ui(self):
        config = _make_config(confirm_writes=True)
        conn_mgr = AsyncMock()
        llm = AsyncMock()
        confirm_ui = MagicMock()
        confirm_ui.confirm_operation.return_value = True  # user confirms

        loop = DecisionLoop(config, llm, conn_mgr, confirm_ui=confirm_ui)
        loop._inspector.get_full_schema = AsyncMock(return_value={})
        loop._inspector.format_schema_for_prompt = MagicMock(return_value="schema")

        insert_op = _make_operation(SqlOperationType.INSERT, "INSERT INTO t VALUES (1)")
        loop._generator.generate = AsyncMock(return_value=insert_op)

        insert_result = _make_exec_result(success=True, op=insert_op, rows_affected=1)
        loop._executor.execute = AsyncMock(return_value=insert_result)
        loop._confirm_ui.show_result = MagicMock()

        await loop.run("insert", "mysql://host/db")
        # confirm_operation is called once per iteration (INSERT triggers confirmation)
        assert confirm_ui.confirm_operation.call_count >= 1
