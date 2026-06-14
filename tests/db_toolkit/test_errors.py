from __future__ import annotations

from testagent.common.errors import TestAgentError
from testagent.db_toolkit.errors import (
    DbConnectionError,
    DbToolkitError,
    EnvironmentViolationError,
    SafetyViolationError,
    SchemaInspectionError,
    SqlExecutionError,
)


def test_hierarchy():
    assert issubclass(DbToolkitError, TestAgentError)
    assert issubclass(DbConnectionError, DbToolkitError)
    assert issubclass(EnvironmentViolationError, DbToolkitError)
    assert issubclass(SafetyViolationError, DbToolkitError)
    assert issubclass(SchemaInspectionError, DbToolkitError)
    assert issubclass(SqlExecutionError, DbToolkitError)


def test_instantiation():
    e = EnvironmentViolationError("prod cannot write", code="PROD_WRITE")
    assert str(e).startswith("[PROD_WRITE]")
    assert "prod cannot write" in str(e)
    assert e.details == {}


def test_details():
    e = SafetyViolationError("multi-statement", code="MULTI_STMT", details={"sql": "a; b"})
    assert e.details == {"sql": "a; b"}
