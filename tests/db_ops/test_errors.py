"""Tests for testagent.db_ops.errors — Error hierarchy."""
from __future__ import annotations

import pytest

from testagent.common.errors import TestAgentError
from testagent.db_ops.errors import (
    ConfirmationRejectedError,
    DbConnectionError,
    DbOpsError,
    ExecutionTimeoutError,
    ForbiddenOperationError,
    SchemaInspectionError,
    SQLGenerationError,
)


class TestDbOpsErrorHierarchy:
    """Verify the error class hierarchy and basic behaviour."""

    def test_db_ops_error_is_test_agent_error(self):
        assert issubclass(DbOpsError, TestAgentError)

    def test_all_errors_inherit_from_db_ops_error(self):
        for cls in (
            DbConnectionError,
            ForbiddenOperationError,
            ExecutionTimeoutError,
            ConfirmationRejectedError,
            SchemaInspectionError,
            SQLGenerationError,
        ):
            assert issubclass(cls, DbOpsError), f"{cls.__name__} must inherit DbOpsError"

    def test_all_errors_inherit_from_test_agent_error(self):
        for cls in (
            DbConnectionError,
            ForbiddenOperationError,
            ExecutionTimeoutError,
            ConfirmationRejectedError,
            SchemaInspectionError,
            SQLGenerationError,
        ):
            assert issubclass(cls, TestAgentError), f"{cls.__name__} must inherit TestAgentError"


class TestDbOpsErrorInstances:
    """Test instantiation and attribute access."""

    def test_basic_message(self):
        err = DbOpsError("something broke")
        assert str(err).startswith("[UNKNOWN]")
        assert "something broke" in str(err)
        assert err.message == "something broke"
        assert err.code == "UNKNOWN"
        assert err.details == {}

    def test_with_code_and_details(self):
        err = DbConnectionError(
            "cannot connect",
            code="CONN_FAILED",
            details={"host": "db.example.com"},
        )
        assert err.code == "CONN_FAILED"
        assert err.details == {"host": "db.example.com"}
        assert "CONN_FAILED" in str(err)

    def test_repr(self):
        err = SQLGenerationError("bad SQL", code="BAD_SQL")
        r = repr(err)
        assert "SQLGenerationError" in r
        assert "bad SQL" in r
        assert "BAD_SQL" in r

    def test_can_be_caught_as_exception(self):
        with pytest.raises(Exception):
            raise ForbiddenOperationError("no DELETE")

    def test_can_be_caught_as_db_ops_error(self):
        with pytest.raises(DbOpsError):
            raise ExecutionTimeoutError("timed out")

    def test_can_be_caught_as_specific_error(self):
        with pytest.raises(SchemaInspectionError):
            raise SchemaInspectionError("bad schema", code="BAD_SCHEMA")

    def test_confirmation_rejected(self):
        err = ConfirmationRejectedError("user said no", code="USER_REJECTED")
        assert err.message == "user said no"
        assert err.code == "USER_REJECTED"

    def test_details_default_is_empty_dict(self):
        err = DbOpsError("test")
        assert err.details == {}
        # Ensure it's a fresh dict each time
        err2 = DbOpsError("test2")
        assert err.details is not err2.details

    def test_message_attribute_accessible(self):
        """TestAgentError stores message as an attribute."""
        err = SQLGenerationError("LLM failed")
        assert err.message == "LLM failed"

    def test_raise_and_catch_preserves_chain(self):
        original = ValueError("root cause")
        try:
            try:
                raise original
            except ValueError as exc:
                raise DbConnectionError("wrapped", code="WRAPPED") from exc
        except DbConnectionError as caught:
            assert caught.__cause__ is original
