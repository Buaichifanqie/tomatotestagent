from __future__ import annotations

import pytest

from testagent.db_toolkit.errors import EnvironmentViolationError, SafetyViolationError
from testagent.db_toolkit.models import DbEnv, Environment, SqlOpType
from testagent.db_toolkit.safety import SafetyGuard


@pytest.fixture
def test_env():
    return DbEnv(
        level=Environment.TEST,
        connection_url="mysql://u:p@localhost/test_db",
        detected_by="url_pattern",
    )


@pytest.fixture
def prod_env():
    return DbEnv(
        level=Environment.PRODUCTION,
        connection_url="mysql://u:p@localhost/prod_db",
        detected_by="default",
    )


class TestSafetyGuard:
    def test_select_allowed_in_prod(self, prod_env):
        guard = SafetyGuard()
        guard.check(prod_env, SqlOpType.SELECT, "SELECT * FROM users")

    def test_insert_blocked_in_prod(self, prod_env):
        guard = SafetyGuard()
        with pytest.raises(EnvironmentViolationError):
            guard.check(prod_env, SqlOpType.INSERT, "INSERT INTO users (name) VALUES ('a')")

    def test_update_blocked_in_prod(self, prod_env):
        guard = SafetyGuard()
        with pytest.raises(EnvironmentViolationError):
            guard.check(prod_env, SqlOpType.UPDATE, "UPDATE users SET name='b'")

    def test_delete_blocked_in_prod(self, prod_env):
        guard = SafetyGuard()
        with pytest.raises(EnvironmentViolationError):
            guard.check(prod_env, SqlOpType.DELETE, "DELETE FROM users WHERE id=1")

    def test_all_allowed_in_test(self, test_env):
        guard = SafetyGuard()
        for op in SqlOpType:
            guard.check(test_env, op, f"{op.value} ...")

    def test_multi_statement_blocked(self, test_env):
        guard = SafetyGuard()
        with pytest.raises(SafetyViolationError, match="multi-statement"):
            guard.check(test_env, SqlOpType.SELECT, "SELECT 1; DROP TABLE users")

    def test_drop_blocked(self, test_env):
        guard = SafetyGuard()
        with pytest.raises(SafetyViolationError, match="forbidden keyword"):
            guard.check(test_env, SqlOpType.SELECT, "DROP TABLE users")

    def test_alter_blocked(self, test_env):
        guard = SafetyGuard()
        with pytest.raises(SafetyViolationError, match="forbidden keyword"):
            guard.check(test_env, SqlOpType.SELECT, "ALTER TABLE users ADD col int")

    def test_truncate_blocked(self, test_env):
        guard = SafetyGuard()
        with pytest.raises(SafetyViolationError, match="forbidden keyword"):
            guard.check(test_env, SqlOpType.SELECT, "TRUNCATE TABLE users")

    def test_line_comment_blocked(self, test_env):
        guard = SafetyGuard()
        with pytest.raises(SafetyViolationError, match="comment"):
            guard.check(test_env, SqlOpType.SELECT, "SELECT * -- hidden\nFROM users")

    def test_block_comment_blocked(self, test_env):
        guard = SafetyGuard()
        with pytest.raises(SafetyViolationError, match="comment"):
            guard.check(test_env, SqlOpType.SELECT, "SELECT * /* hidden */ FROM users")
