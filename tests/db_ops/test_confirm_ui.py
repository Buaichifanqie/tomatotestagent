"""Tests for testagent.db_ops.confirm_ui — ConfirmUI."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from testagent.db_ops.confirm_ui import ConfirmUI
from testagent.db_ops.models import SqlOperation, SqlOperationType


def _make_operation(
    op_type: SqlOperationType = SqlOperationType.SELECT,
    sql: str = "SELECT 1",
    params: dict | None = None,
    description: str = "",
) -> SqlOperation:
    return SqlOperation(type=op_type, sql=sql, params=params or {}, description=description)


class TestConfirmOperation:
    @patch("testagent.db_ops.confirm_ui.input", return_value="y")
    @patch("testagent.db_ops.confirm_ui.console")
    def test_confirm_y_returns_true(self, mock_console, mock_input):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.INSERT, "INSERT INTO t VALUES (1)")
        assert ui.confirm_operation(op) is True

    @patch("testagent.db_ops.confirm_ui.input", return_value="yes")
    @patch("testagent.db_ops.confirm_ui.console")
    def test_confirm_yes_returns_true(self, mock_console, mock_input):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.INSERT, "INSERT INTO t VALUES (1)")
        assert ui.confirm_operation(op) is True

    @patch("testagent.db_ops.confirm_ui.input", return_value="n")
    @patch("testagent.db_ops.confirm_ui.console")
    def test_confirm_n_returns_false(self, mock_console, mock_input):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.INSERT, "INSERT INTO t VALUES (1)")
        assert ui.confirm_operation(op) is False

    @patch("testagent.db_ops.confirm_ui.input", return_value="")
    @patch("testagent.db_ops.confirm_ui.console")
    def test_confirm_empty_returns_false(self, mock_console, mock_input):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.INSERT, "INSERT INTO t VALUES (1)")
        assert ui.confirm_operation(op) is False

    @patch("testagent.db_ops.confirm_ui.input", return_value="Y")
    @patch("testagent.db_ops.confirm_ui.console")
    def test_confirm_uppercase_y_returns_true(self, mock_console, mock_input):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.UPDATE, "UPDATE t SET x=1")
        assert ui.confirm_operation(op) is True

    @patch("testagent.db_ops.confirm_ui.input", return_value="y")
    @patch("testagent.db_ops.confirm_ui.console")
    def test_confirm_renders_sql(self, mock_console, mock_input):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.INSERT, "INSERT INTO users (name) VALUES (:name)")
        ui.confirm_operation(op)

        # Verify console.print was called (rendering happened)
        assert mock_console.print.called

    @patch("testagent.db_ops.confirm_ui.input", return_value="y")
    @patch("testagent.db_ops.confirm_ui.console")
    def test_confirm_with_params_renders_params(self, mock_console, mock_input):
        ui = ConfirmUI()
        op = _make_operation(
            SqlOperationType.INSERT,
            "INSERT INTO t (name) VALUES (:name)",
            params={"name": "test"},
            description="Insert test row",
        )
        ui.confirm_operation(op)
        assert mock_console.print.called


class TestShowResult:
    @patch("testagent.db_ops.confirm_ui.console")
    def test_success_result(self, mock_console):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.INSERT)
        ui.show_result(
            success=True,
            operation=op,
            rows_affected=1,
            duration_ms=50,
        )
        assert mock_console.print.called

    @patch("testagent.db_ops.confirm_ui.console")
    def test_failure_result(self, mock_console):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.INSERT)
        ui.show_result(
            success=False,
            operation=op,
            error_message="table not found",
            duration_ms=10,
        )
        assert mock_console.print.called

    @patch("testagent.db_ops.confirm_ui.console")
    def test_select_with_data_renders_table(self, mock_console):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.SELECT)
        ui.show_result(
            success=True,
            operation=op,
            rows_affected=2,
            data=[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
            duration_ms=20,
        )
        assert mock_console.print.called

    @patch("testagent.db_ops.confirm_ui.console")
    def test_select_with_empty_data(self, mock_console):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.SELECT)
        ui.show_result(
            success=True,
            operation=op,
            rows_affected=0,
            data=[],
            duration_ms=5,
        )
        assert mock_console.print.called


class TestShowCleanupPlan:
    @patch("testagent.db_ops.confirm_ui.console")
    def test_empty_operations_does_nothing(self, mock_console):
        ui = ConfirmUI()
        ui.show_cleanup_plan([])
        # Should not print anything for empty list
        mock_console.print.assert_not_called()

    @patch("testagent.db_ops.confirm_ui.console")
    def test_shows_operations(self, mock_console):
        ui = ConfirmUI()
        ops = [
            _make_operation(SqlOperationType.SELECT, "DELETE FROM t WHERE id=1", description="cleanup row 1"),
            _make_operation(SqlOperationType.SELECT, "DELETE FROM t WHERE id=2", description="cleanup row 2"),
        ]
        ui.show_cleanup_plan(ops)
        assert mock_console.print.called


class TestTypeColors:
    def test_select_color(self):
        assert ConfirmUI._TYPE_COLORS["SELECT"] == "green"

    def test_insert_color(self):
        assert ConfirmUI._TYPE_COLORS["INSERT"] == "yellow"

    def test_update_color(self):
        assert ConfirmUI._TYPE_COLORS["UPDATE"] == "red"


class TestRenderWarnings:
    @patch("testagent.db_ops.confirm_ui.console")
    def test_insert_warning(self, mock_console):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.INSERT, "INSERT INTO t VALUES (1)")
        ui._render_warnings(op)
        # Should print a warning about inserting
        mock_console.print.assert_called_once()
        call_str = str(mock_console.print.call_args)
        assert "插入" in call_str or "insert" in call_str.lower()

    @patch("testagent.db_ops.confirm_ui.console")
    def test_update_warning(self, mock_console):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.UPDATE, "UPDATE t SET x=1")
        ui._render_warnings(op)
        mock_console.print.assert_called_once()
        call_str = str(mock_console.print.call_args)
        assert "修改" in call_str or "警告" in call_str

    @patch("testagent.db_ops.confirm_ui.console")
    def test_select_no_warning(self, mock_console):
        ui = ConfirmUI()
        op = _make_operation(SqlOperationType.SELECT, "SELECT 1")
        ui._render_warnings(op)
        mock_console.print.assert_not_called()
