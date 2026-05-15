from __future__ import annotations

SESSION_STATUSES: tuple[str, ...] = (
    "pending",
    "planning",
    "executing",
    "analyzing",
    "completed",
    "failed",
)

TASK_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "passed",
    "failed",
    "flaky",
    "skipped",
    "retrying",
)

DEFECT_CATEGORIES: tuple[str, ...] = (
    "bug",
    "flaky",
    "environment",
    "configuration",
)

DEFECT_SEVERITIES: tuple[str, ...] = (
    "critical",
    "major",
    "minor",
    "trivial",
)

ISOLATION_LEVELS: tuple[str, ...] = (
    "docker",
    "microvm",
    "local",
)

MESSAGE_TYPES: tuple[str, ...] = (
    "task_assignment",
    "result_report",
    "query",
    "notification",
    "ack",
    "error",
)

RRF_K: int = 60
