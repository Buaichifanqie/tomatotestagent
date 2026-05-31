"""坐标缓存管理器.

在同一个测试会话中缓存已获取的元素坐标，避免重复的多模态 API 调用。
缓存键基于动作上下文哈希 + 动作类型 + 目标描述，缓存值包含坐标和执行后的页面哈希。
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class CacheEntry:
    """缓存条目."""

    coord: dict[str, int]
    page_hash_after: str | None
    timestamp: float
    tc_id: str
    step: int


@dataclass
class CacheStats:
    """缓存统计信息."""

    hits: int = 0
    misses: int = 0
    fallbacks: int = 0
    tokens_saved: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def cache_size(self) -> int:
        return self._cache_size

    _cache_size: int = 0


class CoordinateCache:
    """坐标缓存管理器.

    提供基于动作上下文的坐标缓存功能，支持：
    - 缓存读写（get/put）
    - 缓存更新（update，用于回退重试后更新）
    - 统计信息（hits, misses, fallbacks, hit_rate）
    """

    def __init__(self) -> None:
        self._cache: dict[str, CacheEntry] = {}
        self._stats = CacheStats()

    @property
    def stats(self) -> CacheStats:
        """获取统计信息（更新 cache_size）."""
        self._stats._cache_size = len(self._cache)
        return self._stats

    def get(self, context_hash: str, action: str, target: str) -> CacheEntry | None:
        """查询缓存."""
        key = self._make_key(context_hash, action, target)
        entry = self._cache.get(key)
        if entry:
            self._stats.hits += 1
        else:
            self._stats.misses += 1
        return entry

    def put(
        self,
        context_hash: str,
        action: str,
        target: str,
        coord: dict[str, int],
        page_hash_after: str | None,
        tc_id: str,
        step: int,
    ) -> None:
        """写入缓存."""
        key = self._make_key(context_hash, action, target)
        self._cache[key] = CacheEntry(
            coord=coord,
            page_hash_after=page_hash_after,
            timestamp=time.time(),
            tc_id=tc_id,
            step=step,
        )

    def update(
        self,
        context_hash: str,
        action: str,
        target: str,
        coord: dict[str, int],
        page_hash_after: str | None,
        tc_id: str,
        step: int,
    ) -> None:
        """更新缓存（回退重试时使用）."""
        self._stats.fallbacks += 1
        self.put(context_hash, action, target, coord, page_hash_after, tc_id, step)

    def _make_key(self, context_hash: str, action: str, target: str) -> str:
        """生成缓存键."""
        normalized_target = "".join(target.lower().split())
        return f"{context_hash}_{action}_{normalized_target}"
