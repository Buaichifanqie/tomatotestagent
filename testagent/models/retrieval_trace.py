from __future__ import annotations

from sqlalchemy import Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from testagent.models.base import BaseModel, JSONType


class RetrievalTrace(BaseModel):
    __tablename__ = "retrieval_traces"

    app_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    query_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    retrieved_items: Mapped[list[dict] | None] = mapped_column(JSONType, nullable=True)
    generated_case_ids: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    adoption_score: Mapped[float | None] = mapped_column(Float, nullable=True)
