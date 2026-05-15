from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from testagent.models.base import BaseModel, JSONType

if TYPE_CHECKING:
    from testagent.models.plan import TestTask

RESULT_STATUSES = ("passed", "failed", "error", "flaky")


class TestResult(BaseModel):
    __tablename__ = "test_results"
    __test__ = False

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_tasks.id"), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    assertion_results: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)
    logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    video_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    artifacts: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)

    task: Mapped["TestTask"] = relationship(back_populates="result")  # noqa: UP037
