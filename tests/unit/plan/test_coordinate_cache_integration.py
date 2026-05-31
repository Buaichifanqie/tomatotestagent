"""坐标缓存端到端集成测试."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.plan.coordinate_cache import CoordinateCache
from testagent.plan.ui_tree_utils import get_page_hash_from_source


class TestCoordinateCacheIntegration:
    """端到端集成测试."""

    def test_cache_lifecycle(self):
        """完整缓存生命周期：写入 -> 命中 -> 更新."""
        cache = CoordinateCache()

        # 模拟动作上下文哈希
        context_hash = "launch_app_abc123"

        # 写入缓存
        cache.put(
            context_hash=context_hash,
            action="tap",
            target="搜索框",
            coord={"x": 540, "y": 1200},
            page_hash_after="def456",
            tc_id="TC-001",
            step=3,
        )

        # 命中缓存
        entry = cache.get(context_hash, "tap", "搜索框")
        assert entry is not None
        assert entry.coord == {"x": 540, "y": 1200}
        assert entry.page_hash_after == "def456"

        # 更新缓存（回退重试后）
        cache.update(
            context_hash=context_hash,
            action="tap",
            target="搜索框",
            coord={"x": 600, "y": 1300},
            page_hash_after="new_hash",
            tc_id="TC-002",
            step=5,
        )

        entry = cache.get(context_hash, "tap", "搜索框")
        assert entry.coord == {"x": 600, "y": 1300}
        assert cache.stats.fallbacks == 1

    def test_different_context_different_cache(self):
        """不同动作上下文的相同 target 不会误命中."""
        cache = CoordinateCache()

        # 冷启动后点击搜索框
        context1 = "launch_app"
        cache.put(context1, "tap", "搜索框", {"x": 540, "y": 100}, "hash1", "TC-001", 1)

        # 从视频详情页返回后点击搜索框（上下文不同）
        context2 = "tap_video_back"
        entry = cache.get(context2, "tap", "搜索框")
        assert entry is None

    def test_stats_accuracy(self):
        """统计信息准确性."""
        cache = CoordinateCache()

        cache.put("ctx1", "tap", "btn", {"x": 1, "y": 2}, "h2", "TC-001", 1)

        # 2 hits, 1 miss
        cache.get("ctx1", "tap", "btn")
        cache.get("ctx1", "tap", "btn")
        cache.get("ctx2", "tap", "btn")

        stats = cache.stats
        assert stats.hits == 2
        assert stats.misses == 1
        assert abs(stats.hit_rate - 2 / 3) < 0.01

    def test_target_normalization(self):
        """target 标准化确保缓存命中."""
        cache = CoordinateCache()

        # 写入时有空格
        cache.put("ctx1", "tap", "搜索 框", {"x": 100, "y": 200}, "h2", "TC-001", 1)

        # 查询时无空格也能命中
        entry = cache.get("ctx1", "tap", "搜索框")
        assert entry is not None
        assert entry.coord == {"x": 100, "y": 200}

    def test_different_actions_different_cache(self):
        """不同 action 不会误命中."""
        cache = CoordinateCache()

        cache.put("ctx1", "tap", "搜索框", {"x": 100, "y": 200}, "h2", "TC-001", 1)

        # type action 应该 miss
        entry = cache.get("ctx1", "type", "搜索框")
        assert entry is None

    def test_page_hash_after_none_for_input(self):
        """input 动作的 page_hash_after 为 None."""
        cache = CoordinateCache()

        cache.put("ctx1", "tap", "输入框", {"x": 100, "y": 200}, None, "TC-001", 1)

        entry = cache.get("ctx1", "tap", "输入框")
        assert entry is not None
        assert entry.page_hash_after is None
