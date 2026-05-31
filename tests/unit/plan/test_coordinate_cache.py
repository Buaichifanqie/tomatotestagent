from __future__ import annotations

import time

from testagent.plan.coordinate_cache import CacheEntry, CoordinateCache


class TestCacheEntry:
    """CacheEntry 数据类测试."""

    def test_create_entry(self):
        """创建缓存条目."""
        entry = CacheEntry(
            coord={"x": 540, "y": 1200},
            page_hash_after="abc123",
            timestamp=1717171200.0,
            tc_id="TC-001",
            step=3,
        )
        assert entry.coord == {"x": 540, "y": 1200}
        assert entry.page_hash_after == "abc123"
        assert entry.tc_id == "TC-001"
        assert entry.step == 3

    def test_entry_with_null_page_hash_after(self):
        """input 动作的 page_hash_after 可以为 None."""
        entry = CacheEntry(
            coord={"x": 540, "y": 1200},
            page_hash_after=None,
            timestamp=1717171200.0,
            tc_id="TC-001",
            step=3,
        )
        assert entry.page_hash_after is None


class TestCoordinateCache:
    """CoordinateCache 管理器测试."""

    def test_cache_miss(self):
        """缓存未命中返回 None."""
        cache = CoordinateCache()
        result = cache.get("abc123", "tap", "搜索框")
        assert result is None
        assert cache.stats.misses == 1

    def test_cache_hit(self):
        """缓存命中返回条目."""
        cache = CoordinateCache()
        cache.put(
            context_hash="abc123",
            action="tap",
            target="搜索框",
            coord={"x": 540, "y": 1200},
            page_hash_after="def456",
            tc_id="TC-001",
            step=3,
        )
        result = cache.get("abc123", "tap", "搜索框")
        assert result is not None
        assert result.coord == {"x": 540, "y": 1200}
        assert cache.stats.hits == 1

    def test_cache_hit_normalized_target(self):
        """target 标准化（大小写、空格）后能命中."""
        cache = CoordinateCache()
        cache.put("abc123", "tap", "搜索 框", {"x": 540, "y": 1200}, "def456", "TC-001", 3)
        result = cache.get("abc123", "tap", "搜索框")
        assert result is not None

    def test_cache_miss_different_context_hash(self):
        """不同上下文哈希未命中."""
        cache = CoordinateCache()
        cache.put("abc123", "tap", "搜索框", {"x": 540, "y": 1200}, "def456", "TC-001", 3)
        result = cache.get("xyz789", "tap", "搜索框")
        assert result is None

    def test_cache_miss_different_action(self):
        """不同 action 未命中."""
        cache = CoordinateCache()
        cache.put("abc123", "tap", "搜索框", {"x": 540, "y": 1200}, "def456", "TC-001", 3)
        result = cache.get("abc123", "type", "搜索框")
        assert result is None

    def test_update_overwrites_entry(self):
        """update 方法覆盖已有条目并记录 fallback."""
        cache = CoordinateCache()
        cache.put("abc123", "tap", "搜索框", {"x": 540, "y": 1200}, "def456", "TC-001", 3)
        cache.update("abc123", "tap", "搜索框", {"x": 600, "y": 1300}, "ghi789", "TC-002", 5)
        result = cache.get("abc123", "tap", "搜索框")
        assert result.coord == {"x": 600, "y": 1300}
        assert cache.stats.fallbacks == 1

    def test_stats_hit_rate(self):
        """命中率计算."""
        cache = CoordinateCache()
        cache.put("abc123", "tap", "搜索框", {"x": 540, "y": 1200}, "def456", "TC-001", 3)
        cache.get("abc123", "tap", "搜索框")  # hit
        cache.get("xyz789", "tap", "搜索框")  # miss
        cache.get("abc123", "tap", "搜索框")  # hit
        stats = cache.stats
        assert stats.hits == 2
        assert stats.misses == 1
        assert abs(stats.hit_rate - 2 / 3) < 0.01
