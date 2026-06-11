"""AI Database Operation Engine.

Provides autonomous database interaction for test automation,
with LLM-powered SQL generation, safety controls, and user confirmation.

Usage:
    from testagent.db_ops import DecisionLoop, DbConfig, ConnectionManager

    config = DbConfig(connection_url="mysql+aiomysql://user:pass@host/db")
    conn_mgr = ConnectionManager()
    loop = DecisionLoop(config, llm, conn_mgr)
    result = await loop.run("插入一个测试用户", config.connection_url)
"""

from testagent.db_ops.cleanup import CleanupManager
from testagent.db_ops.confirm_ui import ConfirmUI
from testagent.db_ops.connection import ConnectionManager
from testagent.db_ops.decision_loop import DecisionLoop
from testagent.db_ops.errors import (
    ConfirmationRejectedError,
    DbConnectionError,
    DbOpsError,
    ExecutionTimeoutError,
    ForbiddenOperationError,
    SchemaInspectionError,
    SQLGenerationError,
)
from testagent.db_ops.models import (
    CleanupPlan,
    DbConfig,
    DbOpsResult,
    ExecutionResult,
    SqlOperation,
    SqlOperationType,
)
from testagent.db_ops.schema import ColumnInfo, SchemaInspector, TableInfo
from testagent.db_ops.sql_generator import SQLGenerator
from testagent.db_ops.sql_executor import SQLExecutor

__all__ = [
    "CleanupManager",
    "CleanupPlan",
    "ColumnInfo",
    "ConfirmationRejectedError",
    "ConfirmUI",
    "ConnectionManager",
    "DbConfig",
    "DbConnectionError",
    "DbOpsError",
    "DbOpsResult",
    "DecisionLoop",
    "ExecutionResult",
    "ExecutionTimeoutError",
    "ForbiddenOperationError",
    "SchemaInspectionError",
    "SchemaInspector",
    "SqlOperation",
    "SqlOperationType",
    "SQLExecutor",
    "SQLGenerationError",
    "SQLGenerator",
    "TableInfo",
]
