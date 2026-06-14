from __future__ import annotations

import re

from testagent.db_toolkit.errors import EnvironmentViolationError, SafetyViolationError
from testagent.db_toolkit.models import DbEnv, SqlOpType

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(DROP|ALTER|TRUNCATE)\b",
    re.IGNORECASE,
)
_MULTI_STATEMENT = re.compile(r";\s*\S")
_LINE_COMMENT = re.compile(r"--")
_BLOCK_COMMENT = re.compile(r"/\*")


class SafetyGuard:
    """Validates SQL operations against environment permissions and safety rules."""

    def check(self, env: DbEnv, op_type: SqlOpType, sql: str) -> None:
        self._check_safety(sql)
        self._check_env_permission(env, op_type)

    def _check_env_permission(self, env: DbEnv, op_type: SqlOpType) -> None:
        if op_type == SqlOpType.SELECT:
            return
        if not env.allow_write:
            raise EnvironmentViolationError(
                f"Write operation {op_type.value} not allowed in {env.level.value} environment",
                code="WRITE_NOT_ALLOWED",
                details={"op_type": op_type.value, "env": env.level.value},
            )
        if op_type == SqlOpType.DELETE and not env.allow_delete:
            raise EnvironmentViolationError(
                "DELETE not allowed in this environment",
                code="DELETE_NOT_ALLOWED",
                details={"env": env.level.value},
            )

    def _check_safety(self, sql: str) -> None:
        if _MULTI_STATEMENT.search(sql):
            raise SafetyViolationError(
                "multi-statement SQL is not allowed",
                code="MULTI_STATEMENT",
                details={"sql": sql[:200]},
            )
        if _FORBIDDEN_KEYWORDS.search(sql):
            raise SafetyViolationError(
                "SQL contains forbidden keyword (DROP/ALTER/TRUNCATE)",
                code="FORBIDDEN_KEYWORD",
                details={"sql": sql[:200]},
            )
        if _LINE_COMMENT.search(sql) or _BLOCK_COMMENT.search(sql):
            raise SafetyViolationError(
                "SQL contains comments",
                code="SQL_COMMENT",
                details={"sql": sql[:200]},
            )
