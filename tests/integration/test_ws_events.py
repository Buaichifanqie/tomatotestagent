from __future__ import annotations

import asyncio
import os
import socket
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio

from testagent.gateway.event_bus import EventBus
from testagent.gateway.session import (
    SessionManager,
)

REDIS_HOST = os.environ.get("TESTAGENT_REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("TESTAGENT_REDIS_PORT", "6379"))


def _redis_is_available() -> bool:
    try:
        with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=2):
            return True
    except OSError:
        return False


requires_redis = pytest.mark.skipif(
    not _redis_is_available(),
    reason="Redis not available; start Redis on localhost:6379 or set TESTAGENT_REDIS_HOST",
)


# =============================================================================
# SessionManager Integration Tests
# =============================================================================


class TestSessionManagerIntegration:
    @pytest.fixture()
    def mgr(self) -> SessionManager:
        return SessionManager()

    async def test_session_lifecycle_events(self, mgr: SessionManager) -> None:
        """验证完整 session 生命周期中 V1.0 事件的发布。"""
        session = await mgr.create_session(name="lifecycle-test")
        collected: list[dict[str, Any]] = []

        async def collector() -> None:
            async for event in mgr.subscribe(session["id"]):
                collected.append(event)

        ct = asyncio.create_task(collector())
        await asyncio.sleep(0.02)

        await mgr.broadcast_event(
            session["id"],
            "task.snapshot_saved",
            {"task_id": "t1", "snapshot_id": "snap-001", "progress_pct": 50},
        )
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
        with pytest.raises(asyncio.CancelledError):
            await ct

        assert len(collected) >= 4
        events_found = {e["event"] for e in collected}
        assert "task.snapshot_saved" in events_found
        assert "task.resuming" in events_found
        assert "resource.usage" in events_found
        assert "quality.trend_update" in events_found

    async def test_multiple_subscribers(self, mgr: SessionManager) -> None:
        """验证多个订阅者同时接收事件。"""
        session = await mgr.create_session(name="multi-sub-test")
        collected_1: list[dict[str, Any]] = []
        collected_2: list[dict[str, Any]] = []

        async def collector_1() -> None:
            async for event in mgr.subscribe(session["id"]):
                collected_1.append(event)

        async def collector_2() -> None:
            async for event in mgr.subscribe(session["id"]):
                collected_2.append(event)

        ct1 = asyncio.create_task(collector_1())
        ct2 = asyncio.create_task(collector_2())
        await asyncio.sleep(0.02)

        await mgr.broadcast_event(
            session["id"],
            "task.started",
            {"task_id": "t1", "name": "Test login"},
        )

        await asyncio.sleep(0.05)
        ct1.cancel()
        ct2.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ct1
        with pytest.raises(asyncio.CancelledError):
            await ct2

        events_1 = [e for e in collected_1 if e["event"] == "task.started"]
        events_2 = [e for e in collected_2 if e["event"] == "task.started"]
        assert len(events_1) == 1
        assert len(events_2) == 1
        assert events_1[0]["data"]["task_id"] == "t1"
        assert events_2[0]["data"]["task_id"] == "t1"

    async def test_broadcast_to_nonexistent_session(self, mgr: SessionManager) -> None:
        """验证向不存在的 session 广播事件不会抛出异常。"""
        await mgr.broadcast_event(
            "nonexistent-id",
            "task.started",
            {"task_id": "t1"},
        )

    async def test_subscribe_completed_session(self, mgr: SessionManager) -> None:
        """验证订阅已完成的 session 立即收到终态事件。"""
        session = await mgr.create_session(name="completed-sub")
        await mgr.transition(session["id"], "planning")
        await mgr.transition(session["id"], "executing")
        await mgr.transition(session["id"], "analyzing")
        await mgr.transition(session["id"], "completed")

        collected: list[dict[str, Any]] = []
        async for event in mgr.subscribe(session["id"]):
            collected.append(event)

        assert len(collected) == 1
        assert collected[0]["event"] in ("session.completed", "session.failed")

    async def test_event_timestamp_format(self, mgr: SessionManager) -> None:
        """验证事件包含正确的 ISO 时间戳。"""
        session = await mgr.create_session(name="timestamp-test")
        collected: list[dict[str, Any]] = []

        async def collector() -> None:
            async for event in mgr.subscribe(session["id"]):
                collected.append(event)

        ct = asyncio.create_task(collector())
        await asyncio.sleep(0.02)

        await mgr.broadcast_event(
            session["id"],
            "task.progress",
            {"task_id": "t1", "progress_pct": 50},
        )

        await asyncio.sleep(0.05)
        ct.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ct

        assert len(collected) >= 1
        assert "timestamp" in collected[0]
        assert collected[0]["session_id"] == session["id"]
        assert collected[0]["event"] == "task.progress"


# =============================================================================
# EventBus Integration Tests (Redis required)
# =============================================================================


@pytest.mark.skipif(not _redis_is_available(), reason="Redis required")
class TestEventBusIntegration:
    @pytest_asyncio.fixture()
    async def redis_client(self) -> Any:
        try:
            import redis.asyncio as aioredis

            client = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=15)
            await client.ping()
            yield client
            await client.flushdb()
            await client.aclose()
        except ImportError:
            pytest.skip("redis-py not installed")

    @pytest_asyncio.fixture()
    async def event_bus(self, redis_client: Any) -> EventBus:
        return EventBus(redis_client=redis_client)

    async def test_publish_and_subscribe_single_event(self, event_bus: EventBus, redis_client: Any) -> None:
        """验证通过 Redis Pub/Sub 发布和接收单个事件。"""
        event_payload = {
            "event": "task.snapshot_saved",
            "session_id": "session-int-001",
            "data": {"task_id": "t1", "snapshot_id": "snap-001", "progress_pct": 50},
            "timestamp": datetime.now(UTC).isoformat(),
        }

        async def subscriber() -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            async for event in event_bus.subscribe("session-int-001"):
                result.append(event)
                break
            return result

        sub_task = asyncio.create_task(subscriber())
        await asyncio.sleep(0.2)

        await event_bus.publish("session-int-001", event_payload)

        received = await asyncio.wait_for(sub_task, timeout=5.0)
        assert len(received) == 1
        assert received[0]["event"] == "task.snapshot_saved"
        assert received[0]["data"]["snapshot_id"] == "snap-001"

    async def test_publish_multiple_events(self, event_bus: EventBus, redis_client: Any) -> None:
        """验证发布多个 V1.0 事件都能被接收。"""
        events_to_publish = [
            {
                "event": "task.snapshot_saved",
                "session_id": "session-int-002",
                "data": {"task_id": "t1", "snapshot_id": "snap-001", "progress_pct": 30},
                "timestamp": datetime.now(UTC).isoformat(),
            },
            {
                "event": "task.resuming",
                "session_id": "session-int-002",
                "data": {"task_id": "t1", "from_step": "step_2"},
                "timestamp": datetime.now(UTC).isoformat(),
            },
            {
                "event": "resource.usage",
                "session_id": "session-int-002",
                "data": {"cpu_pct": 60.0, "memory_mb": 2048, "disk_pct": 45.0, "running_tasks": 5},
                "timestamp": datetime.now(UTC).isoformat(),
            },
            {
                "event": "quality.trend_update",
                "session_id": "session-int-002",
                "data": {"session_id": "session-int-002", "pass_rate": 0.88, "defect_count": 3},
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ]

        received: list[dict[str, Any]] = []
        target_count = len(events_to_publish)

        async def subscriber() -> None:
            async for event in event_bus.subscribe("session-int-002"):
                received.append(event)
                if len(received) >= target_count:
                    break

        sub_task = asyncio.create_task(subscriber())
        await asyncio.sleep(0.2)

        for event in events_to_publish:
            await event_bus.publish("session-int-002", event)
            await asyncio.sleep(0.05)

        await asyncio.wait_for(sub_task, timeout=5.0)

        assert len(received) == target_count
        event_types = {e["event"] for e in received}
        assert "task.snapshot_saved" in event_types
        assert "task.resuming" in event_types
        assert "resource.usage" in event_types
        assert "quality.trend_update" in event_types

    async def test_different_channels_isolated(self, event_bus: EventBus, redis_client: Any) -> None:
        """验证不同通道的事件不会互相干扰。"""
        received_a: list[dict[str, Any]] = []
        received_b: list[dict[str, Any]] = []

        async def sub_a() -> None:
            async for event in event_bus.subscribe("channel-a"):
                received_a.append(event)
                break

        async def sub_b() -> None:
            async for event in event_bus.subscribe("channel-b"):
                received_b.append(event)
                break

        task_a = asyncio.create_task(sub_a())
        task_b = asyncio.create_task(sub_b())
        await asyncio.sleep(0.2)

        await event_bus.publish("channel-a", {"event": "task.started", "data": {}})
        await event_bus.publish("channel-b", {"event": "task.completed", "data": {}})

        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=5.0)

        assert len(received_a) == 1
        assert len(received_b) == 1
        assert received_a[0]["event"] == "task.started"
        assert received_b[0]["event"] == "task.completed"

    async def test_graceful_degradation_without_redis(self) -> None:
        """验证 Redis 不可用时 EventBus 降级行为。"""
        bus = EventBus(redis_client=None)
        await bus.publish("test", {"event": "test"})

        events: list[dict[str, Any]] = []
        async for event in bus.subscribe("test"):
            events.append(event)
        assert len(events) == 0

    async def test_unsubscribe_cleanup(self, event_bus: EventBus, redis_client: Any) -> None:
        """验证订阅取消后资源正确清理。"""
        received: list[dict[str, Any]] = []

        async def subscriber() -> None:
            async for event in event_bus.subscribe("cleanup-test"):
                received.append(event)
                break

        sub_task = asyncio.create_task(subscriber())
        await asyncio.sleep(0.2)

        await event_bus.publish(
            "cleanup-test",
            {"event": "task.started", "data": {}},
        )

        await asyncio.wait_for(sub_task, timeout=5.0)
        assert len(received) == 1
        assert received[0]["event"] == "task.started"


# =============================================================================
# WebSocket Reconnection and Persistence Tests
# =============================================================================


class TestSessionPersistence:
    @pytest_asyncio.fixture()
    async def redis_client(self) -> Any:
        if not _redis_is_available():
            pytest.skip("Redis not available")
        try:
            import redis.asyncio as aioredis

            client = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=15)
            await client.ping()
            yield client
            await client.flushdb()
            await client.aclose()
        except ImportError:
            pytest.skip("redis-py not installed")

    async def test_session_persist_and_restore(self, redis_client: Any) -> None:
        """验证 session 持久化到 Redis 后可以恢复。"""
        mgr = SessionManager(redis_client=redis_client)
        session = await mgr.create_session(name="persist-test")
        session_id = session["id"]

        await mgr._persist_to_redis(session)

        restored = await mgr._load_from_redis(session_id)
        assert restored is not None
        assert restored["id"] == session_id
        assert restored["name"] == "persist-test"

    async def test_session_persist_status(self, redis_client: Any) -> None:
        """验证 session 状态转换后也能持久化。"""
        mgr = SessionManager(redis_client=redis_client)
        session = await mgr.create_session(name="persist-status")
        session_id = session["id"]

        await mgr.transition(session_id, "planning")
        await mgr.transition(session_id, "executing")

        await mgr._persist_to_redis(session)

        restored = await mgr._load_from_redis(session_id)
        assert restored is not None
        assert restored["id"] == session_id

    async def test_persist_without_redis(self) -> None:
        """验证无 Redis 时持久化静默失败。"""
        mgr = SessionManager(redis_client=None)
        session = await mgr.create_session(name="no-redis-persist")
        await mgr._persist_to_redis(session)

        restored = await mgr._load_from_redis(session["id"])
        assert restored is None

    async def test_get_active_sessions_empty(self) -> None:
        """验证无活跃 session 时返回空列表。"""
        mgr = SessionManager()
        active = await mgr.get_active_sessions()
        assert active == []
