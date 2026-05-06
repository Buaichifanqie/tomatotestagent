from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from testagent.models.base import BaseModel, DateTimeTZ, JSONType

if TYPE_CHECKING:
    from testagent.models.result import TestResult
    from testagent.models.session import TestSession

PLAN_STATUSES = ("pending", "in_progress", "completed", "failed")

STRATEGY_TYPES = ("smoke", "regression", "exploratory", "incremental")

TASK_STATUSES = ("queued", "running", "passed", "failed", "flaky", "skipped", "retrying")

TASK_TYPES = ("api_test", "web_test", "app_test")

ISOLATION_LEVELS = ("docker", "microvm", "local")


class TestPlan(BaseModel):
    __tablename__ = "test_plans"
    __test__ = False

    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_sessions.id"), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_json: Mapped[dict[str, object]] = mapped_column(JSONType, nullable=False)
    skill_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    session: Mapped["TestSession"] = relationship(back_populates="plans")  # noqa: UP037
    tasks: Mapped[list["TestTask"]] = relationship(  # noqa: UP037
        back_populates="plan", cascade="all, delete-orphan"
    )


class TestTask(BaseModel):
    __tablename__ = "test_tasks"
    __test__ = False

    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_plans.id"), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    skill_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    task_config: Mapped[dict[str, object]] = mapped_column(JSONType, nullable=False)
    isolation_level: Mapped[str] = mapped_column(String(16), nullable=False, default="docker")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    depends_on: Mapped[str | None] = mapped_column(String(36), ForeignKey("test_tasks.id"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)

    plan: Mapped["TestPlan"] = relationship(back_populates="tasks")  # noqa: UP037
    result: Mapped["TestResult | None"] = relationship(  # noqa: UP037
        back_populates="task", uselist=False, cascade="all, delete-orphan"
    )
