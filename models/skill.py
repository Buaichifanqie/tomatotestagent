from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from testagent.models.base import BaseModel


class SkillDefinition(BaseModel):
    __tablename__ = "skill_definitions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_skill_name_version"),)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_pattern: Mapped[str | None] = mapped_column(String(512), nullable=True)
    required_mcp_servers: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    required_rag_collections: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
