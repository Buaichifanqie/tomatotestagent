from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from testagent.models.base import BaseModel, DateTimeTZ, JSONType

REPLAY_STATUSES = ("PENDING", "RUNNING", "PASSED", "STILL_FAILED", "BLOCKED", "SKIPPED")


class FailedCaseReplay(BaseModel):
    __tablename__ = "failed_case_replays"
    __test__ = False

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("prerequisite_case_ids", [])
        kwargs.setdefault("prerequisite_case_data", [])
        kwargs.setdefault("replay_count", 0)
        kwargs.setdefault("last_replay_status", "PENDING")
        kwargs.setdefault("resolved", 0)
        super().__init__(**kwargs)

    # -- Identification --
    app_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    test_case_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # -- Failure snapshot --
    test_case_name: Mapped[str] = mapped_column(String(512), nullable=False)
    original_status: Mapped[str] = mapped_column(String(32), nullable=False)
    original_error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    original_failed_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_screenshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_report_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # -- Full TestCase serialization --
    test_case_data: Mapped[dict] = mapped_column(JSONType, nullable=False)

    # -- Prerequisite chain --
    prerequisite_case_ids: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    prerequisite_case_data: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)

    # -- Replay tracking --
    replay_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    last_replay_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", server_default=text("'PENDING'"))
    last_replay_timestamp: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)
    last_replay_error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    last_replay_screenshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    last_replay_report_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # -- Resolution --
    resolved: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)
    original_run_timestamp: Mapped[datetime] = mapped_column(DateTimeTZ, nullable=False)

    # -- Timestamps --
    updated_at: Mapped[datetime] = mapped_column(
        DateTimeTZ,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
