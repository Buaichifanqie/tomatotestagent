from __future__ import annotations

import pytest
from testagent.db_toolkit.models import (
    DbEnv,
    Environment,
    ExecutionResult,
    SqlOpType,
)


class TestEnvironment:
    def test_values(self):
        assert Environment.TEST == "test"
        assert Environment.PRODUCTION == "production"


class TestSqlOpType:
    def test_values(self):
        assert SqlOpType.SELECT == "SELECT"
        assert SqlOpType.INSERT == "INSERT"
        assert SqlOpType.UPDATE == "UPDATE"
        assert SqlOpType.DELETE == "DELETE"


class TestDbEnv:
    def test_test_env_allows_all(self):
        env = DbEnv(
            level=Environment.TEST,
            connection_url="mysql://test_user:pass@localhost/testdb",
            detected_by="url_pattern",
        )
        assert env.allow_write is True
        assert env.allow_delete is True

    def test_prod_env_read_only(self):
        env = DbEnv(
            level=Environment.PRODUCTION,
            connection_url="mysql://user:pass@localhost/proddb",
            detected_by="default",
        )
        assert env.allow_write is False
        assert env.allow_delete is False

    def test_frozen(self):
        env = DbEnv(
            level=Environment.TEST,
            connection_url="sqlite:///test.db",
            detected_by="url_pattern",
        )
        with pytest.raises(AttributeError):
            env.level = Environment.PRODUCTION


class TestExecutionResult:
    def test_success_result(self):
        r = ExecutionResult(success=True, rows_affected=3, data=[{"id": 1}])
        assert r.success is True
        assert r.rows_affected == 3
        assert r.data == [{"id": 1}]
        assert r.error_message == ""

    def test_failure_result(self):
        r = ExecutionResult(success=False, error_message="timeout")
        assert r.success is False
        assert r.error_message == "timeout"
        assert r.rows_affected == 0

    def test_defaults(self):
        r = ExecutionResult(success=True)
        assert r.rows_affected == 0
        assert r.data == []
        assert r.error_message == ""
        assert r.duration_ms == 0
