from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from testagent.common import get_logger

if TYPE_CHECKING:
    from testagent.llm.base import ILLMProvider

_logger = get_logger(__name__)


@dataclass
class ExploreReport:
    """AppExplorer 探索报告。"""
    app_name: str
    package_name: str
    pages: list[dict] = field(default_factory=list)
    popups: list[dict] = field(default_factory=list)
    flows: list[dict] = field(default_factory=list)
    elements: list[dict] = field(default_factory=list)


@dataclass
class GeneratedSkill:
    """生成的 Skill 文件。"""
    main_file: Path
    sub_files: list[Path] = field(default_factory=list)
    confidence_notes: list[str] = field(default_factory=list)


class AppSkillGenerator:
    """AI 生成 App Skill 的流水线（Phase 3 实现）。"""

    def __init__(
        self,
        llm_provider: ILLMProvider | None = None,
        output_dir: Path | str = "skills/apps",
    ) -> None:
        self._llm = llm_provider
        self._output_dir = Path(output_dir)

    async def generate(
        self,
        app_name: str,
        package_name: str = "",
        prd_content: str | None = None,
    ) -> GeneratedSkill:
        """生成 App Skill 文件。

        Phase 3 实现完整逻辑，当前返回模板文件。
        """
        _logger.info(
            "App Skill generation started",
            extra={"extra_data": {"app": app_name, "has_prd": prd_content is not None}},
        )

        # 阶段 1: AppExplorer 探索（待实现）
        # explore_report = await self._explore_app(app_name, package_name)

        # 阶段 2: PRD 增强（待实现）
        # if prd_content:
        #     explore_report = await self._enhance_with_prd(explore_report, prd_content)

        # 阶段 3: 生成 Skill 文件（当前生成模板）
        app_dir = self._output_dir / app_name
        app_dir.mkdir(parents=True, exist_ok=True)

        main_file = app_dir / "SKILL.md"
        main_file.write_text(
            self._generate_template(app_name, package_name),
            encoding="utf-8",
        )

        return GeneratedSkill(
            main_file=main_file,
            sub_files=[],
            confidence_notes=["Template generated — AI exploration not yet implemented"],
        )

    def _generate_template(self, app_name: str, package_name: str) -> str:
        """生成 App Skill 模板文件。"""
        return f"""---
name: {app_name}
version: "1.0.0"
description: {app_name} App 测试技能
app_info:
  package_name: "{package_name}"
  launch_activity: ""
  login_method: ""
  platforms: [android, ios]
triggers:
  - "{app_name}"
dependencies:
  skills: [app_smoke_test]
  mcp_servers: [appium, vision]
tags: []
---

## 视觉特征库

### 首页
<!-- TODO: 由 AI 探索生成，或人工填写 -->

### 其他页面
<!-- TODO: 补充其他核心页面的视觉特征 -->

## 核心业务流

### 1. 冷启动
<!-- TODO: 描述冷启动验证要点 -->

### 2. 常见弹窗处理
| 弹窗 | 视觉特征 | 处理方式 |
|------|---------|---------|
<!-- TODO: 补充常见弹窗 -->

### 3. 已知陷阱
<!-- TODO: 补充已知的测试陷阱 -->
"""
