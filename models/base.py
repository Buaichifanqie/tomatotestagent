from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class JSONType(TypeDecorator[Any]):
    """Adapter: JSON for SQLite, JSONB for PostgreSQL.

    Uses SQLAlchemy's TypeDecorator to select the appropriate JSON column type
    based on the engine dialect at runtime.  SQLite gets the standard JSON type
    (backed by JSON1 extension), while PostgreSQL gets the native JSONB type
    that supports binary storage and GIN indexing.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB

            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class DateTimeTZ(TypeDecorator[Any]):
    """Adapter: DATETIME for SQLite, TIMESTAMPTZ for PostgreSQL.

    SQLite stores datetimes as ISO-8601 strings (no native timezone support),
    while PostgreSQL uses the native TIMESTAMPTZ type that preserves timezone
    information.  All application-level datetimes are stored in UTC.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import TIMESTAMP

            return dialect.type_descriptor(TIMESTAMP(timezone=True))
        return dialect.type_descriptor(DateTime())


class Base(DeclarativeBase):
    pass


class BaseModel(Base):
    __abstract__ = True

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTimeTZ, default=lambda: datetime.now(UTC))
