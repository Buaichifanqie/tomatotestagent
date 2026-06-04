"""add failed_case_replays table

Creates the failed_case_replays table for the Failed Case Replay feature.
Stores captured test case failures with full TestCase serialization,
prerequisite chains, and replay tracking fields.

Revision ID: 0004_add_failed_replay
Revises: 0003_add_defect_dedup_fields
Create Date: 2026-06-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_add_failed_replay"
down_revision: str | None = "0003_add_defect_dedup_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "failed_case_replays",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("app_id", sa.String(256), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("test_case_id", sa.String(128), nullable=False),
        sa.Column("test_case_name", sa.String(512), nullable=False),
        sa.Column("original_status", sa.String(32), nullable=False),
        sa.Column("original_error_message", sa.String(2048), nullable=True),
        sa.Column("original_failed_step", sa.Integer(), nullable=True),
        sa.Column("original_screenshot_path", sa.String(1024), nullable=True),
        sa.Column("original_report_path", sa.String(1024), nullable=True),
        sa.Column("test_case_data", sa.JSON(), nullable=False),
        sa.Column("prerequisite_case_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("prerequisite_case_data", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("replay_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_replay_status", sa.String(32), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("last_replay_timestamp", sa.DateTime(), nullable=True),
        sa.Column("last_replay_error_message", sa.String(2048), nullable=True),
        sa.Column("last_replay_screenshot_path", sa.String(1024), nullable=True),
        sa.Column("last_replay_report_path", sa.String(1024), nullable=True),
        sa.Column("resolved", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("original_run_timestamp", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_failed_case_replays_app_id", "failed_case_replays", ["app_id"])
    op.create_index(
        "ix_failed_case_replays_app_resolved",
        "failed_case_replays",
        ["app_id", "resolved"],
    )
    op.create_index(
        "ix_failed_case_replays_app_tc",
        "failed_case_replays",
        ["app_id", "test_case_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_failed_case_replays_app_tc", table_name="failed_case_replays")
    op.drop_index("ix_failed_case_replays_app_resolved", table_name="failed_case_replays")
    op.drop_index("ix_failed_case_replays_app_id", table_name="failed_case_replays")
    op.drop_table("failed_case_replays")
