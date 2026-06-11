"""Tests for testagent.db_ops.models — Pydantic models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from testagent.db_ops.models import (
    CleanupPlan,
    DbConfig,
    DbOpsResult,
    ExecutionResult,
    SqlOperation,
    SqlOperationType,
)


# ---------------------------------------------------------------------------
# SqlOperationType
# ---------------------------------------------------------------------------


class TestSqlOperationType:
    def test_enum_values(self):
        assert SqlOperationType.SELECT == "SELECT"
        assert SqlOperationType.INSERT == "INSERT"
        assert SqlOperationType.UPDATE == "UPDATE"

    def test_enum_from_value(self):
        assert SqlOperationType("SELECT") is SqlOperationType.SELECT
        assert SqlOperationType("INSERT") is SqlOperationType.INSERT

    def test_enum_is_str(self):
        assert isinstance(SqlOperationType.SELECT, str)
        assert SqlOperationType.SELECT == "SELECT"

    def test_delete_not_in_enum(self):
        with pytest.raises(ValueError):
            SqlOperationType("DELETE")


# ---------------------------------------------------------------------------
# DbConfig
# ---------------------------------------------------------------------------


class TestDbConfig:
    def test_defaults(self):
        config = DbConfig()
        assert config.connection_url == ""
        assert config.max_iterations == 5
        assert config.timeout_seconds == 30
        assert config.confirm_writes is True
        assert config.is_test_flag is True
        assert len(config.allowed_operations) == 3

    def test_custom_values(self):
        config = DbConfig(
            connection_url="mysql+aiomysql://user:pass@host/db",
            max_iterations=10,
            timeout_seconds=60,
            confirm_writes=False,
            is_test_flag=False,
        )
        assert config.connection_url == "mysql+aiomysql://user:pass@host/db"
        assert config.max_iterations == 10
        assert config.timeout_seconds == 60
        assert config.confirm_writes is False
        assert config.is_test_flag is False

    def test_max_iterations_below_minimum(self):
        with pytest.raises(ValidationError):
            DbConfig(max_iterations=0)

    def test_max_iterations_above_maximum(self):
        with pytest.raises(ValidationError):
            DbConfig(max_iterations=21)

    def test_timeout_below_minimum(self):
        with pytest.raises(ValidationError):
            DbConfig(timeout_seconds=4)

    def test_allowed_operations_default_includes_all_three(self):
        config = DbConfig()
        ops = set(config.allowed_operations)
        assert SqlOperationType.SELECT in ops
        assert SqlOperationType.INSERT in ops
        assert SqlOperationType.UPDATE in ops

    def test_allowed_operations_custom(self):
        config = DbConfig(allowed_operations=[SqlOperationType.SELECT])
        assert config.allowed_operations == [SqlOperationType.SELECT]

    def test_max_iterations_boundary_valid(self):
        config = DbConfig(max_iterations=1)
        assert config.max_iterations == 1
        config = DbConfig(max_iterations=20)
        assert config.max_iterations == 20

    def test_timeout_boundary_valid(self):
        config = DbConfig(timeout_seconds=5)
        assert config.timeout_seconds == 5


# ---------------------------------------------------------------------------
# SqlOperation
# ---------------------------------------------------------------------------


class TestSqlOperation:
    def test_basic_creation(self):
        op = SqlOperation(
            type=SqlOperationType.SELECT,
            sql="SELECT * FROM users",
        )
        assert op.type == SqlOperationType.SELECT
        assert op.sql == "SELECT * FROM users"
        assert op.params == {}
        assert op.description == ""
        assert op.is_cleanup is False

    def test_with_params(self):
        op = SqlOperation(
            type=SqlOperationType.INSERT,
            sql="INSERT INTO users (name) VALUES (:name)",
            params={"name": "test"},
            description="Insert test user",
            is_cleanup=False,
        )
        assert op.params == {"name": "test"}
        assert op.description == "Insert test user"

    def test_is_cleanup_flag(self):
        op = SqlOperation(
            type=SqlOperationType.SELECT,
            sql="DELETE FROM users WHERE is_test = 1",
            is_cleanup=True,
        )
        assert op.is_cleanup is True

    def test_missing_type_raises(self):
        with pytest.raises(ValidationError):
            SqlOperation(sql="SELECT 1")

    def test_missing_sql_raises(self):
        with pytest.raises(ValidationError):
            SqlOperation(type=SqlOperationType.SELECT)

    def test_model_dump(self):
        op = SqlOperation(
            type=SqlOperationType.SELECT,
            sql="SELECT 1",
            params={"x": 1},
            description="test",
        )
        d = op.model_dump()
        assert d["type"] == "SELECT"
        assert d["sql"] == "SELECT 1"
        assert d["params"] == {"x": 1}


# ---------------------------------------------------------------------------
# ExecutionResult
# ---------------------------------------------------------------------------


class TestExecutionResult:
    def test_success_result(self):
        op = SqlOperation(type=SqlOperationType.SELECT, sql="SELECT 1")
        result = ExecutionResult(
            success=True,
            operation=op,
            rows_affected=1,
            data=[{"id": 1}],
            duration_ms=50,
        )
        assert result.success is True
        assert result.rows_affected == 1
        assert result.data == [{"id": 1}]
        assert result.error_message == ""
        assert result.duration_ms == 50

    def test_failure_result(self):
        op = SqlOperation(type=SqlOperationType.INSERT, sql="INSERT INTO t VALUES (1)")
        result = ExecutionResult(
            success=False,
            operation=op,
            error_message="table not found",
            duration_ms=10,
        )
        assert result.success is False
        assert result.error_message == "table not found"
        assert result.rows_affected == 0
        assert result.data == []

    def test_defaults(self):
        op = SqlOperation(type=SqlOperationType.SELECT, sql="SELECT 1")
        result = ExecutionResult(success=True, operation=op)
        assert result.rows_affected == 0
        assert result.data == []
        assert result.error_message == ""
        assert result.duration_ms == 0

    def test_missing_operation_raises(self):
        with pytest.raises(ValidationError):
            ExecutionResult(success=True)


# ---------------------------------------------------------------------------
# CleanupPlan
# ---------------------------------------------------------------------------


class TestCleanupPlan:
    def test_empty_plan(self):
        plan = CleanupPlan()
        assert plan.operations == []
        assert plan.description == ""

    def test_with_operations(self):
        ops = [
            SqlOperation(type=SqlOperationType.SELECT, sql="DELETE FROM t WHERE id=1"),
        ]
        plan = CleanupPlan(operations=ops, description="cleanup test data")
        assert len(plan.operations) == 1
        assert plan.description == "cleanup test data"

    def test_operations_default_factory(self):
        p1 = CleanupPlan()
        p2 = CleanupPlan()
        p1.operations.append(
            SqlOperation(type=SqlOperationType.SELECT, sql="DELETE FROM t")
        )
        assert len(p1.operations) == 1
        assert len(p2.operations) == 0  # default_factory creates separate lists


# ---------------------------------------------------------------------------
# DbOpsResult
# ---------------------------------------------------------------------------


class TestDbOpsResult:
    def test_success_result(self):
        op = SqlOperation(type=SqlOperationType.SELECT, sql="SELECT 1")
        exec_result = ExecutionResult(success=True, operation=op, duration_ms=50)
        result = DbOpsResult(
            success=True,
            operations=[exec_result],
            iterations_used=1,
            total_duration_ms=50,
        )
        assert result.success is True
        assert len(result.operations) == 1
        assert result.cleanup_plan is None
        assert result.iterations_used == 1
        assert result.total_duration_ms == 50
        assert result.error_message == ""

    def test_failure_result_with_error(self):
        result = DbOpsResult(
            success=False,
            error_message="Schema inspection failed",
        )
        assert result.success is False
        assert result.error_message == "Schema inspection failed"
        assert result.operations == []
        assert result.cleanup_plan is None

    def test_with_cleanup_plan(self):
        plan = CleanupPlan(description="cleanup")
        result = DbOpsResult(
            success=True,
            cleanup_plan=plan,
        )
        assert result.cleanup_plan is not None
        assert result.cleanup_plan.description == "cleanup"

    def test_defaults(self):
        result = DbOpsResult(success=False)
        assert result.operations == []
        assert result.cleanup_plan is None
        assert result.iterations_used == 0
        assert result.total_duration_ms == 0
        assert result.error_message == ""

    def test_missing_success_raises(self):
        with pytest.raises(ValidationError):
            DbOpsResult()
