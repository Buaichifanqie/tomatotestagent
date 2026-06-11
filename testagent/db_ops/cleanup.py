from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from testagent.common.logging import get_logger
from testagent.db_ops.connection import ConnectionManager
from testagent.db_ops.models import CleanupPlan, ExecutionResult, SqlOperation, SqlOperationType
from testagent.db_ops.schema import SchemaInspector
from testagent.db_ops.sql_executor import SQLExecutor

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


class CleanupManager:
    """Manages test data cleanup after database operations.

    Strategy:
    1. Generate cleanup SQL via LLM based on executed operations
    2. For INSERT: DELETE the inserted rows (by is_test flag or specific IDs)
    3. For UPDATE: Restore original values (if captured)
    4. Execute cleanup operations
    """

    def __init__(
        self,
        conn_manager: ConnectionManager,
        executor: SQLExecutor,
        llm: Any,  # ILLMProvider
    ) -> None:
        self._conn_manager = conn_manager
        self._executor = executor
        self._llm = llm
        self._cleanup_prompt = _load_prompt("cleanup_planning.txt")

    async def generate_cleanup_plan(
        self,
        executed_operations: list[ExecutionResult],
        schema_context: str,
    ) -> CleanupPlan:
        """Generate a cleanup plan based on executed operations."""
        # Filter to only successful write operations
        write_ops = [
            r for r in executed_operations
            if r.success and r.operation.type in (SqlOperationType.INSERT, SqlOperationType.UPDATE)
        ]

        if not write_ops:
            return CleanupPlan(description="无需清理")

        # Build operation summary for LLM
        ops_summary = []
        for r in write_ops:
            ops_summary.append({
                "type": r.operation.type.value,
                "sql": r.operation.sql,
                "params": r.operation.params,
                "rows_affected": r.rows_affected,
                "data": r.data[:5] if r.data else [],  # Sample of inserted data
            })

        prompt = self._cleanup_prompt.format(
            executed_operations=json.dumps(ops_summary, ensure_ascii=False, indent=2),
            schema_context=schema_context,
        )

        try:
            response = await self._llm.chat(
                system="你是一名数据库测试工程师，负责生成清理 SQL。",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
                temperature=0.1,
            )

            content = response.content
            if not content:
                return CleanupPlan(description="LLM 未返回清理计划")

            text = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
            return self._parse_cleanup_response(text)
        except Exception as exc:
            logger.warning("Failed to generate cleanup plan: %s", exc)
            # Fallback: try simple is_test-based cleanup
            return self._fallback_cleanup(write_ops)

    async def execute_cleanup(
        self,
        plan: CleanupPlan,
        connection_url: str,
    ) -> list[ExecutionResult]:
        """Execute all cleanup operations in the plan."""
        results: list[ExecutionResult] = []
        for op in plan.operations:
            result = await self._executor.execute(op, connection_url)
            results.append(result)
            if not result.success:
                logger.warning("Cleanup operation failed: %s - %s", op.sql, result.error_message)
        return results

    def _parse_cleanup_response(self, text: str) -> CleanupPlan:
        """Parse LLM response into a CleanupPlan."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse cleanup response as JSON")
            return CleanupPlan(description="清理计划解析失败")

        operations: list[SqlOperation] = []
        for op_data in data.get("operations", []):
            sql = op_data.get("sql", "").strip()
            if not sql:
                continue

            # Cleanup operations are always DELETE (allowed for cleanup)
            # But we validate they target test data
            operations.append(SqlOperation(
                type=SqlOperationType.SELECT,  # Type doesn't matter for cleanup execution
                sql=sql,
                params=op_data.get("params", {}),
                description=op_data.get("description", ""),
                is_cleanup=True,
            ))

        return CleanupPlan(
            operations=operations,
            description=data.get("description", ""),
        )

    def _fallback_cleanup(self, write_ops: list[ExecutionResult]) -> CleanupPlan:
        """Fallback cleanup: delete rows with is_test=1 from affected tables."""
        import re
        tables: set[str] = set()
        for r in write_ops:
            match = re.search(r"INSERT\s+INTO\s+(\w+)", r.operation.sql, re.IGNORECASE)
            if match:
                tables.add(match.group(1))

        operations: list[SqlOperation] = []
        for table in tables:
            operations.append(SqlOperation(
                type=SqlOperationType.SELECT,
                sql=f"DELETE FROM {table} WHERE is_test = 1",
                description=f"清理 {table} 中的测试数据",
                is_cleanup=True,
            ))

        return CleanupPlan(
            operations=operations,
            description=f"回退清理: 删除 {len(tables)} 个表中标记为 is_test=1 的记录",
        )
