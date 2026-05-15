from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from testagent.models.base import BaseModel, JSONType


class MCPConfig(BaseModel):
    __tablename__ = "mcp_configs"

    session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("test_sessions.id"), nullable=True)
    server_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    command: Mapped[str] = mapped_column(String(1024), nullable=False)
    args: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)
    env: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
