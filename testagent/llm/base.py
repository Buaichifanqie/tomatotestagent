from __future__ import annotations

import asyncio
import heapq
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from testagent.common.errors import LLMTokenLimitError
from testagent.common.logging import get_logger

logger = get_logger(__name__)

PRIORITY_PLANNER = 0
PRIORITY_EXECUTOR = 1
PRIORITY_ANALYZER = 2

VALID_STOP_REASONS: frozenset[str] = frozenset({"end_turn", "tool_use", "max_tokens"})


class LLMResponse(BaseModel):
    content: list[dict[str, Any]]
    stop_reason: str
    usage: dict[str, Any]


@runtime_checkable
class ILLMProvider(Protocol):
    async def chat(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse: ...

    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class RateLimiter:
    __test__ = False

    def __init__(self, rpm: int = 60, tpm: int = 100000) -> None:
        self._rpm = rpm
        self._tpm = tpm
        self._tokens = float(rpm)
        self._last_refill: float = 0.0
        self._lock = asyncio.Lock()
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._seq = 0

    def _refill(self) -> None:
        loop = asyncio.get_event_loop()
        now = loop.time()
        if self._last_refill == 0.0:
            self._last_refill = now
            return
        elapsed = now - self._last_refill
        refill_rate = self._rpm / 60.0
        self._tokens = min(float(self._rpm), self._tokens + elapsed * refill_rate)
        self._last_refill = now

    def _notify_waiters(self) -> None:
        while self._waiters and self._tokens >= 1.0:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                self._tokens -= 1.0
                future.set_result(None)

    async def acquire(self, priority: int = 0) -> None:
        loop = asyncio.get_event_loop()
        future = loop.create_future()

        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            heapq.heappush(self._waiters, (priority, self._seq, future))
            self._seq += 1

        while True:
            wait_interval = 60.0 / max(self._rpm, 1)
            await asyncio.sleep(wait_interval)
            async with self._lock:
                self._refill()
                self._notify_waiters()
                if future.done():
                    return


class BudgetManager:
    __test__ = False

    def __init__(self, total_budget: int = 1000000) -> None:
        self._total_budget = total_budget
        self._used = 0
        self._lock = asyncio.Lock()

    @property
    def remaining(self) -> int:
        return max(0, self._total_budget - self._used)

    @property
    def is_exhausted(self) -> bool:
        return self._used >= self._total_budget

    async def consume(self, tokens: int, priority: int = 0) -> None:
        async with self._lock:
            if self.is_exhausted and priority > PRIORITY_PLANNER:
                raise LLMTokenLimitError(
                    "Token budget exhausted; only Planner Agent can proceed",
                    code="BUDGET_EXHAUSTED",
                    details={"used": self._used, "budget": self._total_budget},
                )
            self._used += tokens
