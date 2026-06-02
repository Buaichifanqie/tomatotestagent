from __future__ import annotations

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from testagent.models.base import BaseModel


class TestCaseRecord(BaseModel):
    __tablename__ = "test_case_records"

    app_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    app_version: Mapped[str] = mapped_column(String(64), nullable=False)
    case_content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # "generated", "manual", "imported"
    original_case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    tags: Mapped[str] = mapped_column(String(512), nullable=False, default="")  # comma-separated
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="app_local")
    last_validated_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
