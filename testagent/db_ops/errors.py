from __future__ import annotations

from testagent.common.errors import TestAgentError


class DbOpsError(TestAgentError):
    """Base error for the AI Database Operation Engine."""
    pass


class DbConnectionError(DbOpsError):
    """Failed to connect to the target database."""
    pass


class ForbiddenOperationError(DbOpsError):
    """Attempted a disallowed operation (e.g. DELETE)."""
    pass


class ExecutionTimeoutError(DbOpsError):
    """SQL execution exceeded the configured timeout."""
    pass


class ConfirmationRejectedError(DbOpsError):
    """User rejected a write operation in the confirmation UI."""
    pass


class SchemaInspectionError(DbOpsError):
    """Failed to inspect the database schema."""
    pass


class SQLGenerationError(DbOpsError):
    """LLM failed to generate valid SQL."""
    pass
