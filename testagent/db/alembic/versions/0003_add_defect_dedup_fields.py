"""add defect deduplication fields: original_defect_id, occurrence_count

Adds two columns to the defects table to support defect deduplication (F-D02):
  1. original_defect_id — nullable FK-like link to the original defect when
     a new defect is identified as a duplicate
  2. occurrence_count — integer counter tracking how many times this defect
     has been re-discovered, incremented on each duplicate detection

Revision ID: 0003_add_defect_dedup_fields
Revises: 0002_upgrade_to_postgresql
Create Date: 2026-05-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_add_defect_dedup_fields"
down_revision: str | None = "0002_upgrade_to_postgresql"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "defects",
        sa.Column("original_defect_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "defects",
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_index(
        "ix_defects_original_defect_id",
        "defects",
        ["original_defect_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_defects_original_defect_id", table_name="defects")
    op.drop_column("defects", "occurrence_count")
    op.drop_column("defects", "original_defect_id")
