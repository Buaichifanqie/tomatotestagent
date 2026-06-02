from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from testagent.models.base import BaseModel, DateTimeTZ


class AppVersion(BaseModel):
    __tablename__ = "app_versions"

    app_id: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    current_version: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    updated_at: Mapped[datetime] = mapped_column(
        DateTimeTZ,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
