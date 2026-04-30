from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self

import aiosqlite
from pydantic import BaseModel, Field, field_validator, model_validator

from testagent.common.errors import AgentError
from testagent.common.logging import get_logger

logger = get_logger(__name__)

VALID_MESSAGE_TYPES: frozenset[str] = frozenset({
    "task_assignment",
    "result_report",
    "query",
    "notification",
    "ack",
    "error",
})

SENDER_RECEIVER_PATTERN: str = r"^(planner|executor_\d+|analyzer|gateway|cli|broadcast)$"

SESSION_STATE_MACHINE: dict[str, list[str]] = {
    "planning": ["task_assignment"],
    "executing": ["result_report"],
    "analyzing": ["notification", "query"],
    "completed": [],
    "failed": [],
}

ACKNOWLEDGMENT_TIMEOUT_SECONDS: int = 30
MAX_RETRY_COUNT: int = 3


class MessagePayload(BaseModel):
    type: str


class TaskAssignmentPayload(MessagePayload):
    type: Literal["task_assignment"] = "task_assignment"
    task_id: str
    task_type: str
    config: dict[str, Any] = Field(default_factory=dict)
    skill_ref: str | None = None


class ResultReportPayload(MessagePayload):
    type: Literal["result_report"] = "result_report"
    task_id: str
    status: str
    results: dict[str, Any] | None = None
    logs: str | None = None
    duration_ms: float | None = None


class QueryPayload(MessagePayload):
    type: Literal["query"] = "query"
    query_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class NotificationPayload(MessagePayload):
    type: Literal["notification"] = "notification"
    event: str
    data: dict[str, Any] | None = None


class AckPayload(MessagePayload):
    type: Literal["ack"] = "ack"
    acked_message_id: str
    status: str = "received"


class ErrorPayload(MessagePayload):
    type: Literal["error"] = "error"
    error_code: str
    message: str
    details: dict[str, Any] | None = None


PayloadType = Annotated[
    TaskAssignmentPayload
    | ResultReportPayload
    | QueryPayload
    | NotificationPayload
    | AckPayload
    | ErrorPayload,
    Field(discriminator="type"),
]


class AgentMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message_type: str
    sender: str = Field(pattern=SENDER_RECEIVER_PATTERN)
    receiver: str = Field(pattern=SENDER_RECEIVER_PATTERN)
    session_id: str
    payload: PayloadType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    in_reply_to: str | None = None

    @field_validator("message_type")
    @classmethod
    def _validate_message_type(cls, v: str) -> str:
        if v not in VALID_MESSAGE_TYPES:
            raise ValueError(
                f"Invalid message_type: {v!r}. "
                f"Must be one of {sorted(VALID_MESSAGE_TYPES)}"
            )
        return v

    @model_validator(mode="after")
    def _validate_payload_consistency(self) -> Self:
        if self.message_type != self.payload.type:
            raise ValueError(
                f"message_type {self.message_type!r} does not match "
                f"payload type {self.payload.type!r}"
            )
        if self.message_type == "ack" and self.in_reply_to is None and isinstance(self.payload, AckPayload):
            self.in_reply_to = self.payload.acked_message_id
        return self

    def is_valid_transition(self, current_state: str) -> bool:
        if self.message_type in ("ack", "error"):
            return True
        allowed = SESSION_STATE_MACHINE.get(current_state)
        if allowed is None:
            return False
        return self.message_type in allowed


class MessageStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def _ensure_connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_messages (
                    message_id TEXT PRIMARY KEY,
                    message_type TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    receiver TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    in_reply_to TEXT,
                    acked_at TEXT
                )
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_receiver_acked
                ON agent_messages(receiver, acked_at)
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON agent_messages(session_id)
            """)
            await self._conn.commit()
        return self._conn

    async def append(self, message: AgentMessage) -> None:
        conn = await self._ensure_connection()
        payload_json = message.payload.model_dump_json()
        ts = message.timestamp.isoformat()
        await conn.execute(
            """
            INSERT OR IGNORE INTO agent_messages
                (message_id, message_type, sender, receiver, session_id,
                 payload, timestamp, in_reply_to)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.message_id,
                message.message_type,
                message.sender,
                message.receiver,
                message.session_id,
                payload_json,
                ts,
                message.in_reply_to,
            ),
        )
        await conn.commit()

    async def get_unacked(
        self, receiver: str, timeout_seconds: int = ACKNOWLEDGMENT_TIMEOUT_SECONDS
    ) -> list[AgentMessage]:
        conn = await self._ensure_connection()
        cursor = await conn.execute(
            """
            SELECT * FROM agent_messages
            WHERE receiver = ?
              AND acked_at IS NULL
              AND datetime(timestamp) < datetime('now', ?)
            ORDER BY timestamp ASC
            """,
            (receiver, f"-{timeout_seconds} seconds"),
        )
        rows = await cursor.fetchall()
        return [self._row_to_message(row) for row in rows]

    async def ack(self, message_id: str) -> None:
        conn = await self._ensure_connection()
        now = datetime.now(UTC).isoformat()
        await conn.execute(
            "UPDATE agent_messages SET acked_at = ? WHERE message_id = ?",
            (now, message_id),
        )
        await conn.commit()

    async def is_duplicate(self, message_id: str) -> bool:
        conn = await self._ensure_connection()
        cursor = await conn.execute(
            "SELECT 1 FROM agent_messages WHERE message_id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _row_to_message(self, row: aiosqlite.Row) -> AgentMessage:
        payload_data = json.loads(row["payload"])
        payload_dict: dict[str, Any] = {"type": row["message_type"]}
        payload_dict.update(payload_data)
        payload = _deserialize_payload(payload_dict)
        return AgentMessage(
            message_id=row["message_id"],
            message_type=row["message_type"],
            sender=row["sender"],
            receiver=row["receiver"],
            session_id=row["session_id"],
            payload=payload,
            timestamp=datetime.fromisoformat(row["timestamp"]),
            in_reply_to=row["in_reply_to"],
        )


def _deserialize_payload(data: dict[str, Any]) -> PayloadType:
    type_ = data.get("type", "")
    if type_ == "task_assignment":
        return TaskAssignmentPayload(**data)
    if type_ == "result_report":
        return ResultReportPayload(**data)
    if type_ == "query":
        return QueryPayload(**data)
    if type_ == "notification":
        return NotificationPayload(**data)
    if type_ == "ack":
        return AckPayload(**data)
    if type_ == "error":
        return ErrorPayload(**data)
    raise AgentError(f"Unknown payload type: {type_!r}")
