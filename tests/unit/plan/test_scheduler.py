from __future__ import annotations

import pytest

from testagent.plan.models import TestCase, TestStep
from testagent.plan.scheduler import _has_state_conflict, _infer_state, _state_distance, reorder_for_execution


class TestInferState:
    """_infer_state extracts required_state from title/step keywords."""

    def test_llm_label_preserved(self):
        tc = TestCase(id="TC-001", title="test", required_state=["logged_in"])
        assert _infer_state(tc) == {"logged_in"}

    def test_empty_when_no_clue(self):
        tc = TestCase(id="TC-001", title="搜索关键词")
        assert _infer_state(tc) == set()

    def test_logged_in_from_title(self):
        tc = TestCase(id="TC-001", title="登录状态下搜索历史显示")
        assert _infer_state(tc) == {"logged_in"}

    def test_logged_out_from_title(self):
        tc = TestCase(id="TC-001", title="未登录状态下搜索")
        assert _infer_state(tc) == {"logged_out"}

    def test_network_off_from_title(self):
        tc = TestCase(id="TC-001", title="断网时搜索提示")
        assert _infer_state(tc) == {"network_off"}

    def test_logged_in_from_steps(self):
        tc = TestCase(
            id="TC-001",
            title="点赞操作",
            steps=[TestStep(step=1, action="tap", target="登录按钮", value="")],
        )
        assert _infer_state(tc) == {"logged_in"}

    def test_logged_out_from_steps(self):
        tc = TestCase(
            id="TC-001",
            title="退出后验证",
            steps=[TestStep(step=1, action="tap", target="退出登录", value="")],
        )
        assert _infer_state(tc) == {"logged_out"}

    def test_negation_priority_over_positive(self):
        """未登录页面展示 should NOT match logged_in."""
        tc = TestCase(id="TC-001", title="未登录页面展示")
        result = _infer_state(tc)
        assert "logged_in" not in result
        assert "logged_out" in result

    def test_multi_state_from_llm(self):
        tc = TestCase(id="TC-001", title="test", required_state=["logged_in", "network_off"])
        assert _infer_state(tc) == {"logged_in", "network_off"}

    def test_no_mutation_of_original(self):
        """_infer_state must not mutate the TestCase."""
        tc = TestCase(id="TC-001", title="登录搜索", required_state=[])
        _infer_state(tc)
        assert tc.required_state == []

    def test_cross_dimension_independence(self):
        """断网且已登录 should match both network_off and logged_in."""
        tc = TestCase(id="TC-001", title="断网且已登录状态下缓存展示")
        result = _infer_state(tc)
        assert "network_off" in result
        assert "logged_in" in result

    def test_negation_does_not_block_other_dimension(self):
        """未登录断网提示 should match logged_out and network_off."""
        tc = TestCase(id="TC-001", title="未登录断网提示")
        result = _infer_state(tc)
        assert "logged_out" in result
        assert "network_off" in result


class TestStateDistance:
    """_state_distance counts dimension-aware transitions."""

    def test_same_state(self):
        assert _state_distance({"logged_in"}, {"logged_in"}) == 0

    def test_subset(self):
        assert _state_distance({"logged_in"}, {"logged_in", "network_off"}) == 1

    def test_disjoint(self):
        """logged_in → network_off costs 2: undo auth + set up network."""
        assert _state_distance({"logged_in"}, {"network_off"}) == 2

    def test_empty_target(self):
        """logged_in → {} costs 1 (need to undo logged_in)."""
        assert _state_distance({"logged_in"}, set()) == 1

    def test_empty_current(self):
        assert _state_distance(set(), {"logged_in", "network_off"}) == 2

    def test_superset(self):
        """logged_in+network_off → logged_in costs 1 (need to undo network_off)."""
        assert _state_distance({"logged_in", "network_off"}, {"logged_in"}) == 1

    def test_same_dimension_switch(self):
        """logged_in → logged_out costs 1 (switch within auth dimension)."""
        assert _state_distance({"logged_in"}, {"logged_out"}) == 1

    def test_cross_dimension_plus_switch(self):
        """logged_in+network_off → logged_out costs 2 (switch auth + undo network)."""
        assert _state_distance({"logged_in", "network_off"}, {"logged_out"}) == 2

    def test_empty_to_empty(self):
        assert _state_distance(set(), set()) == 0


class TestHasStateConflict:
    """_has_state_conflict detects dimension-level conflicts."""

    def test_no_conflict_same(self):
        assert _has_state_conflict({"logged_in"}, {"logged_in"}) is False

    def test_no_conflict_subset(self):
        """current has logged_in, needed doesn't care — no conflict."""
        assert _has_state_conflict({"logged_in"}, set()) is False

    def test_conflict_extra_dimension_in_current(self):
        """current={logged_in, network_off}, needed={logged_in} — extra network dim conflicts."""
        assert _has_state_conflict({"logged_in", "network_off"}, {"logged_in"}) is True

    def test_conflict_same_dimension(self):
        """current=logged_in, needed=logged_out — auth dimension conflicts."""
        assert _has_state_conflict({"logged_in"}, {"logged_out"}) is True

    def test_conflict_with_multi_state(self):
        """current={logged_in, network_off}, needed={logged_out} — auth conflicts."""
        assert _has_state_conflict({"logged_in", "network_off"}, {"logged_out"}) is True

    def test_conflict_network_dimension(self):
        """current=network_off, needed=network_on — network dimension conflicts."""
        assert _has_state_conflict({"network_off"}, {"network_on"}) is True

    def test_no_conflict_empty(self):
        assert _has_state_conflict(set(), set()) is False


class TestReorderForExecution:
    """reorder_for_execution uses greedy minimum-switch algorithm."""

    def test_single_tc(self):
        tcs = [TestCase(id="TC-001", title="test")]
        result = reorder_for_execution(tcs)
        assert len(result) == 1
        assert result[0].id == "TC-001"

    def test_empty_list(self):
        assert reorder_for_execution([]) == []

    def test_no_mutation(self):
        """Original list must not be mutated."""
        tcs = [
            TestCase(id="TC-001", title="未登录搜索", priority="P1"),
            TestCase(id="TC-002", title="登录点赞", priority="P1"),
        ]
        original_ids = [tc.id for tc in tcs]
        reorder_for_execution(tcs)
        assert [tc.id for tc in tcs] == original_ids

    def test_same_state_preserves_order(self):
        """Original order must be preserved — no reordering by state group."""
        tcs = [
            TestCase(id="TC-001", title="未登录搜索", priority="P1"),
            TestCase(id="TC-002", title="登录点赞", priority="P1"),
            TestCase(id="TC-003", title="未登录浏览", priority="P1"),
            TestCase(id="TC-004", title="登录评论", priority="P1"),
        ]
        result = reorder_for_execution(tcs)
        assert [tc.id for tc in result] == ["TC-001", "TC-002", "TC-003", "TC-004"]

    def test_priority_preserves_order(self):
        """Original order preserved regardless of priority."""
        tcs = [
            TestCase(id="TC-001", title="登录P2", priority="P2"),
            TestCase(id="TC-002", title="登录P0", priority="P0"),
            TestCase(id="TC-003", title="登录P1", priority="P1"),
        ]
        result = reorder_for_execution(tcs)
        ids = [tc.id for tc in result]
        assert ids == ["TC-001", "TC-002", "TC-003"]

    def test_core_preserves_order(self):
        """Original order preserved — core flag does not affect ordering."""
        tcs = [
            TestCase(id="TC-001", title="登录P1普通", priority="P1", is_core=False),
            TestCase(id="TC-002", title="登录P1核心", priority="P1", is_core=True),
        ]
        result = reorder_for_execution(tcs)
        ids = [tc.id for tc in result]
        assert ids == ["TC-001", "TC-002"]

    def test_states_inferred_without_reordering(self):
        """States are inferred on TCs but order stays the same."""
        tcs = [
            TestCase(id="TC-001", title="登录搜索", priority="P1"),
            TestCase(id="TC-002", title="通用操作", priority="P1"),
            TestCase(id="TC-003", title="登录评论", priority="P1"),
        ]
        result = reorder_for_execution(tcs)
        ids = [tc.id for tc in result]
        assert ids == ["TC-001", "TC-002", "TC-003"]
        # States should be inferred
        assert "logged_in" in _infer_state(result[0])
        assert "logged_in" in _infer_state(result[2])

    def test_mixed_states_preserves_order(self):
        """Mixed states — order is still preserved."""
        tcs = [
            TestCase(id="TC-001", title="断网提示", priority="P1"),
            TestCase(id="TC-002", title="登录搜索", priority="P1"),
            TestCase(id="TC-003", title="未登录浏览", priority="P1"),
        ]
        result = reorder_for_execution(tcs)
        assert [tc.id for tc in result] == ["TC-001", "TC-002", "TC-003"]

    def test_multi_state_preserves_order(self):
        """Multi-state cases — order preserved."""
        tcs = [
            TestCase(id="TC-001", title="普通搜索", priority="P1"),
            TestCase(id="TC-002", title="登录且断网测试", priority="P1", required_state=["logged_in", "network_off"]),
            TestCase(id="TC-003", title="登录搜索", priority="P1"),
        ]
        result = reorder_for_execution(tcs)
        ids = [tc.id for tc in result]
        assert ids == ["TC-001", "TC-002", "TC-003"]

    def test_mixed_states_sorted_correctly(self):
        tcs = [
            TestCase(id="TC-001", title="断网提示", priority="P1"),
            TestCase(id="TC-002", title="登录搜索", priority="P1"),
            TestCase(id="TC-003", title="未登录浏览", priority="P1"),
        ]
        result = reorder_for_execution(tcs)
        assert len(result) == 3
        result_ids = {tc.id for tc in result}
        assert result_ids == {"TC-001", "TC-002", "TC-003"}
