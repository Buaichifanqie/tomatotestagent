from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from testagent.models.base import BaseModel

if TYPE_CHECKING:
    from testagent.models.plan import TestPlan

SESSION_STATUSES = ("pending", "planning", "executing", "analyzing", "completed", "failed")

TRIGGER_TYPES = ("manual", "ci_push", "ci_pr", "scheduled")


class TestSession(BaseModel):
    __tablename__ = "test_sessions"
    __test__ = False

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    input_context: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    plans: Mapped[list["TestPlan"]] = relationship(  # noqa: UP037
        back_populates="session", cascade="all, delete-orphan"
    )
