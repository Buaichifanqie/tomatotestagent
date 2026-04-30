"""create all initial tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "test_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("trigger_type", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("input_context", sa.JSON(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "test_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("test_sessions.id"), nullable=False),
        sa.Column("strategy_type", sa.String(32), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("skill_ref", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("total_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_tasks", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "skill_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("trigger_pattern", sa.String(512), nullable=True),
        sa.Column("required_mcp_servers", sa.JSON(), nullable=True),
        sa.Column("required_rag_collections", sa.JSON(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("name", "version", name="uq_skill_name_version"),
    )

    op.create_table(
        "mcp_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("test_sessions.id"), nullable=True),
        sa.Column("server_name", sa.String(255), nullable=False, unique=True),
        sa.Column("command", sa.String(1024), nullable=False),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column("env", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
    )

    op.create_table(
        "test_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("test_plans.id"), nullable=False),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("skill_ref", sa.String(255), nullable=True),
        sa.Column("task_config", sa.JSON(), nullable=False),
        sa.Column("isolation_level", sa.String(16), nullable=False, server_default="docker"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("depends_on", sa.String(36), sa.ForeignKey("test_tasks.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "test_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("test_tasks.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("assertion_results", sa.JSON(), nullable=True),
        sa.Column("logs", sa.Text(), nullable=True),
        sa.Column("screenshot_url", sa.String(1024), nullable=True),
        sa.Column("video_url", sa.String(1024), nullable=True),
        sa.Column("artifacts", sa.JSON(), nullable=True),
    )

    op.create_table(
        "defects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("result_id", sa.String(36), sa.ForeignKey("test_results.id"), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reproduction_steps", sa.Text(), nullable=True),
        sa.Column("jira_key", sa.String(64), nullable=True, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("root_cause", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("defects")
    op.drop_table("test_results")
    op.drop_table("test_tasks")
    op.drop_table("mcp_configs")
    op.drop_table("skill_definitions")
    op.drop_table("test_plans")
    op.drop_table("test_sessions")
