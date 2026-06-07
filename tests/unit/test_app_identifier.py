from __future__ import annotations

import pytest
from pathlib import Path
from testagent.skills.app_identifier import AppIdentifier, IdentificationResult


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """创建临时 skills 目录，包含一个 App Skill"""
    app_dir = tmp_path / "apps" / "bilibili"
    app_dir.mkdir(parents=True)
    skill_md = app_dir / "SKILL.md"
    skill_md.write_text(
        """---
name: bilibili
version: "1.0.0"
description: 哔哩哔哩 App 测试技能
app_info:
  package_name: "tv.danmaku.bili"
triggers:
  - "bilibili"
  - "哔哩哔哩"
  - "B站"
  - regex: "tv\\\\.danmaku\\\\.bili"
dependencies:
  skills: [app_smoke_test]
  mcp_servers: [appium, vision]
tags: [video, social, entertainment]
---

## 视觉特征库
""",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def identifier(skills_dir: Path) -> AppIdentifier:
    return AppIdentifier(skills_dir=skills_dir)


class TestIdentify:
    def test_exact_trigger_match(self, identifier: AppIdentifier) -> None:
        """精确触发词匹配应返回高置信度"""
        results = identifier.identify("测试B站搜索功能")
        assert len(results) >= 1
        app_name, confidence = results[0]
        assert app_name == "bilibili"
        assert confidence >= 0.9

    def test_package_name_match(self, identifier: AppIdentifier) -> None:
        """包名匹配应返回最高置信度"""
        results = identifier.identify("测试 tv.danmaku.bili 的搜索")
        assert len(results) >= 1
        app_name, confidence = results[0]
        assert app_name == "bilibili"
        assert confidence >= 0.95

    def test_alias_match(self, identifier: AppIdentifier) -> None:
        """别名匹配（哔哩哔哩）应返回高置信度"""
        results = identifier.identify("测试哔哩哔哩的播放功能")
        assert len(results) >= 1
        assert results[0][0] == "bilibili"
        assert results[0][1] >= 0.9

    def test_no_match(self, identifier: AppIdentifier) -> None:
        """无匹配时应返回空列表"""
        results = identifier.identify("测试登录功能")
        assert results == []

    def test_regex_trigger_match(self, identifier: AppIdentifier) -> None:
        """正则触发词匹配应返回中高置信度"""
        results = identifier.identify("请测试tv.danmaku.bili应用")
        assert len(results) >= 1
        assert results[0][0] == "bilibili"
        assert results[0][1] >= 0.8


class TestIdentifyWithAction:
    def test_high_confidence_auto(self, identifier: AppIdentifier) -> None:
        """高置信度应返回 auto 动作"""
        result = identifier.identify_with_action("测试B站搜索")
        assert result.app_name == "bilibili"
        assert result.action == "auto"
        assert result.confidence >= 0.9

    def test_no_match_ask(self, identifier: AppIdentifier) -> None:
        """无匹配应返回 ask 动作"""
        result = identifier.identify_with_action("测试登录功能")
        assert result.app_name == ""
        assert result.action == "ask"
        assert result.confidence == 0.0


class TestLoadTriggers:
    def test_loads_from_apps_subdirectory(self, skills_dir: Path) -> None:
        """应从 skills/apps/ 子目录加载触发词"""
        identifier = AppIdentifier(skills_dir=skills_dir)
        results = identifier.identify("B站")
        assert len(results) >= 1

    def test_empty_skills_dir(self, tmp_path: Path) -> None:
        """空目录不应报错"""
        empty_dir = tmp_path / "empty_skills"
        empty_dir.mkdir()
        (empty_dir / "apps").mkdir()
        identifier = AppIdentifier(skills_dir=empty_dir)
        results = identifier.identify("B站")
        assert results == []
