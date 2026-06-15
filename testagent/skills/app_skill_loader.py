from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from testagent.common import get_logger
from testagent.skills.parser import MarkdownParser

_logger = get_logger(__name__)

SKILL_FILE_GLOB = "*.md"


@dataclass
class AppSkillFile:
    """一个 App Skill 文件的解析结果。"""
    name: str
    version: str
    file_path: Path
    meta: dict[str, object]
    body: str
    is_main: bool = False


class AppSkillLoader:
    """加载 skills/apps/ 下的 App Skill 文件。"""

    def __init__(self, apps_dir: Path | str) -> None:
        self._apps_dir = Path(apps_dir)
        self._parser = MarkdownParser()

    def list_apps(self) -> list[str]:
        """列出所有可用的 App 名称。"""
        if not self._apps_dir.exists():
            return []
        return sorted(
            d.name for d in self._apps_dir.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists()
        )

    def load_app(self, app_name: str) -> list[AppSkillFile]:
        """加载指定 App 的所有 Skill 文件。"""
        app_dir = self._apps_dir / app_name
        if not app_dir.exists() or not app_dir.is_dir():
            _logger.debug(
                "App Skill directory not found",
                extra={"extra_data": {"app": app_name, "dir": str(app_dir)}},
            )
            return []

        skills: list[AppSkillFile] = []

        # 先加载主文件
        main_file = app_dir / "SKILL.md"
        if main_file.exists():
            skill = self._load_file(main_file, is_main=True)
            if skill:
                skills.append(skill)

        # 再加载子文件
        for md_file in sorted(app_dir.glob("*.md")):
            if md_file.name == "SKILL.md":
                continue
            skill = self._load_file(md_file, is_main=False)
            if skill:
                skills.append(skill)

        return skills

    def get_summary(self, app_name: str) -> str | None:
        """获取 App Skill 的摘要信息（用于 system prompt 注入）。"""
        skills = self.load_app(app_name)
        if not skills:
            return None

        main_skill = next((s for s in skills if s.is_main), skills[0])
        meta = main_skill.meta

        lines: list[str] = []
        lines.append(f"App: {app_name}")

        # app_info 摘要
        app_info = meta.get("app_info", {})
        if isinstance(app_info, dict):
            pkg = app_info.get("package_name", "")
            if pkg:
                lines.append(f"包名: {pkg}")
            login = app_info.get("login_method", "")
            if login:
                lines.append(f"登录方式: {login}")

        # 从 body 提取关键信息
        body = main_skill.body
        if body:
            # 提取弹窗处理规则
            popup_section = self._extract_section(body, "常见弹窗处理")
            if popup_section:
                lines.append(f"弹窗规则:\n{popup_section}")

            # 提取视觉特征摘要（首页部分）
            visual_section = self._extract_section(body, "首页")
            if visual_section:
                visual_lines = visual_section.split("\n")[:5]
                lines.append(f"首页视觉特征:\n{''.join(visual_lines)}")

        # 子文件列表
        sub_skills = [s for s in skills if not s.is_main]
        if sub_skills:
            sub_names = ", ".join(s.name for s in sub_skills)
            lines.append(f"专项功能: {sub_names}")

        return "\n".join(lines)

    def get_full_content(self, app_name: str) -> str:
        """返回 app 所有 skill 文件的完整内容，用于注入执行上下文。"""
        files = self.load_app(app_name)
        if not files:
            return ""
        parts: list[str] = []
        for f in files:
            if f.body.strip():
                header = f"## {f.file_path.stem}" if not f.is_main else f"## {app_name} 主技能"
                parts.append(f"{header}\n\n{f.body.strip()}")
        return "\n\n---\n\n".join(parts)

    def get_matching_content(self, app_name: str, user_intent: str) -> str:
        """根据用户意图只返回匹配的子 skill 内容（不包含主 SKILL.md）。

        用于 TC 生成：只注入与用户需求相关的领域知识，避免范围蔓延。
        例如用户说"测试视频播放功能"，只返回 video_playback.md 的内容。
        """
        import re

        files = self.load_app(app_name)
        if not files:
            return ""

        intent_lower = user_intent.lower()
        matched_parts: list[str] = []

        for f in files:
            if f.is_main or not f.body.strip():
                continue
            # 从 frontmatter 获取 trigger
            trigger = str(f.meta.get("trigger", ""))
            if not trigger:
                continue
            # 检查用户意图是否匹配 trigger 中的任一关键词
            keywords = [kw.strip() for kw in trigger.split("|") if kw.strip()]
            if any(kw in intent_lower for kw in keywords):
                header = f"## {f.file_path.stem}"
                matched_parts.append(f"{header}\n\n{f.body.strip()}")

        return "\n\n---\n\n".join(matched_parts)

    def find_app_by_package(self, package_name: str) -> str | None:
        """根据包名查找对应的 app skill 名称。"""
        for app_name in self.list_apps():
            files = self.load_app(app_name)
            for f in files:
                meta = f.meta or {}
                info = meta.get("app_info", {})
                if isinstance(info, dict) and info.get("package_name") == package_name:
                    return app_name
        return None

    def get_toggle_groups(self, app_name: str) -> list[list[str]]:
        """收集 app 所有 skill 文件中定义的 toggle_groups。

        各 skill 文件的 toggle_groups 会被合并为一份完整列表。
        没有定义时返回空列表。
        """
        files = self.load_app(app_name)
        merged: list[list[str]] = []
        seen: set[str] = set()
        for f in files:
            raw = (f.meta or {}).get("toggle_groups")
            if not isinstance(raw, list):
                continue
            for group in raw:
                if not isinstance(group, list) or not group:
                    continue
                # 用组内第一个元素去重
                key = str(group[0])
                if key not in seen:
                    seen.add(key)
                    merged.append([str(item) for item in group])
        return merged

    def _load_file(self, path: Path, is_main: bool = False) -> AppSkillFile | None:
        """解析单个 Skill 文件。"""
        try:
            content = path.read_text(encoding="utf-8")
            meta, body = self._parser.parse(content)
            name = str(meta.get("name", path.stem))
            version = str(meta.get("version", "1.0.0"))
            return AppSkillFile(
                name=name,
                version=version,
                file_path=path,
                meta=meta,
                body=body,
                is_main=is_main,
            )
        except Exception as exc:
            _logger.warning(
                "Failed to parse App Skill file",
                extra={"extra_data": {"path": str(path), "error": str(exc)}},
            )
            return None

    def _extract_section(self, body: str, section_title: str) -> str:
        """从 markdown body 中提取指定章节内容。"""
        lines = body.split("\n")
        in_section = False
        section_lines: list[str] = []
        section_level = 0

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
                if heading == section_title:
                    in_section = True
                    section_level = len(stripped) - len(stripped.lstrip("#"))
                    continue
                elif in_section:
                    current_level = len(stripped) - len(stripped.lstrip("#"))
                    if current_level <= section_level:
                        break
            elif in_section:
                section_lines.append(line)

        return "\n".join(section_lines).strip()
