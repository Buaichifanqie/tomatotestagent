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

    def get_hard_rules(self, app_name: str, user_intent: str = "") -> str:
        """提取所有匹配的子 skill 中的「硬性约束」段落。

        硬性约束（如"不要点击竖屏视频"）必须完整注入 prompt，不做截断。
        只加载与 user_intent 匹配的子 skill（与 get_matching_content 一致）。

        Returns:
            所有硬性约束段落的拼接文本，或空字符串。
        """
        import re as _re

        files = self.load_app(app_name)
        if not files:
            return ""

        intent_lower = user_intent.lower()
        rules: list[str] = []

        for f in files:
            if not f.body.strip():
                continue
            # 主 skill 总是加载；子 skill 按 trigger 匹配
            if not f.is_main:
                trigger = str(f.meta.get("trigger", ""))
                if trigger and user_intent:
                    keywords = [kw.strip() for kw in trigger.split("|") if kw.strip()]
                    if not any(kw in intent_lower for kw in keywords):
                        continue
            section = self._extract_section(f.body, "硬性约束")
            if section:
                rules.append(section)

        return "\n\n".join(rules)

    def get_ui_knowledge(self, app_name: str, user_intent: str) -> str:
        """根据用户意图返回 UI 知识层内容（视觉特征库 + 元素名称 + 交互快捷方式），不包含执行策略。

        用于 TC 生成阶段：告诉 LLM "有哪些 UI 元素"以及"有哪些快捷交互方式"，但不告诉它详细的执行流程。
        具体来说：
        - 提取：视觉特征库、交互快捷方式（标题含"快捷方式"的子章节）、常见弹窗处理、toggle_groups
        - 跳过：核心业务流中的详细步骤、已知陷阱、控制栏交互的关键知识（含 tap_first 示例）
        """
        files = self.load_app(app_name)
        if not files:
            return ""

        intent_lower = user_intent.lower()
        matched_parts: list[str] = []

        for f in files:
            if not f.body.strip():
                continue

            # 子 skill：只处理与用户意图匹配的
            if not f.is_main:
                trigger = str(f.meta.get("trigger", ""))
                if not trigger:
                    continue
                keywords = [kw.strip() for kw in trigger.split("|") if kw.strip()]
                if not any(kw in intent_lower for kw in keywords):
                    continue

            # 提取视觉特征库（包含所有子章节：播放页、控制栏、全屏模式等）
            visual_section = self._extract_section(f.body, "视觉特征库")
            if visual_section:
                header = f"## {f.file_path.stem} - 视觉特征" if not f.is_main else f"## {app_name} - 视觉特征"
                matched_parts.append(f"{header}\n\n{visual_section}")

            # 提取核心业务流中的交互快捷方式（标题含"快捷方式"的子章节）
            shortcuts = self._extract_sections_by_keyword(f.body, "核心业务流", "快捷方式")
            if shortcuts:
                header = f"## {f.file_path.stem} - 交互快捷方式" if not f.is_main else f"## {app_name} - 交互快捷方式"
                matched_parts.append(f"{header}\n\n{shortcuts}")

            # 主 skill 额外提取弹窗处理规则（UI 层知识）
            if f.is_main:
                popup_section = self._extract_section(f.body, "常见弹窗处理")
                if popup_section:
                    matched_parts.append(f"## 常见弹窗处理\n\n{popup_section}")

            # 提取 toggle_groups（状态翻转对）
            toggle_groups = f.meta.get("toggle_groups")
            if isinstance(toggle_groups, list) and toggle_groups:
                pairs = "\n".join(f"  - {' / '.join(str(item) for item in group)}" for group in toggle_groups if isinstance(group, list))
                if pairs:
                    matched_parts.append(f"## 状态翻转对（toggle_groups）\n\n{pairs}")

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

    def get_hidden_controls(self, app_name: str) -> dict[str, list[str]]:
        """获取需要 tap_first 的隐藏控件配置。

        返回格式: {"trigger_area": "视频区域", "targets": ["暂停按钮", "全屏按钮", ...]}
        空 dict 表示没有配置 hidden_controls。
        """
        files = self.load_app(app_name)
        for f in files:
            hc = (f.meta or {}).get("hidden_controls")
            if isinstance(hc, dict) and hc.get("targets"):
                return {
                    "trigger_area": str(hc.get("trigger_area", "")),
                    "targets": [str(t) for t in hc["targets"]],
                }
        return {}

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

    @staticmethod
    def _expand_keywords(keywords: list[str]) -> list[str]:
        """扩展关键词：对中文长词提取 2 字符滑动窗口子串。

        例：「全屏按钮」→ ['全屏按钮', '全屏', '屏按', '按钮']
        这样能匹配到只包含「全屏」但不包含「全屏按钮」的段落。
        无意义子串（如「屏按」）不会匹配任何内容，不影响结果。
        """
        expanded = list(keywords)
        for kw in keywords:
            if len(kw) > 2:
                for i in range(len(kw) - 1):
                    sub = kw[i:i + 2]
                    if any('一' <= c <= '鿿' for c in sub):
                        expanded.append(sub)
        return expanded

    def get_relevant_sections(
        self,
        app_name: str,
        user_intent: str = "",
        keywords: list[str] | None = None,
        max_length: int = 3000,
    ) -> str:
        """根据关键词从匹配的 skill 中提取相关段落。

        按关键词匹配度评分排序，优先返回最相关的段落，
        总长度不超过 max_length。
        自动排除「硬性约束」段落（已通过 get_hard_rules 单独注入）。
        """
        if not keywords:
            return ""

        files = self.load_app(app_name)
        if not files:
            return ""

        intent_lower = user_intent.lower()
        expanded_kws = self._expand_keywords(keywords)

        # 原始关键词权重 3，扩展子串权重 1
        kw_weights: list[tuple[str, int]] = []
        for kw in keywords:
            kw_weights.append((kw.lower(), 3))
        for kw in expanded_kws:
            kw_lower = kw.lower()
            if kw_lower not in {w for w, _ in kw_weights}:
                kw_weights.append((kw_lower, 1))

        scored_sections: list[tuple[int, str]] = []  # (score, text)

        for f in files:
            if not f.body.strip():
                continue
            if not f.is_main:
                trigger = str(f.meta.get("trigger", ""))
                if trigger and user_intent:
                    trigger_kws = [kw.strip() for kw in trigger.split("|") if kw.strip()]
                    if not any(kw in intent_lower for kw in trigger_kws):
                        continue

            for heading, content in self._parse_sections(f.body):
                # 跳过硬性约束段落（已单独注入，避免重复）
                heading_text = heading.lstrip("#").strip()
                if heading_text.startswith("硬性约束"):
                    continue

                section_text = f"{heading}\n{content}" if heading else content
                section_lower = section_text.lower()

                # 计算加权匹配分数
                score = sum(
                    weight for kw, weight in kw_weights if kw in section_lower
                )
                if score > 0:
                    scored_sections.append((score, section_text))

        if not scored_sections:
            return ""

        # 按分数降序排列
        scored_sections.sort(key=lambda x: x[0], reverse=True)

        # 按分数从高到低填充，不超过 max_length
        result_parts: list[str] = []
        total_len = 0
        for _, section_text in scored_sections:
            if total_len + len(section_text) > max_length:
                remaining = max_length - total_len
                if remaining > 100:
                    result_parts.append(section_text[:remaining])
                break
            result_parts.append(section_text)
            total_len += len(section_text)

        return "\n\n".join(result_parts)

    @staticmethod
    def _parse_sections(body: str) -> list[tuple[str, str]]:
        """将 markdown body 按 ## 标题拆分为 (heading, content) 列表。

        标题前的裸文本用空字符串作 heading。
        只按 ## 拆分，### 及以下视为段落内容。
        """
        lines = body.split("\n")
        sections: list[tuple[str, str]] = []
        current_heading = ""
        current_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            # 精确匹配 ##：只有 2 个 # 才算段落边界
            if stripped.startswith("#"):
                n_hashes = len(stripped) - len(stripped.lstrip("#"))
                if n_hashes == 2:
                    content = "\n".join(current_lines).strip()
                    if content:
                        sections.append((current_heading, content))
                    current_heading = stripped
                    current_lines = []
                    continue
            current_lines.append(line)

        content = "\n".join(current_lines).strip()
        if content:
            sections.append((current_heading, content))

        return sections

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
                if heading == section_title or heading.startswith(section_title):
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

    def _extract_sections_by_keyword(self, body: str, parent_title: str, keyword: str) -> str:
        """从指定父章节中提取标题包含 keyword 的所有子章节。"""
        lines = body.split("\n")
        in_parent = False
        parent_level = 0
        results: list[str] = []
        current_sub: list[str] = []
        current_sub_level = 0
        in_matching_sub = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                level = len(stripped) - len(stripped.lstrip("#"))
                heading = stripped.lstrip("#").strip()

                if not in_parent:
                    # 还没进入父章节，等待匹配
                    if heading == parent_title:
                        in_parent = True
                        parent_level = level
                    continue

                # 已在父章节内
                if level <= parent_level:
                    # 遇到同级或更高级标题，父章节结束
                    if in_matching_sub and current_sub:
                        results.append("\n".join(current_sub))
                    break

                # 遇到子章节标题
                if in_matching_sub and current_sub:
                    results.append("\n".join(current_sub))

                if keyword in heading:
                    in_matching_sub = True
                    current_sub = [line]
                    current_sub_level = level
                else:
                    in_matching_sub = False
                    current_sub = []
            elif in_parent and in_matching_sub:
                current_sub.append(line)

        # 处理最后一个子章节
        if in_matching_sub and current_sub:
            results.append("\n".join(current_sub))

        return "\n\n".join(results).strip()
