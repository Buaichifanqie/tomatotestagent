"""upgrade schema for PostgreSQL V1.0: JSONB, TIMESTAMPTZ, GIN indexes

Migration steps:
  1. Enable pg_trgm extension for fuzzy text search on GIN indexes
  2. Migrate DateTime columns → TIMESTAMPTZ (timezone-aware timestamps)
  3. Migrate JSON/Text columns → JSONB (binary JSON with indexing support)
  4. Create GIN indexes:
     - defects.root_cause (JSONB path ops) — defect multi-dimensional search
     - defects.description (pg_trgm ops) — fuzzy text search
     - test_results.assertion_results (JSONB path ops) — assertion query

Note: This migration is idempotent for PostgreSQL.  It is a no-op for SQLite
since SQLAlchemy's dialect handling ignores postgresql-specific DDL.

Revision ID: 0002_upgrade_to_postgresql
Revises: 0001_initial
Create Date: 2026-05-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_upgrade_to_postgresql"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    _migrate_datetime_to_timestamptz()
    _migrate_json_to_jsonb()
    _create_gin_indexes()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    _drop_gin_indexes()
    _revert_jsonb_to_json()
    _revert_timestamptz_to_datetime()

    op.execute("DROP EXTENSION IF EXISTS pg_trgm")


# ---------------------------------------------------------------------------
# Upgrade helpers
# ---------------------------------------------------------------------------


def _migrate_datetime_to_timestamptz() -> None:
    """Migrate DateTime columns to TIMESTAMPTZ on all tables."""
    datetime_columns: list[tuple[str, str]] = [
        ("test_sessions", "created_at"),
        ("test_sessions", "completed_at"),
        ("test_plans", "created_at"),
        ("test_tasks", "created_at"),
        ("test_tasks", "started_at"),
        ("test_tasks", "completed_at"),
        ("test_results", "created_at"),
        ("defects", "created_at"),
        ("mcp_configs", "created_at"),
        ("skill_definitions", "created_at"),
        ("skill_definitions", "updated_at"),
    ]
    for table, column in datetime_columns:
        op.alter_column(
            table,
            column,
            type_=postgresql.TIMESTAMP(timezone=True),
            postgresql_using=f"{column}::timestamptz",
        )


def _migrate_json_to_jsonb() -> None:
    """Migrate JSON / TEXT columns to JSONB on all tables."""
    jsonb_columns: list[tuple[str, str]] = [
        ("test_sessions", "input_context"),
        ("test_plans", "plan_json"),
        ("test_tasks", "task_config"),
        ("test_results", "assertion_results"),
        ("test_results", "artifacts"),
        ("defects", "root_cause"),
        ("skill_definitions", "required_mcp_servers"),
        ("skill_definitions", "required_rag_collections"),
        ("skill_definitions", "tags"),
        ("mcp_configs", "args"),
        ("mcp_configs", "env"),
    ]
    for table, column in jsonb_columns:
        op.alter_column(
            table,
            column,
            type_=postgresql.JSONB,
            postgresql_using=f"{column}::jsonb",
        )


def _create_gin_indexes() -> None:
    """Create GIN indexes on JSONB and text columns for fast querying."""
    op.create_index(
        "ix_defects_root_cause_gin",
        "defects",
        [sa.text("root_cause jsonb_path_ops")],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_defects_description_gin",
        "defects",
        [sa.text("description gin_trgm_ops")],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_test_results_assertion_results_gin",
        "test_results",
        [sa.text("assertion_results jsonb_path_ops")],
        postgresql_using="gin",
    )


# ---------------------------------------------------------------------------
# Downgrade helpers
# ---------------------------------------------------------------------------


def _drop_gin_indexes() -> None:
    op.drop_index("ix_defects_root_cause_gin", table_name="defects")
    op.drop_index("ix_defects_description_gin", table_name="defects")
    op.drop_index("ix_test_results_assertion_results_gin", table_name="test_results")


def _revert_jsonb_to_json() -> None:
    jsonb_columns: list[tuple[str, str]] = [
        ("test_sessions", "input_context"),
        ("test_plans", "plan_json"),
        ("test_tasks", "task_config"),
        ("test_results", "assertion_results"),
        ("test_results", "artifacts"),
        ("defects", "root_cause"),
        ("skill_definitions", "required_mcp_servers"),
        ("skill_definitions", "required_rag_collections"),
        ("skill_definitions", "tags"),
        ("mcp_configs", "args"),
        ("mcp_configs", "env"),
    ]
    for table, column in jsonb_columns:
        op.alter_column(
            table,
            column,
            type_=sa.JSON(),
            postgresql_using=f"{column}::json",
        )


def _revert_timestamptz_to_datetime() -> None:
    datetime_columns: list[tuple[str, str]] = [
        ("test_sessions", "created_at"),
        ("test_sessions", "completed_at"),
        ("test_plans", "created_at"),
        ("test_tasks", "created_at"),
        ("test_tasks", "started_at"),
        ("test_tasks", "completed_at"),
        ("test_results", "created_at"),
        ("defects", "created_at"),
        ("mcp_configs", "created_at"),
        ("skill_definitions", "created_at"),
        ("skill_definitions", "updated_at"),
    ]
    for table, column in datetime_columns:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(),
            postgresql_using=f"{column}::timestamp",
        )
