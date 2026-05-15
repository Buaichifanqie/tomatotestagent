from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from testagent.models import (
    Base,
    DateTimeTZ,
    Defect,
    JSONType,
    MCPConfig,
    SkillDefinition,
    TestPlan,
    TestResult,
    TestSession,
    TestTask,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sqlite_engine() -> Engine:
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def pg_dialect() -> Any:
    """Return a PostgreSQL dialect instance (no DBAPI driver required)."""
    return postgresql.dialect()  # type: ignore[no-untyped-call]


@pytest.fixture()
def session(sqlite_engine: Engine) -> Any:
    with Session(sqlite_engine) as sess:
        yield sess


# ---------------------------------------------------------------------------
# Type adapter tests --- JSONType
# ---------------------------------------------------------------------------


class TestJSONTypeAdapter:
    def test_json_type_sqlite_renders_json(self) -> None:
        """JSONType with SQLite dialect should compile to JSON."""
        engine = create_engine("sqlite:///:memory:")
        col_type = JSONType()
        compiled = col_type.compile(dialect=engine.dialect)
        assert compiled == "JSON"

    def test_json_type_postgresql_renders_jsonb(self, pg_dialect: Any) -> None:
        """JSONType with PostgreSQL dialect should compile to JSONB."""
        col_type = JSONType()
        compiled = col_type.compile(dialect=pg_dialect)
        assert compiled == "JSONB"

    def test_json_type_sqlite_stores_and_retrieves(self, session: Session) -> None:
        """JSONType with SQLite can store and retrieve dict data."""
        s = TestSession(
            name="json-test",
            status="pending",
            trigger_type="manual",
            input_context={"key": "value", "nested": {"a": 1}},
        )
        session.add(s)
        session.flush()
        session.expire_all()
        loaded = session.get(TestSession, s.id)
        assert loaded is not None
        assert loaded.input_context == {"key": "value", "nested": {"a": 1}}

    def test_json_type_nullable(self, session: Session) -> None:
        """JSONType column can be NULL."""
        s = TestSession(name="null-json", status="pending", trigger_type="manual", input_context=None)
        session.add(s)
        session.flush()
        assert s.input_context is None

    def test_json_type_with_list(self, session: Session) -> None:
        """JSONType can store a list value."""
        sk = SkillDefinition(
            name="test-skill",
            version="1.0",
            description="test",
            tags=["web", "smoke", "mvp"],
        )
        session.add(sk)
        session.flush()
        session.expire_all()
        loaded = session.get(SkillDefinition, sk.id)
        assert loaded is not None
        assert loaded.tags == ["web", "smoke", "mvp"]  # type: ignore[comparison-overlap]


# ---------------------------------------------------------------------------
# Type adapter tests --- DateTimeTZ
# ---------------------------------------------------------------------------


class TestDateTimeTZAdapter:
    def test_datetimetz_sqlite_renders_datetime(self) -> None:
        """DateTimeTZ with SQLite dialect should compile to DATETIME."""
        engine = create_engine("sqlite:///:memory:")
        col_type = DateTimeTZ()
        compiled = col_type.compile(dialect=engine.dialect)
        assert compiled == "DATETIME"

    def test_datetimetz_postgresql_renders_timestamptz(self, pg_dialect: Any) -> None:
        """DateTimeTZ with PostgreSQL dialect should compile to TIMESTAMPTZ."""
        col_type = DateTimeTZ()
        compiled = col_type.compile(dialect=pg_dialect)
        assert compiled == "TIMESTAMP WITH TIME ZONE"

    def test_datetimetz_stores_utc_datetime(self, session: Session) -> None:
        """DateTimeTZ stores and retrieves UTC datetime correctly."""
        now = datetime.now(UTC)
        s = TestSession(
            name="dttz-test",
            status="pending",
            trigger_type="manual",
            completed_at=now,
        )
        session.add(s)
        session.flush()
        session.expire_all()
        loaded = session.get(TestSession, s.id)
        assert loaded is not None
        assert loaded.completed_at is not None
        assert abs((loaded.completed_at.replace(tzinfo=UTC) - now).total_seconds()) < 1

    def test_datetimetz_nullable(self, session: Session) -> None:
        """DateTimeTZ column can be NULL."""
        s = TestSession(name="null-dttz", status="pending", trigger_type="manual", completed_at=None)
        session.add(s)
        session.flush()
        assert s.completed_at is None

    def test_datetimetz_auto_set_on_create(self, session: Session) -> None:
        """created_at with DateTimeTZ is auto-set on insert."""
        before = datetime.now(UTC)
        s = TestSession(name="auto-dttz", status="pending", trigger_type="manual")
        session.add(s)
        session.flush()
        after = datetime.now(UTC)
        loaded_naive = s.created_at.replace(tzinfo=UTC)
        before_naive = before.replace(tzinfo=UTC)
        after_naive = after.replace(tzinfo=UTC)
        assert before_naive <= loaded_naive <= after_naive


# ---------------------------------------------------------------------------
# Model-level integration tests with V1 types (PostgreSQL dialect)
# ---------------------------------------------------------------------------


class TestPostgreSQLColumnTypes:
    """Verify column type reflection produces correct types for PostgreSQL dialect."""

    def test_input_context_is_jsonb_on_postgresql(self, pg_dialect: Any) -> None:
        """TestSession.input_context should be JSONB on PostgreSQL."""
        compiled = TestSession.__table__.c.input_context.type.compile(dialect=pg_dialect)
        assert compiled == "JSONB"

    def test_created_at_is_timestamptz_on_postgresql(self, pg_dialect: Any) -> None:
        """TestSession.created_at should be TIMESTAMPTZ on PostgreSQL."""
        compiled = TestSession.__table__.c.created_at.type.compile(dialect=pg_dialect)
        assert compiled == "TIMESTAMP WITH TIME ZONE"

    def test_plan_json_is_jsonb_on_postgresql(self, pg_dialect: Any) -> None:
        """TestPlan.plan_json should be JSONB on PostgreSQL."""
        compiled = TestPlan.__table__.c.plan_json.type.compile(dialect=pg_dialect)
        assert compiled == "JSONB"

    def test_task_config_is_jsonb_on_postgresql(self, pg_dialect: Any) -> None:
        """TestTask.task_config should be JSONB on PostgreSQL."""
        compiled = TestTask.__table__.c.task_config.type.compile(dialect=pg_dialect)
        assert compiled == "JSONB"

    def test_assertion_results_is_jsonb_on_postgresql(self, pg_dialect: Any) -> None:
        """TestResult.assertion_results should be JSONB on PostgreSQL."""
        compiled = TestResult.__table__.c.assertion_results.type.compile(dialect=pg_dialect)
        assert compiled == "JSONB"

    def test_artifacts_is_jsonb_on_postgresql(self, pg_dialect: Any) -> None:
        """TestResult.artifacts should be JSONB on PostgreSQL."""
        compiled = TestResult.__table__.c.artifacts.type.compile(dialect=pg_dialect)
        assert compiled == "JSONB"

    def test_root_cause_is_jsonb_on_postgresql(self, pg_dialect: Any) -> None:
        """Defect.root_cause should be JSONB on PostgreSQL."""
        compiled = Defect.__table__.c.root_cause.type.compile(dialect=pg_dialect)
        assert compiled == "JSONB"

    def test_mcp_config_args_is_jsonb_on_postgresql(self, pg_dialect: Any) -> None:
        """MCPConfig.args should be JSONB on PostgreSQL."""
        compiled = MCPConfig.__table__.c.args.type.compile(dialect=pg_dialect)
        assert compiled == "JSONB"

    def test_mcp_config_env_is_jsonb_on_postgresql(self, pg_dialect: Any) -> None:
        """MCPConfig.env should be JSONB on PostgreSQL."""
        compiled = MCPConfig.__table__.c.env.type.compile(dialect=pg_dialect)
        assert compiled == "JSONB"

    def test_skill_mcp_servers_is_jsonb_on_postgresql(self, pg_dialect: Any) -> None:
        """SkillDefinition.required_mcp_servers should be JSONB on PostgreSQL."""
        compiled = SkillDefinition.__table__.c.required_mcp_servers.type.compile(dialect=pg_dialect)
        assert compiled == "JSONB"

    def test_skill_tags_is_jsonb_on_postgresql(self, pg_dialect: Any) -> None:
        """SkillDefinition.tags should be JSONB on PostgreSQL."""
        compiled = SkillDefinition.__table__.c.tags.type.compile(dialect=pg_dialect)
        assert compiled == "JSONB"

    def test_skill_updated_at_is_timestamptz_on_postgresql(self, pg_dialect: Any) -> None:
        """SkillDefinition.updated_at should be TIMESTAMPTZ on PostgreSQL."""
        compiled = SkillDefinition.__table__.c.updated_at.type.compile(dialect=pg_dialect)
        assert compiled == "TIMESTAMP WITH TIME ZONE"

    def test_task_started_at_is_timestamptz_on_postgresql(self, pg_dialect: Any) -> None:
        """TestTask.started_at should be TIMESTAMPTZ on PostgreSQL."""
        compiled = TestTask.__table__.c.started_at.type.compile(dialect=pg_dialect)
        assert compiled == "TIMESTAMP WITH TIME ZONE"

    def test_task_completed_at_is_timestamptz_on_postgresql(self, pg_dialect: Any) -> None:
        """TestTask.completed_at should be TIMESTAMPTZ on PostgreSQL."""
        compiled = TestTask.__table__.c.completed_at.type.compile(dialect=pg_dialect)
        assert compiled == "TIMESTAMP WITH TIME ZONE"

    def test_session_completed_at_is_timestamptz_on_postgresql(self, pg_dialect: Any) -> None:
        """TestSession.completed_at should be TIMESTAMPTZ on PostgreSQL."""
        compiled = TestSession.__table__.c.completed_at.type.compile(dialect=pg_dialect)
        assert compiled == "TIMESTAMP WITH TIME ZONE"


# ---------------------------------------------------------------------------
# SQLite dialect preservation tests
# ---------------------------------------------------------------------------


class TestSQLiteColumnTypes:
    """Verify column types remain as JSON/DATETIME on SQLite."""

    def test_input_context_is_json_on_sqlite(self, sqlite_engine: Engine) -> None:
        """TestSession.input_context should be JSON on SQLite."""
        compiled = TestSession.__table__.c.input_context.type.compile(dialect=sqlite_engine.dialect)
        assert compiled == "JSON"

    def test_created_at_is_datetime_on_sqlite(self, sqlite_engine: Engine) -> None:
        """TestSession.created_at should be DATETIME on SQLite."""
        compiled = TestSession.__table__.c.created_at.type.compile(dialect=sqlite_engine.dialect)
        assert compiled == "DATETIME"

    def test_plan_json_is_json_on_sqlite(self, sqlite_engine: Engine) -> None:
        """TestPlan.plan_json should be JSON on SQLite."""
        compiled = TestPlan.__table__.c.plan_json.type.compile(dialect=sqlite_engine.dialect)
        assert compiled == "JSON"

    def test_task_config_is_json_on_sqlite(self, sqlite_engine: Engine) -> None:
        """TestTask.task_config should be JSON on SQLite."""
        compiled = TestTask.__table__.c.task_config.type.compile(dialect=sqlite_engine.dialect)
        assert compiled == "JSON"

    def test_assertion_results_is_json_on_sqlite(self, sqlite_engine: Engine) -> None:
        """TestResult.assertion_results should be JSON on SQLite."""
        compiled = TestResult.__table__.c.assertion_results.type.compile(dialect=sqlite_engine.dialect)
        assert compiled == "JSON"

    def test_root_cause_is_json_on_sqlite(self, sqlite_engine: Engine) -> None:
        """Defect.root_cause should be JSON on SQLite."""
        compiled = Defect.__table__.c.root_cause.type.compile(dialect=sqlite_engine.dialect)
        assert compiled == "JSON"

    def test_mcp_config_args_is_json_on_sqlite(self, sqlite_engine: Engine) -> None:
        """MCPConfig.args should be JSON on SQLite."""
        compiled = MCPConfig.__table__.c.args.type.compile(dialect=sqlite_engine.dialect)
        assert compiled == "JSON"

    def test_skill_tags_is_json_on_sqlite(self, sqlite_engine: Engine) -> None:
        """SkillDefinition.tags should be JSON on SQLite."""
        compiled = SkillDefinition.__table__.c.tags.type.compile(dialect=sqlite_engine.dialect)
        assert compiled == "JSON"


# ---------------------------------------------------------------------------
# GIN index definition tests
# ---------------------------------------------------------------------------


class TestGINIndexDefinitions:
    """Verify the migration script defines correct GIN indexes (no-op for SQLite)."""

    MIGRATION_FILE: ClassVar[str] = "testagent/db/alembic/versions/0002_upgrade_to_postgresql.py"

    @classmethod
    def _content(cls) -> str:
        return Path(cls.MIGRATION_FILE).read_text(encoding="utf-8")

    def test_migration_has_gin_index_on_defects_root_cause(self) -> None:
        """Migration should create GIN index on defects.root_cause."""
        content = self._content()
        assert "ix_defects_root_cause_gin" in content
        assert "root_cause jsonb_path_ops" in content

    def test_migration_has_gin_index_on_defects_description(self) -> None:
        """Migration should create GIN index on defects.description with pg_trgm."""
        content = self._content()
        assert "ix_defects_description_gin" in content
        assert "description gin_trgm_ops" in content

    def test_migration_has_gin_index_on_assertion_results(self) -> None:
        """Migration should create GIN index on test_results.assertion_results."""
        content = self._content()
        assert "ix_test_results_assertion_results_gin" in content
        assert "assertion_results jsonb_path_ops" in content

    def test_migration_enables_pg_trgm(self) -> None:
        """Migration should enable pg_trgm extension."""
        content = self._content()
        assert "pg_trgm" in content
        assert "CREATE EXTENSION IF NOT EXISTS" in content

    def test_migration_checks_dialect_before_running(self) -> None:
        """Migration should be no-op for non-PostgreSQL dialects."""
        content = self._content()
        assert 'bind.dialect.name != "postgresql"' in content

    def test_migration_revises_initial(self) -> None:
        """Migration should chain after 0001_initial."""
        content = self._content()
        assert 'down_revision: str | None = "0001_initial"' in content


# ---------------------------------------------------------------------------
# Full model integration with V1 types (SQLite runtime)
# ---------------------------------------------------------------------------


class TestV1ModelIntegration:
    """End-to-end model operations with V1 type adapters on SQLite."""

    def test_create_full_session_with_json_and_timestamptz(self, session: Session) -> None:
        now = datetime.now(UTC)
        s = TestSession(
            name="v1-integration",
            status="completed",
            trigger_type="ci_push",
            input_context={"url": "https://example.com", "env": "staging", "retry": True},
            completed_at=now,
        )
        session.add(s)
        session.flush()
        session.expire_all()
        loaded = session.get(TestSession, s.id)
        assert loaded is not None
        assert loaded.input_context == {"url": "https://example.com", "env": "staging", "retry": True}
        assert loaded.completed_at is not None
        assert abs((loaded.completed_at.replace(tzinfo=UTC) - now).total_seconds()) < 1

    def test_create_full_skill_with_all_json_fields(self, session: Session) -> None:
        now = datetime.now(UTC)
        sk = SkillDefinition(
            name="full-skill",
            version="2.0.0",
            description="Full skill with all JSON fields",
            required_mcp_servers=["playwright_server", "api_server"],
            required_rag_collections=["req_docs", "locator_library"],
            tags=["web", "v1", "full"],
            updated_at=now,
        )
        session.add(sk)
        session.flush()
        session.expire_all()
        loaded = session.get(SkillDefinition, sk.id)
        assert loaded is not None
        assert loaded.required_mcp_servers == ["playwright_server", "api_server"]  # type: ignore[comparison-overlap]
        assert loaded.required_rag_collections == ["req_docs", "locator_library"]  # type: ignore[comparison-overlap]
        assert loaded.tags == ["web", "v1", "full"]  # type: ignore[comparison-overlap]
        assert loaded.updated_at is not None
        assert abs((loaded.updated_at.replace(tzinfo=UTC) - now).total_seconds()) < 1

    def test_full_defect_with_root_cause_json(self, session: Session) -> None:
        s = TestSession(name="defect-v1", status="analyzing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="regression",
            plan_json={"steps": ["step1", "step2"]},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="web_test",
            task_config={"url": "/login", "method": "POST"},
        )
        session.add(t)
        session.flush()
        r = TestResult(
            task_id=t.id,
            status="failed",
            assertion_results={
                "total": 3,
                "passed": 0,
                "failed": 3,
                "details": {"status": 500, "body": "error"},
            },
            artifacts={"screenshot": "s3://bucket/screen.png", "har": "s3://bucket/trace.har"},
        )
        session.add(r)
        session.flush()
        d = Defect(
            result_id=r.id,
            severity="critical",
            category="bug",
            title="Login endpoint returns 500",
            root_cause={
                "component": "auth-service",
                "error_type": "NullPointerException",
                "stack_trace": "at com.example.AuthService.login(AuthService.java:42)",
            },
        )
        session.add(d)
        session.flush()
        session.expire_all()
        loaded_defect = session.get(Defect, d.id)
        assert loaded_defect is not None
        assert loaded_defect.root_cause == {
            "component": "auth-service",
            "error_type": "NullPointerException",
            "stack_trace": "at com.example.AuthService.login(AuthService.java:42)",
        }
        loaded_result = session.get(TestResult, r.id)
        assert loaded_result is not None
        assert loaded_result.assertion_results is not None
        assert loaded_result.assertion_results.get("total") == 3
        assert loaded_result.artifacts is not None
        assert loaded_result.artifacts.get("screenshot") == "s3://bucket/screen.png"


# ---------------------------------------------------------------------------
# Dialect safety tests
# ---------------------------------------------------------------------------


class TestDialectSafety:
    """Verify type adapters gracefully handle non-standard dialect names."""

    def test_json_type_defaults_to_json_on_unknown_dialect(self) -> None:
        """JSONType should fall back to JSON for unknown dialects."""
        col_type = JSONType()
        compiled = col_type.compile()
        assert compiled == "JSON"

    def test_datetimetz_defaults_to_datetime_on_unknown_dialect(self) -> None:
        """DateTimeTZ should fall back to DATETIME for unknown dialects."""
        col_type = DateTimeTZ()
        compiled = col_type.compile()
        assert compiled == "DATETIME"
