from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from testagent.common import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_logger = get_logger(__name__)

_CHANNEL_PREFIX = "testagent:events:"


class EventBus:
    """基于 Redis Pub/Sub 的事件总线（V1.0）。

    提供跨进程、跨 Gateway 实例的事件分发能力。
    通过 Redis Pub/Sub 通道实现事件的发布和订阅。
    MVP 阶段以降级模式运行：如果 Redis 不可用，事件总线静默跳过发布。
    """

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        self._logger = _logger

    def _channel(self, session_id: str) -> str:
        return f"{_CHANNEL_PREFIX}{session_id}"

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        """将事件发布到指定的 Redis Pub/Sub 通道。

        Args:
            channel: Pub/Sub 通道名称（不含前缀）。
            event: 要发布的事件字典。
        """
        if self._redis is None:
            self._logger.debug("EventBus: Redis not available, skipping publish")
            return
        try:
            full_channel = self._channel(channel)
            payload = json.dumps(event, default=str)
            await self._redis.publish(full_channel, payload)
            self._logger.debug(
                "Event published to Redis",
                extra={
                    "extra_data": {
                        "channel": full_channel,
                        "event_type": event.get("event", "unknown"),
                    }
                },
            )
        except Exception:
            self._logger.warning(
                "Failed to publish event to Redis",
                extra={
                    "extra_data": {
                        "channel": channel,
                        "event_type": event.get("event", "unknown"),
                    }
                },
            )

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        """订阅指定通道的事件流，返回异步事件迭代器。

        Args:
            channel: Pub/Sub 通道名称（不含前缀）。

        Yields:
            从通道接收到的事件字典。
        """
        if self._redis is None:
            self._logger.debug("EventBus: Redis not available, subscribe returns empty")
            return

        pubsub = self._redis.pubsub()
        full_channel = self._channel(channel)
        await pubsub.subscribe(full_channel)
        self._logger.debug(
            "Subscribed to Redis channel",
            extra={"extra_data": {"channel": full_channel}},
        )
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    data = message.get("data", b"{}")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    event = json.loads(data)
                    yield event
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    self._logger.warning(
                        "Failed to decode event from Redis",
                        extra={"extra_data": {"channel": full_channel, "error": str(exc)}},
                    )
        finally:
            await pubsub.unsubscribe(full_channel)
            await pubsub.aclose()
