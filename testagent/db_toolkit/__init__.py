"""AI Database Toolkit — autonomous, environment-aware database tools for the agent."""

from testagent.db_toolkit.cleanup import CleanupTracker
from testagent.db_toolkit.connection import ConnectionManager
from testagent.db_toolkit.env import detect_environment
from testagent.db_toolkit.errors import (
    DbConnectionError,
    DbToolkitError,
    EnvironmentViolationError,
    SafetyViolationError,
    SchemaInspectionError,
    SqlExecutionError,
)
from testagent.db_toolkit.models import DbEnv, Environment, ExecutionResult, SqlOpType
from testagent.db_toolkit.safety import SafetyGuard
from testagent.db_toolkit.schema import ColumnInfo, SchemaInspector, TableInfo
from testagent.db_toolkit.tools import (
    DB_TOOL_DEFINITIONS,
    ToolkitState,
    handle_db_cleanup,
    handle_db_execute,
    handle_db_inspect,
    handle_db_query,
)

__all__ = [
    "CleanupTracker",
    "ColumnInfo",
    "ConnectionManager",
    "DB_TOOL_DEFINITIONS",
    "DbConnectionError",
    "DbEnv",
    "DbToolkitError",
    "detect_environment",
    "Environment",
    "EnvironmentViolationError",
    "ExecutionResult",
    "handle_db_cleanup",
    "handle_db_execute",
    "handle_db_inspect",
    "handle_db_query",
    "SafetyGuard",
    "SafetyViolationError",
    "SchemaInspectionError",
    "SchemaInspector",
    "SqlExecutionError",
    "SqlOpType",
    "TableInfo",
    "ToolkitState",
]
