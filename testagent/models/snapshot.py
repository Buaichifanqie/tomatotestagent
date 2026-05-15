from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from testagent.models.base import BaseModel, DateTimeTZ, JSONType

SNAPSHOT_STATUSES = ("queued", "running", "retrying", "passed", "failed", "skipped", "completed")


class ExecutionSnapshotModel(BaseModel):
    __tablename__ = "execution_snapshots"
    __test__ = False

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_tasks.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_sessions.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    checkpoint: Mapped[dict[str, object]] = mapped_column(JSONType, nullable=False, default=dict)
    completed_steps: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    remaining_steps: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    intermediate_results: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)
    resource_state: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)
