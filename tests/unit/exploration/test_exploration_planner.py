"""Exploration planner tests for the AppExplorer feature."""
from __future__ import annotations

import json

import pytest

from testagent.exploration.exploration_planner import (
    ExplorationPlanner,
    ExplorationTarget,
    ReachAction,
)


# --- ReachAction tests ---


class TestReachAction:
    """ReachAction dataclass tests."""

    def test_from_dict_tap(self):
        """ReachAction.from_dict parses a tap action correctly."""
        d = {"type": "tap", "target_hint": "搜索按钮"}
        action = ReachAction.from_dict(d)
        assert action.type == "tap"
        assert action.target_hint == "搜索按钮"
        assert action.input_value == ""

    def test_from_dict_type(self):
        """ReachAction.from_dict parses a type action with input_value."""
        d = {"type": "type", "target_hint": "搜索输入框", "input_value": "测试"}
        action = ReachAction.from_dict(d)
        assert action.type == "type"
        assert action.target_hint == "搜索输入框"
        assert action.input_value == "测试"


# --- ExplorationTarget tests ---


class TestExplorationTarget:
    """ExplorationTarget dataclass tests."""

    def test_from_dict(self):
        """ExplorationTarget.from_dict parses a full target dict."""
        d = {
            "target_name": "搜索结果页",
            "keywords": ["搜索", "search"],
            "reach_actions": [
                {"type": "tap", "target_hint": "搜索入口"},
                {"type": "type", "target_hint": "搜索框", "input_value": "测试"},
                {"type": "tap", "target_hint": "搜索按钮"},
            ],
            "priority": 1,
        }
        target = ExplorationTarget.from_dict(d)
        assert target.target_name == "搜索结果页"
        assert target.keywords == ["搜索", "search"]
        assert len(target.reach_actions) == 3
        assert isinstance(target.reach_actions[0], ReachAction)
        assert target.reach_actions[1].input_value == "测试"
        assert target.priority == 1

    def test_from_dict_defaults_priority(self):
        """ExplorationTarget.from_dict defaults priority to 2."""
        d = {
            "target_name": "设置页",
            "keywords": ["设置"],
            "reach_actions": [{"type": "tap", "target_hint": "设置入口"}],
        }
        target = ExplorationTarget.from_dict(d)
        assert target.priority == 2


# --- Planner tests ---


VALID_LLM_OUTPUT = json.dumps(
    [
        {
            "target_name": "搜索结果页",
            "keywords": ["搜索", "search"],
            "reach_actions": [
                {"type": "tap", "target_hint": "搜索入口"},
                {"type": "type", "target_hint": "搜索框", "input_value": "测试"},
                {"type": "tap", "target_hint": "搜索按钮"},
            ],
            "priority": 1,
        },
        {
            "target_name": "个人主页",
            "keywords": ["我的", "个人", "profile"],
            "reach_actions": [
                {"type": "tap", "target_hint": "我的Tab"}
            ],
            "priority": 2,
        },
    ]
)


class TestExplorationPlanner:
    """ExplorationPlanner tests."""

    @pytest.mark.asyncio
    async def test_plan_parses_llm_output(self):
        """Planner parses valid JSON from LLM into ExplorationTarget list."""

        async def fake_llm(text: str) -> str:
            return VALID_LLM_OUTPUT

        planner = ExplorationPlanner(llm_callable=fake_llm)
        targets = await planner.plan("PRD: 该App支持搜索和个人主页")

        assert len(targets) == 2
        assert all(isinstance(t, ExplorationTarget) for t in targets)
        assert targets[0].target_name == "搜索结果页"
        assert targets[1].target_name == "个人主页"

    @pytest.mark.asyncio
    async def test_plan_handles_llm_failure(self):
        """Planner returns empty list when LLM raises an exception."""

        async def failing_llm(text: str) -> str:
            raise RuntimeError("LLM service unavailable")

        planner = ExplorationPlanner(llm_callable=failing_llm)
        targets = await planner.plan("PRD text")

        assert targets == []

    @pytest.mark.asyncio
    async def test_plan_handles_invalid_json(self):
        """Planner returns empty list when LLM returns invalid JSON."""

        async def bad_llm(text: str) -> str:
            return "this is not json at all {broken"

        planner = ExplorationPlanner(llm_callable=bad_llm)
        targets = await planner.plan("PRD text")

        assert targets == []

    @pytest.mark.asyncio
    async def test_plan_handles_json_in_markdown(self):
        """Planner strips markdown fences and parses JSON correctly."""
        wrapped = f"```json\n{VALID_LLM_OUTPUT}\n```"

        async def markdown_llm(text: str) -> str:
            return wrapped

        planner = ExplorationPlanner(llm_callable=markdown_llm)
        targets = await planner.plan("PRD text")

        assert len(targets) == 2
        assert targets[0].target_name == "搜索结果页"

    @pytest.mark.asyncio
    async def test_plan_sorts_by_priority(self):
        """Planner sorts targets by priority ascending."""
        unsorted_output = json.dumps(
            [
                {
                    "target_name": "可选页",
                    "keywords": ["可选"],
                    "reach_actions": [{"type": "tap", "target_hint": "入口"}],
                    "priority": 3,
                },
                {
                    "target_name": "核心页",
                    "keywords": ["核心"],
                    "reach_actions": [{"type": "tap", "target_hint": "入口"}],
                    "priority": 1,
                },
                {
                    "target_name": "重要页",
                    "keywords": ["重要"],
                    "reach_actions": [{"type": "tap", "target_hint": "入口"}],
                    "priority": 2,
                },
            ]
        )

        async def unsorted_llm(text: str) -> str:
            return unsorted_output

        planner = ExplorationPlanner(llm_callable=unsorted_llm)
        targets = await planner.plan("PRD text")

        assert len(targets) == 3
        assert [t.priority for t in targets] == [1, 2, 3]
        assert targets[0].target_name == "核心页"
        assert targets[2].target_name == "可选页"
