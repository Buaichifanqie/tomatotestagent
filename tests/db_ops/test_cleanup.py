"""Tests for testagent.db_ops.cleanup — CleanupManager."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.db_ops.cleanup import CleanupManager
from testagent.db_ops.models import (
    CleanupPlan,
    ExecutionResult,
    SqlOperation,
    SqlOperationType,
)


def _make_prompt_patch():
    """Patch _load_prompt so tests don't need the actual prompt files."""
    return patch(
        "testagent.db_ops.cleanup._load_prompt",
        return_value="Operations: {executed_operations}\nSchema: {schema_context}",
    )


def _exec_result(
    success: bool = True,
    op_type: SqlOperationType = SqlOperationType.INSERT,
    sql: str = "INSERT INTO users (name) VALUES (:name)",
    params: dict | None = None,
    rows_affected: int = 1,
    data: list | None = None,
) -> ExecutionResult:
    op = SqlOperation(type=op_type, sql=sql, params=params or {})
    return ExecutionResult(
        success=success,
        operation=op,
        rows_affected=rows_affected,
        data=data or [],
    )


class TestGenerateCleanupPlan:
    @pytest.mark.asyncio
    async def test_no_write_ops_returns_empty_plan(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        # Only SELECT results — no cleanup needed
        results = [
            _exec_result(op_type=SqlOperationType.SELECT, sql="SELECT 1", rows_affected=1),
        ]
        plan = await mgr.generate_cleanup_plan(results, "schema")
        assert plan.description == "无需清理"
        assert plan.operations == []

    @pytest.mark.asyncio
    async def test_empty_results_returns_empty_plan(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        plan = await mgr.generate_cleanup_plan([], "schema")
        assert plan.description == "无需清理"

    @pytest.mark.asyncio
    async def test_failed_write_ops_excluded(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        results = [
            _exec_result(success=False, op_type=SqlOperationType.INSERT, sql="INSERT INTO t VALUES (1)"),
        ]
        plan = await mgr.generate_cleanup_plan(results, "schema")
        assert plan.description == "无需清理"

    @pytest.mark.asyncio
    async def test_successful_insert_calls_llm(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        cleanup_json = json.dumps({
            "operations": [
                {"sql": "DELETE FROM users WHERE id = 1", "description": "remove test user"}
            ],
            "description": "cleanup insert",
        })
        response = MagicMock()
        response.content = [{"text": cleanup_json}]
        llm.chat.return_value = response

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        results = [_exec_result(success=True, op_type=SqlOperationType.INSERT)]
        plan = await mgr.generate_cleanup_plan(results, "schema")

        llm.chat.assert_awaited_once()
        assert len(plan.operations) == 1
        assert plan.operations[0].is_cleanup is True
        assert "DELETE FROM users" in plan.operations[0].sql

    @pytest.mark.asyncio
    async def test_llm_failure_uses_fallback(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()
        llm.chat.side_effect = RuntimeError("API error")

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        results = [
            _exec_result(
                success=True,
                op_type=SqlOperationType.INSERT,
                sql="INSERT INTO users (name) VALUES ('test')",
            ),
        ]
        plan = await mgr.generate_cleanup_plan(results, "schema")

        # Should fall back to is_test-based cleanup
        assert len(plan.operations) > 0
        assert "is_test" in plan.operations[0].sql.lower()

    @pytest.mark.asyncio
    async def test_llm_empty_response_returns_no_plan(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()
        response = MagicMock()
        response.content = []
        llm.chat.return_value = response

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        results = [_exec_result(success=True)]
        plan = await mgr.generate_cleanup_plan(results, "schema")
        assert "LLM 未返回清理计划" in plan.description

    @pytest.mark.asyncio
    async def test_update_op_included_in_cleanup(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        cleanup_json = json.dumps({
            "operations": [
                {"sql": "UPDATE users SET name = 'old' WHERE id = 1", "description": "restore"}
            ],
            "description": "restore original",
        })
        response = MagicMock()
        response.content = [{"text": cleanup_json}]
        llm.chat.return_value = response

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        results = [
            _exec_result(
                success=True,
                op_type=SqlOperationType.UPDATE,
                sql="UPDATE users SET name = 'new' WHERE id = 1",
            ),
        ]
        plan = await mgr.generate_cleanup_plan(results, "schema")
        assert len(plan.operations) == 1


class TestParseCleanupResponse:
    def test_parse_valid_json(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        text = json.dumps({
            "operations": [
                {"sql": "DELETE FROM t WHERE id=1", "params": {}, "description": "cleanup"}
            ],
            "description": "done",
        })
        plan = mgr._parse_cleanup_response(text)
        assert len(plan.operations) == 1
        assert plan.operations[0].is_cleanup is True
        assert plan.description == "done"

    def test_parse_with_markdown_fence(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        fenced = '```json\n{"operations": [{"sql": "DELETE FROM t"}], "description": "ok"}\n```'
        plan = mgr._parse_cleanup_response(fenced)
        assert len(plan.operations) == 1

    def test_parse_invalid_json_returns_failure_plan(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        plan = mgr._parse_cleanup_response("not json")
        assert "解析失败" in plan.description

    def test_parse_skips_empty_sql(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        text = json.dumps({
            "operations": [
                {"sql": "", "description": "empty"},
                {"sql": "DELETE FROM t WHERE id=1", "description": "valid"},
            ],
            "description": "mixed",
        })
        plan = mgr._parse_cleanup_response(text)
        assert len(plan.operations) == 1


class TestExecuteCleanup:
    @pytest.mark.asyncio
    async def test_executes_all_operations(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        op1 = SqlOperation(type=SqlOperationType.SELECT, sql="DELETE FROM t WHERE id=1", is_cleanup=True)
        op2 = SqlOperation(type=SqlOperationType.SELECT, sql="DELETE FROM t WHERE id=2", is_cleanup=True)
        plan = CleanupPlan(operations=[op1, op2], description="cleanup")

        result1 = ExecutionResult(success=True, operation=op1)
        result2 = ExecutionResult(success=True, operation=op2)
        executor.execute = AsyncMock(side_effect=[result1, result2])

        results = await mgr.execute_cleanup(plan, "mysql://host/db")
        assert len(results) == 2
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_continues_on_failure(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        op1 = SqlOperation(type=SqlOperationType.SELECT, sql="DELETE FROM t WHERE id=1", is_cleanup=True)
        op2 = SqlOperation(type=SqlOperationType.SELECT, sql="DELETE FROM t WHERE id=2", is_cleanup=True)
        plan = CleanupPlan(operations=[op1, op2], description="cleanup")

        result1 = ExecutionResult(success=False, operation=op1, error_message="fail")
        result2 = ExecutionResult(success=True, operation=op2)
        executor.execute = AsyncMock(side_effect=[result1, result2])

        results = await mgr.execute_cleanup(plan, "mysql://host/db")
        assert len(results) == 2
        assert results[0].success is False
        assert results[1].success is True

    @pytest.mark.asyncio
    async def test_empty_plan_returns_empty(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        plan = CleanupPlan(operations=[], description="nothing")
        results = await mgr.execute_cleanup(plan, "mysql://host/db")
        assert results == []


class TestFallbackCleanup:
    def test_fallback_extracts_table_names(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        write_ops = [
            _exec_result(sql="INSERT INTO users (name) VALUES ('test')"),
            _exec_result(sql="INSERT INTO orders (total) VALUES (100)"),
        ]
        plan = mgr._fallback_cleanup(write_ops)

        assert len(plan.operations) == 2
        sqls = [op.sql for op in plan.operations]
        assert any("users" in s for s in sqls)
        assert any("orders" in s for s in sqls)
        assert all("is_test" in op.sql for op in plan.operations)

    def test_fallback_deduplicates_tables(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        write_ops = [
            _exec_result(sql="INSERT INTO users (name) VALUES ('a')"),
            _exec_result(sql="INSERT INTO users (name) VALUES ('b')"),
        ]
        plan = mgr._fallback_cleanup(write_ops)
        assert len(plan.operations) == 1  # deduplicated

    def test_fallback_with_no_insert_pattern(self):
        conn_mgr = AsyncMock()
        executor = AsyncMock()
        llm = AsyncMock()

        with _make_prompt_patch():
            mgr = CleanupManager(conn_mgr, executor, llm)

        write_ops = [
            _exec_result(
                op_type=SqlOperationType.UPDATE,
                sql="UPDATE users SET name='new' WHERE id=1",
            ),
        ]
        plan = mgr._fallback_cleanup(write_ops)
        # UPDATE doesn't match INSERT INTO pattern, so no tables extracted
        assert len(plan.operations) == 0
