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

        # 模拟页面 XML
        xml_before = '<hierarchy><node class="Button" resource-id="search" text="搜索"/></hierarchy>'
        xml_after = '<hierarchy><node class="EditText" resource-id="input" text=""/></hierarchy>'

        hash_before = get_page_hash_from_source(xml_before)
        hash_after = get_page_hash_from_source(xml_after)

        # 写入缓存
        cache.put(
            page_hash_before=hash_before,
            action="tap",
            target="搜索框",
            coord={"x": 540, "y": 1200},
            page_hash_after=hash_after,
            tc_id="TC-001",
            step=3,
        )

        # 命中缓存
        entry = cache.get(hash_before, "tap", "搜索框")
        assert entry is not None
        assert entry.coord == {"x": 540, "y": 1200}
        assert entry.page_hash_after == hash_after

        # 更新缓存（回退重试后）
        cache.update(
            page_hash_before=hash_before,
            action="tap",
            target="搜索框",
            coord={"x": 600, "y": 1300},
            page_hash_after="new_hash",
            tc_id="TC-002",
            step=5,
        )

        entry = cache.get(hash_before, "tap", "搜索框")
        assert entry.coord == {"x": 600, "y": 1300}
        assert cache.stats.fallbacks == 1

    def test_different_pages_different_cache(self):
        """不同页面的相同 target 不会误命中."""
        cache = CoordinateCache()

        xml_home = '<hierarchy><node class="Button" resource-id="search" text="搜索"/></hierarchy>'
        xml_video = '<hierarchy><node class="Button" resource-id="search2" text="搜索"/></hierarchy>'

        hash_home = get_page_hash_from_source(xml_home)
        hash_video = get_page_hash_from_source(xml_video)

        # 首页搜索框
        cache.put(hash_home, "tap", "搜索框", {"x": 540, "y": 100}, "hash1", "TC-001", 1)

        # 视频页搜索框应该 miss
        entry = cache.get(hash_video, "tap", "搜索框")
        assert entry is None

    def test_stats_accuracy(self):
        """统计信息准确性."""
        cache = CoordinateCache()

        cache.put("h1", "tap", "btn", {"x": 1, "y": 2}, "h2", "TC-001", 1)

        # 2 hits, 1 miss
        cache.get("h1", "tap", "btn")
        cache.get("h1", "tap", "btn")
        cache.get("h2", "tap", "btn")

        stats = cache.stats
        assert stats.hits == 2
        assert stats.misses == 1
        assert abs(stats.hit_rate - 2 / 3) < 0.01

    def test_target_normalization(self):
        """target 标准化确保缓存命中."""
        cache = CoordinateCache()

        # 写入时有空格
        cache.put("h1", "tap", "搜索 框", {"x": 100, "y": 200}, "h2", "TC-001", 1)

        # 查询时无空格也能命中
        entry = cache.get("h1", "tap", "搜索框")
        assert entry is not None
        assert entry.coord == {"x": 100, "y": 200}

    def test_different_actions_different_cache(self):
        """不同 action 不会误命中."""
        cache = CoordinateCache()

        cache.put("h1", "tap", "搜索框", {"x": 100, "y": 200}, "h2", "TC-001", 1)

        # type action 应该 miss
        entry = cache.get("h1", "type", "搜索框")
        assert entry is None

    def test_page_hash_after_none_for_input(self):
        """input 动作的 page_hash_after 为 None."""
        cache = CoordinateCache()

        cache.put("h1", "tap", "输入框", {"x": 100, "y": 200}, None, "TC-001", 1)

        entry = cache.get("h1", "tap", "输入框")
        assert entry is not None
        assert entry.page_hash_after is None
