from __future__ import annotations

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from testagent.models.base import BaseModel


class LearnedPattern(BaseModel):
    __tablename__ = "learned_patterns"

    app_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    pattern_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="app_local")
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
