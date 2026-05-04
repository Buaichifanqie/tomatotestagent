from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

from testagent.common import get_logger
from testagent.common.errors import TestAgentError

if TYPE_CHECKING:
    from testagent.gateway.session import SessionManager

_logger = get_logger(__name__)

_CLIENT_EVENTS = frozenset({"session.cancel"})


def _validate_client_event(event: str) -> bool:
    return event in _CLIENT_EVENTS


class SessionWebSocketManager:
    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager
        self._connections: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()
        self._logger = _logger

    async def handle_websocket(self, websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()

        try:
            await self._session_manager.get_session(session_id)
        except TestAgentError:
            await websocket.send_json(
                {
                    "event": "error",
                    "session_id": session_id,
                    "data": {"code": "SESSION_NOT_FOUND", "message": f"Session '{session_id}' not found"},
                }
            )
            await websocket.close(code=4004)
            return

        await self._add_connection(session_id, websocket)
        self._logger.info(
            "WebSocket connected",
            extra={"extra_data": {"session_id": session_id}},
        )

        subscriber_task = asyncio.create_task(self._forward_events(websocket, session_id))
        reader_task = asyncio.create_task(self._read_client_messages(websocket, session_id))

        try:
            done, _ = await asyncio.wait(
                [subscriber_task, reader_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, (asyncio.CancelledError, WebSocketDisconnect)):
                    self._logger.error(
                        "WebSocket task error",
                        extra={"extra_data": {"session_id": session_id, "error": str(exc)}},
                    )
        finally:
            subscriber_task.cancel()
            reader_task.cancel()
            with_timeout = asyncio.wait(
                [subscriber_task, reader_task],
                timeout=2,
            )
            for t in (subscriber_task, reader_task):
                t.cancel()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(with_timeout, timeout=2)
            await self._remove_connection(session_id, websocket)
            self._logger.info(
                "WebSocket disconnected",
                extra={"extra_data": {"session_id": session_id}},
            )

    async def _add_connection(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            if session_id not in self._connections:
                self._connections[session_id] = set()
            self._connections[session_id].add(websocket)

    async def _remove_connection(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            conns = self._connections.get(session_id)
            if conns:
                conns.discard(websocket)
                if not conns:
                    self._connections.pop(session_id, None)

    async def _forward_events(self, websocket: WebSocket, session_id: str) -> None:
        async for event in self._session_manager.subscribe(session_id):
            try:
                await websocket.send_json(event)
            except WebSocketDisconnect:
                break

    async def _read_client_messages(self, websocket: WebSocket, session_id: str) -> None:
        try:
            while True:
                raw = await websocket.receive_json()
                event = raw.get("event", "")

                if event == "session.cancel":
                    self._logger.info(
                        "Client requested session cancel",
                        extra={"extra_data": {"session_id": session_id}},
                    )
                    try:
                        await self._session_manager.cancel_session(session_id)
                    except TestAgentError as exc:
                        await websocket.send_json(
                            {
                                "event": "error",
                                "session_id": session_id,
                                "data": {"code": exc.code, "message": exc.message},
                            }
                        )
                else:
                    self._logger.warning(
                        "Unknown client event",
                        extra={"extra_data": {"session_id": session_id, "event": event}},
                    )
        except WebSocketDisconnect:
            pass
