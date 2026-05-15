from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from testagent.models.base import BaseModel, JSONType

if TYPE_CHECKING:
    from testagent.models.result import TestResult

DEFECT_SEVERITIES = ("critical", "major", "minor", "trivial")

DEFECT_CATEGORIES = ("bug", "flaky", "environment", "configuration")

DEFECT_STATUSES = ("open", "confirmed", "resolved", "closed")


class Defect(BaseModel):
    __tablename__ = "defects"

    result_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_results.id"), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reproduction_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    jira_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    root_cause: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)
    original_defect_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    occurrence_count: Mapped[int] = mapped_column(default=1, nullable=False)

    result: Mapped["TestResult"] = relationship()  # noqa: UP037
