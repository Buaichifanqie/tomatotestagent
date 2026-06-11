"""Tests for testagent.db_ops.sql_executor — SQLExecutor."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.db_ops.errors import ForbiddenOperationError
from testagent.db_ops.models import SqlOperation, SqlOperationType
from testagent.db_ops.sql_executor import SQLExecutor, _SELECT_LIMIT


# ---------------------------------------------------------------------------
# _validate_operation
# ---------------------------------------------------------------------------


class TestValidateOperation:
    def test_select_allowed(self):
        executor = SQLExecutor(AsyncMock(), timeout_seconds=30)
        op = SqlOperation(type=SqlOperationType.SELECT, sql="SELECT 1")
        # Should not raise
        executor._validate_operation(op)

    def test_insert_allowed(self):
        executor = SQLExecutor(AsyncMock(), timeout_seconds=30)
        op = SqlOperation(type=SqlOperationType.INSERT, sql="INSERT INTO t VALUES (1)")
        executor._validate_operation(op)

    def test_update_allowed(self):
        executor = SQLExecutor(AsyncMock(), timeout_seconds=30)
        op = SqlOperation(type=SqlOperationType.UPDATE, sql="UPDATE t SET x=1")
        executor._validate_operation(op)

    def test_select_allowed(self):
        executor = SQLExecutor(AsyncMock(), timeout_seconds=30)
        op = SqlOperation(type=SqlOperationType.SELECT, sql="SELECT 1")
        executor._validate_operation(op)  # Should not raise


# ---------------------------------------------------------------------------
# _inject_limits
# ---------------------------------------------------------------------------


class TestInjectLimits:
    def test_select_without_limit_gets_limit(self):
        executor = SQLExecutor(AsyncMock(), timeout_seconds=30)
        result = executor._inject_limits("SELECT * FROM users", SqlOperationType.SELECT)
        assert result == f"SELECT * FROM users LIMIT {_SELECT_LIMIT}"

    def test_select_with_limit_unchanged(self):
        executor = SQLExecutor(AsyncMock(), timeout_seconds=30)
        result = executor._inject_limits("SELECT * FROM users LIMIT 10", SqlOperationType.SELECT)
        assert result == "SELECT * FROM users LIMIT 10"

    def test_select_with_limit_case_insensitive(self):
        executor = SQLExecutor(AsyncMock(), timeout_seconds=30)
        result = executor._inject_limits("SELECT * FROM users limit 5", SqlOperationType.SELECT)
        assert result == "SELECT * FROM users limit 5"

    def test_insert_not_modified(self):
        executor = SQLExecutor(AsyncMock(), timeout_seconds=30)
        sql = "INSERT INTO users (name) VALUES ('test')"
        result = executor._inject_limits(sql, SqlOperationType.INSERT)
        assert result == sql

    def test_update_not_modified(self):
        executor = SQLExecutor(AsyncMock(), timeout_seconds=30)
        sql = "UPDATE users SET name='test'"
        result = executor._inject_limits(sql, SqlOperationType.UPDATE)
        assert result == sql

    def test_select_trailing_whitespace(self):
        executor = SQLExecutor(AsyncMock(), timeout_seconds=30)
        result = executor._inject_limits("SELECT * FROM users  ", SqlOperationType.SELECT)
        assert result == f"SELECT * FROM users LIMIT {_SELECT_LIMIT}"


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_select_success(self):
        conn_mgr = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_mappings = MagicMock()
        mock_mappings.all.return_value = [{"id": 1, "name": "Alice"}]
        mock_result.mappings.return_value = mock_mappings
        conn.execute.return_value = mock_result
        conn_mgr.get_connection.return_value = conn

        executor = SQLExecutor(conn_mgr, timeout_seconds=30)
        op = SqlOperation(type=SqlOperationType.SELECT, sql="SELECT * FROM users")
        result = await executor.execute(op, "mysql://host/db")

        assert result.success is True
        assert result.data == [{"id": 1, "name": "Alice"}]
        assert result.rows_affected == 1
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_execute_insert_success(self):
        conn_mgr = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.returns_rows = False
        mock_result.rowcount = 1
        conn.execute.return_value = mock_result
        conn_mgr.get_connection.return_value = conn

        executor = SQLExecutor(conn_mgr, timeout_seconds=30)
        op = SqlOperation(
            type=SqlOperationType.INSERT,
            sql="INSERT INTO users (name) VALUES (:name)",
            params={"name": "test"},
        )
        result = await executor.execute(op, "mysql://host/db")

        assert result.success is True
        assert result.rows_affected == 1
        assert result.data == []

    @pytest.mark.asyncio
    async def test_execute_with_params(self):
        conn_mgr = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_mappings = MagicMock()
        mock_mappings.all.return_value = []
        mock_result.mappings.return_value = mock_mappings
        conn.execute.return_value = mock_result
        conn_mgr.get_connection.return_value = conn

        executor = SQLExecutor(conn_mgr, timeout_seconds=30)
        op = SqlOperation(
            type=SqlOperationType.SELECT,
            sql="SELECT * FROM users WHERE id = :id",
            params={"id": 42},
        )
        result = await executor.execute(op, "mysql://host/db")

        assert result.success is True
        # Verify bindparams was called
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_sql_error_returns_failure(self):
        conn_mgr = AsyncMock()
        conn = AsyncMock()
        conn.execute.side_effect = RuntimeError("table does not exist")
        conn_mgr.get_connection.return_value = conn

        executor = SQLExecutor(conn_mgr, timeout_seconds=30)
        op = SqlOperation(type=SqlOperationType.SELECT, sql="SELECT * FROM nonexistent")
        result = await executor.execute(op, "mysql://host/db")

        assert result.success is False
        assert "table does not exist" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_timeout_returns_failure(self):
        conn_mgr = AsyncMock()
        conn = AsyncMock()
        conn.execute.side_effect = TimeoutError()
        conn_mgr.get_connection.return_value = conn

        executor = SQLExecutor(conn_mgr, timeout_seconds=5)
        op = SqlOperation(type=SqlOperationType.SELECT, sql="SELECT * FROM slow_table")
        result = await executor.execute(op, "mysql://host/db")

        assert result.success is False
        assert "timed out" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_always_closes_connection(self):
        conn_mgr = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_mappings = MagicMock()
        mock_mappings.all.return_value = []
        mock_result.mappings.return_value = mock_mappings
        conn.execute.return_value = mock_result
        conn_mgr.get_connection.return_value = conn

        executor = SQLExecutor(conn_mgr, timeout_seconds=30)
        op = SqlOperation(type=SqlOperationType.SELECT, sql="SELECT 1")
        await executor.execute(op, "mysql://host/db")

        conn.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_select_succeeds(self):
        conn_mgr = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.mappings.return_value.all.return_value = [{"id": 1}]
        conn.execute.return_value = mock_result
        conn_mgr.get_connection.return_value = conn

        executor = SQLExecutor(conn_mgr, timeout_seconds=30)
        op = SqlOperation(type=SqlOperationType.SELECT, sql="SELECT 1")
        result = await executor.execute(op, "mysql://host/db")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_select_injects_limit(self):
        conn_mgr = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_mappings = MagicMock()
        mock_mappings.all.return_value = []
        mock_result.mappings.return_value = mock_mappings
        conn.execute.return_value = mock_result
        conn_mgr.get_connection.return_value = conn

        executor = SQLExecutor(conn_mgr, timeout_seconds=30)
        op = SqlOperation(type=SqlOperationType.SELECT, sql="SELECT * FROM users")
        await executor.execute(op, "mysql://host/db")

        # The SQL passed to conn.execute should have LIMIT injected
        call_args = conn.execute.call_args
        stmt = call_args[0][0]
        assert "LIMIT" in str(stmt)

    @pytest.mark.asyncio
    async def test_execute_insert_commits(self):
        conn_mgr = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.returns_rows = False
        mock_result.rowcount = 1
        conn.execute.return_value = mock_result
        conn_mgr.get_connection.return_value = conn

        executor = SQLExecutor(conn_mgr, timeout_seconds=30)
        op = SqlOperation(type=SqlOperationType.INSERT, sql="INSERT INTO t VALUES (1)")
        await executor.execute(op, "mysql://host/db")

        conn.commit.assert_awaited_once()
