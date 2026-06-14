from __future__ import annotations

from testagent.common.errors import TestAgentError


class DbToolkitError(TestAgentError):
    pass


class DbConnectionError(DbToolkitError):
    pass


class EnvironmentViolationError(DbToolkitError):
    pass


class SafetyViolationError(DbToolkitError):
    pass


class SchemaInspectionError(DbToolkitError):
    pass


class SqlExecutionError(DbToolkitError):
    pass
