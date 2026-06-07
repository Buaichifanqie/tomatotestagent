from __future__ import annotations

import pytest
from pathlib import Path
from testagent.skills.app_skill_loader import AppSkillLoader


@pytest.fixture
def apps_dir(tmp_path: Path) -> Path:
    """创建包含 App Skill 的临时目录"""
    bilibili_dir = tmp_path / "apps" / "bilibili"
    bilibili_dir.mkdir(parents=True)

    # 主文件
    (bilibili_dir / "SKILL.md").write_text(
        """---
name: bilibili
version: "1.0.0"
description: 哔哩哔哩 App 测试技能
app_info:
  package_name: "tv.danmaku.bili"
triggers:
  - "bilibili"
  - "B站"
dependencies:
  skills: [app_smoke_test]
  mcp_servers: [appium, vision]
tags: [video, social, entertainment]
---

## 视觉特征库
### 首页
- **底部导航栏**：4 个 Tab
""",
        encoding="utf-8",
    )

    # 子文件
    (bilibili_dir / "search_flow.md").write_text(
        """---
parent: bilibili
version: "1.0.0"
description: 搜索功能专项测试
trigger: "搜索|search"
---

## 搜索功能测试要点
### 正常流程
1. 点击搜索图标
""",
        encoding="utf-8",
    )

    return tmp_path / "apps"


class TestAppSkillLoader:
    def test_load_main_skill(self, apps_dir: Path) -> None:
        """应正确加载主 SKILL.md"""
        loader = AppSkillLoader(apps_dir=apps_dir)
        skills = loader.load_app("bilibili")
        assert len(skills) >= 1
        main = skills[0]
        assert main.name == "bilibili"
        assert main.version == "1.0.0"
        assert "app_info" in main.meta
        assert main.meta["app_info"]["package_name"] == "tv.danmaku.bili"

    def test_load_sub_skills(self, apps_dir: Path) -> None:
        """应加载子文件"""
        loader = AppSkillLoader(apps_dir=apps_dir)
        skills = loader.load_app("bilibili")
        assert len(skills) == 2
        names = [s.name for s in skills]
        assert "bilibili" in names
        assert "search_flow" in names or any("search" in n for n in names)

    def test_list_apps(self, apps_dir: Path) -> None:
        """应列出所有可用 App"""
        loader = AppSkillLoader(apps_dir=apps_dir)
        apps = loader.list_apps()
        assert "bilibili" in apps

    def test_load_nonexistent_app(self, apps_dir: Path) -> None:
        """加载不存在的 App 应返回空列表"""
        loader = AppSkillLoader(apps_dir=apps_dir)
        skills = loader.load_app("nonexistent")
        assert skills == []

    def test_get_summary(self, apps_dir: Path) -> None:
        """应返回 App Skill 的摘要信息"""
        loader = AppSkillLoader(apps_dir=apps_dir)
        summary = loader.get_summary("bilibili")
        assert summary is not None
        assert "bilibili" in summary
        assert "tv.danmaku.bili" in summary

    def test_empty_apps_dir(self, tmp_path: Path) -> None:
        """空目录不应报错"""
        empty_dir = tmp_path / "empty_apps"
        empty_dir.mkdir()
        loader = AppSkillLoader(apps_dir=empty_dir)
        assert loader.list_apps() == []
