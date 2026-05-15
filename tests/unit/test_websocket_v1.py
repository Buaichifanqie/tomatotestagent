from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

from testagent.gateway.event_bus import EventBus
from testagent.gateway.session import (
    SESSION_EVENTS,
    SessionManager,
)
from testagent.gateway.websocket import SessionWebSocketManager

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# =============================================================================
# V1.0 Event Types Tests
# =============================================================================


class TestV1EventTypes:
    def test_mvp_events_present(self) -> None:
        assert "session.started" in SESSION_EVENTS
        assert "plan.generated" in SESSION_EVENTS
        assert "task.started" in SESSION_EVENTS
        assert "task.progress" in SESSION_EVENTS
        assert "task.completed" in SESSION_EVENTS
        assert "task.self_healing" in SESSION_EVENTS
        assert "result.analyzed" in SESSION_EVENTS
        assert "defect.filed" in SESSION_EVENTS
        assert "session.completed" in SESSION_EVENTS
        assert "session.failed" in SESSION_EVENTS

    def test_v1_events_added(self) -> None:
        assert "task.snapshot_saved" in SESSION_EVENTS
        assert "task.resuming" in SESSION_EVENTS
        assert "resource.usage" in SESSION_EVENTS
        assert "quality.trend_update" in SESSION_EVENTS


# =============================================================================
# SessionManager V1.0 Enhancements Tests
# =============================================================================


class TestSessionManagerV1:
    @pytest.fixture()
    def mgr(self) -> SessionManager:
        return SessionManager()

    async def test_broadcast_event(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="broadcast-test")
        collected: list[dict[str, Any]] = []

        async def collector() -> None:
            async for event in mgr.subscribe(session["id"]):
                collected.append(event)

        collector_task = asyncio.create_task(collector())
        await asyncio.sleep(0.02)

        await mgr.broadcast_event(
            session["id"],
            "task.snapshot_saved",
            {"task_id": "t1", "snapshot_id": "snap-1", "progress_pct": 50},
        )

        await asyncio.sleep(0.05)
        collector_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await collector_task

        assert len(collected) >= 1
        found = [e for e in collected if e["event"] == "task.snapshot_saved"]
        assert len(found) == 1
        assert found[0]["data"]["task_id"] == "t1"
        assert found[0]["data"]["snapshot_id"] == "snap-1"
        assert found[0]["data"]["progress_pct"] == 50

    async def test_broadcast_v1_events(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="v1-events-test")
        collected: list[dict[str, Any]] = []

        async def collector() -> None:
            async for event in mgr.subscribe(session["id"]):
                collected.append(event)

        ct = asyncio.create_task(collector())
        await asyncio.sleep(0.02)

        await mgr.broadcast_event(
            session["id"],
            "task.resuming",
            {"task_id": "t1", "from_step": "step_3"},
        )
        await mgr.broadcast_event(
            session["id"],
            "resource.usage",
            {"cpu_pct": 45.2, "memory_mb": 1024, "disk_pct": 32.0, "running_tasks": 3},
        )
        await mgr.broadcast_event(
            session["id"],
            "quality.trend_update",
            {"session_id": session["id"], "pass_rate": 0.92, "defect_count": 2},
        )

        await asyncio.sleep(0.05)
        ct.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ct

        events_found = {e["event"] for e in collected}
        assert "task.resuming" in events_found
        assert "resource.usage" in events_found
        assert "quality.trend_update" in events_found

    async def test_unsubscribe(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="unsub-test")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with mgr._lock:
            if session["id"] not in mgr._subscribers:
                mgr._subscribers[session["id"]] = []
            mgr._subscribers[session["id"]].append(queue)

        async with mgr._lock:
            assert queue in mgr._subscribers[session["id"]]

        await mgr.unsubscribe(session["id"], queue)

        async with mgr._lock:
            assert queue not in mgr._subscribers.get(session["id"], [])

    async def test_heartbeat_active(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="hb-active")
        alive = await mgr.heartbeat(session["id"])
        assert alive is True

    async def test_heartbeat_completed(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="hb-completed")
        await mgr.transition(session["id"], "planning")
        await mgr.transition(session["id"], "executing")
        await mgr.transition(session["id"], "analyzing")
        await mgr.transition(session["id"], "completed")
        alive = await mgr.heartbeat(session["id"])
        assert alive is False

    async def test_heartbeat_not_found(self, mgr: SessionManager) -> None:
        alive = await mgr.heartbeat("nonexistent")
        assert alive is False

    async def test_get_active_sessions(self, mgr: SessionManager) -> None:
        s1 = await mgr.create_session(name="active-1")
        s2 = await mgr.create_session(name="active-2")
        await mgr.transition(s1["id"], "planning")
        await mgr.transition(s2["id"], "failed")

        active = await mgr.get_active_sessions()
        active_ids = {s["id"] for s in active}
        assert s1["id"] in active_ids
        assert s2["id"] not in active_ids


# =============================================================================
# SessionWebSocketManager V1.0 Enhancements Tests
# =============================================================================


class TestSessionWebSocketManagerV1:
    @pytest.fixture()
    def session_manager(self) -> SessionManager:
        return SessionManager()

    @pytest.fixture()
    def ws_manager(self, session_manager: SessionManager) -> SessionWebSocketManager:
        return SessionWebSocketManager(session_manager)

    async def test_broadcast_event(self, session_manager: SessionManager, ws_manager: SessionWebSocketManager) -> None:
        session = await session_manager.create_session(name="ws-broadcast")
        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

        await ws_manager.handle_websocket(mock_ws, session["id"])
        mock_ws.accept.assert_awaited_once()

        await ws_manager.broadcast_event(
            session["id"],
            "task.snapshot_saved",
            {"task_id": "t1", "snapshot_id": "s1", "progress_pct": 75},
        )

    async def test_connection_count(self, session_manager: SessionManager, ws_manager: SessionWebSocketManager) -> None:
        session = await session_manager.create_session(name="ws-count")
        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

        await ws_manager.handle_websocket(mock_ws, session["id"])
        count = await ws_manager.connection_count(session["id"])
        assert count == 0  # Connection removed after disconnect

    async def test_active_sessions(self, session_manager: SessionManager, ws_manager: SessionWebSocketManager) -> None:
        session = await session_manager.create_session(name="ws-active")
        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

        await ws_manager.handle_websocket(mock_ws, session["id"])
        sessions = await ws_manager.active_sessions()
        assert isinstance(sessions, list)

    async def test_heartbeat_event_sent(
        self, session_manager: SessionManager, ws_manager: SessionWebSocketManager
    ) -> None:
        session = await session_manager.create_session(name="ws-heartbeat")
        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(
            side_effect=[
                WebSocketDisconnect(),
            ]
        )

        await ws_manager.handle_websocket(mock_ws, session["id"])
        mock_ws.accept.assert_awaited_once()

    async def test_pong_event_handled(
        self, session_manager: SessionManager, ws_manager: SessionWebSocketManager
    ) -> None:
        session = await session_manager.create_session(name="ws-pong")
        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(
            side_effect=[
                {"event": "pong"},
                WebSocketDisconnect(),
            ]
        )

        await ws_manager.handle_websocket(mock_ws, session["id"])
        mock_ws.accept.assert_awaited_once()


# =============================================================================
# EventBus Unit Tests
# =============================================================================


class TestEventBus:
    @pytest.fixture()
    def mock_redis(self) -> MagicMock:
        redis = MagicMock()
        redis.publish = AsyncMock(return_value=1)
        return redis

    async def test_publish_with_redis(self, mock_redis: MagicMock) -> None:
        bus = EventBus(redis_client=mock_redis)
        await bus.publish(
            "session-123",
            {
                "event": "task.snapshot_saved",
                "session_id": "session-123",
                "data": {"task_id": "t1"},
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        mock_redis.publish.assert_awaited_once()
        call_args = mock_redis.publish.call_args[0]
        assert call_args[0] == "testagent:events:session-123"
        payload = json.loads(call_args[1])
        assert payload["event"] == "task.snapshot_saved"

    async def test_publish_without_redis(self) -> None:
        bus = EventBus(redis_client=None)
        await bus.publish("session-123", {"event": "test"})

    async def test_publish_redis_error(self, mock_redis: MagicMock) -> None:
        mock_redis.publish = AsyncMock(side_effect=ConnectionError("Redis down"))
        bus = EventBus(redis_client=mock_redis)
        await bus.publish("session-123", {"event": "test"})

    async def test_subscribe_without_redis(self) -> None:
        bus = EventBus(redis_client=None)
        events: list[dict[str, Any]] = []
        async for event in bus.subscribe("session-123"):
            events.append(event)
        assert len(events) == 0

    async def test_subscribe_message_dispatching(self) -> None:
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()

        listen_calls: list[dict[str, Any]] = []

        async def mock_listen() -> AsyncIterator[dict[str, Any]]:
            listen_calls.append({"action": "start"})
            yield {"type": "subscribe", "data": 1}
            yield {
                "type": "message",
                "data": json.dumps(
                    {
                        "event": "task.snapshot_saved",
                        "session_id": "s1",
                        "data": {"progress_pct": 50},
                    }
                ).encode("utf-8"),
            }
            yield {
                "type": "message",
                "data": json.dumps(
                    {
                        "event": "resource.usage",
                        "session_id": "s1",
                        "data": {"cpu_pct": 45.0, "memory_mb": 1024},
                    }
                ).encode("utf-8"),
            }
            listen_calls.append({"action": "end"})

        mock_pubsub.listen = mock_listen

        mock_redis = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub

        bus = EventBus(redis_client=mock_redis)
        collected: list[dict[str, Any]] = []
        count = 0
        async for event in bus.subscribe("s1"):
            collected.append(event)
            count += 1
            if count >= 2:
                break

        assert len(collected) == 2
        assert collected[0]["event"] == "task.snapshot_saved"
        assert collected[1]["event"] == "resource.usage"

    async def test_subscribe_bad_json(self) -> None:
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        async def mock_listen() -> AsyncIterator[dict[str, Any]]:
            yield {"type": "subscribe", "data": 1}
            yield {"type": "message", "data": b"not-valid-json"}
            yield {
                "type": "message",
                "data": json.dumps({"event": "ok_event"}).encode("utf-8"),
            }

        mock_pubsub.listen = mock_listen

        mock_redis = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub

        bus = EventBus(redis_client=mock_redis)
        collected: list[dict[str, Any]] = []
        async for event in bus.subscribe("s1"):
            collected.append(event)
            break

        assert len(collected) == 1
        assert collected[0]["event"] == "ok_event"
