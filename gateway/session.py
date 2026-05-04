from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from testagent.common import get_logger
from testagent.common.errors import TestAgentError

_logger = get_logger(__name__)

SESSION_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"planning", "failed"},
    "planning": {"executing", "failed"},
    "executing": {"analyzing", "failed"},
    "analyzing": {"completed", "failed"},
    "failed": set(),
    "completed": set(),
}

SESSION_EVENTS = frozenset(
    {
        "session.started",
        "plan.generated",
        "task.started",
        "task.progress",
        "task.completed",
        "task.self_healing",
        "result.analyzed",
        "defect.filed",
        "session.completed",
        "session.failed",
        "session.planning",
        "session.executing",
        "session.analyzing",
    }
)


class SessionStateError(TestAgentError):
    pass


class SessionNotFoundError(TestAgentError):
    pass


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()
        self._logger = _logger

    async def create_session(
        self,
        name: str,
        trigger_type: str = "manual",
        input_context: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        session: dict[str, Any] = {
            "id": session_id,
            "name": name,
            "status": "pending",
            "trigger_type": trigger_type,
            "input_context": input_context or {},
            "created_at": now,
            "completed_at": None,
        }
        async with self._lock:
            self._sessions[session_id] = session
        await self._broadcast(session_id, "session.started", session)
        self._logger.info("Session created", extra={"extra_data": {"session_id": session_id, "name": name}})
        return session

    async def get_session(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(
                message=f"Session '{session_id}' not found",
                code="SESSION_NOT_FOUND",
                details={"session_id": session_id},
            )
        return session

    async def list_sessions(self) -> list[dict[str, Any]]:
        async with self._lock:
            return list(self._sessions.values())

    async def transition(self, session_id: str, new_status: str) -> dict[str, Any]:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(
                    message=f"Session '{session_id}' not found",
                    code="SESSION_NOT_FOUND",
                    details={"session_id": session_id},
                )

            current = str(session["status"])
            allowed = SESSION_TRANSITIONS.get(current, set())
            if new_status not in allowed:
                raise SessionStateError(
                    message=f"Invalid state transition from '{current}' to '{new_status}'",
                    code="INVALID_STATE_TRANSITION",
                    details={
                        "session_id": session_id,
                        "current_status": current,
                        "requested_status": new_status,
                        "allowed_transitions": list(allowed),
                    },
                )

            session["status"] = new_status
            if new_status in ("completed", "failed"):
                session["completed_at"] = datetime.now(UTC).isoformat()

        event_name = f"session.{new_status}"
        if event_name in SESSION_EVENTS:
            await self._broadcast(session_id, event_name, session)

        self._logger.info(
            "Session transition",
            extra={
                "extra_data": {
                    "session_id": session_id,
                    "from_status": current,
                    "to_status": new_status,
                }
            },
        )
        return session

    async def cancel_session(self, session_id: str) -> dict[str, Any]:
        return await self.transition(session_id, "failed")

    async def subscribe(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is not None and session["status"] in ("completed", "failed"):
                yield {
                    "event": f"session.{session['status']}",
                    "session_id": session_id,
                    "data": session,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                return

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._lock:
            if session_id not in self._subscribers:
                self._subscribers[session_id] = []
            self._subscribers[session_id].append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
                if event.get("event") in ("session.completed", "session.failed"):
                    break
        finally:
            async with self._lock:
                subs = self._subscribers.get(session_id, [])
                if queue in subs:
                    subs.remove(queue)

    async def publish_event(
        self,
        session_id: str,
        event: str,
        data: dict[str, object] | None = None,
    ) -> None:
        if event not in SESSION_EVENTS:
            self._logger.warning(
                "Unknown session event",
                extra={"extra_data": {"session_id": session_id, "event": event}},
            )
        await self._broadcast(session_id, event, data or {})

    async def _broadcast(self, session_id: str, event: str, data: dict[str, Any]) -> None:
        async with self._lock:
            subs = list(self._subscribers.get(session_id, []))
        if not subs:
            return
        message: dict[str, Any] = {
            "event": event,
            "session_id": session_id,
            "data": data,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        for queue in subs:
            await queue.put(message)
