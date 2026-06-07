from __future__ import annotations

from dataclasses import dataclass, field
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
