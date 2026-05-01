from __future__ import annotations

from typing import TYPE_CHECKING

from testagent.common import get_logger

if TYPE_CHECKING:
    from testagent.models.skill import SkillDefinition

_logger = get_logger(__name__)


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[tuple[str, str], SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        key = (skill.name, skill.version)
        if key in self._skills:
            _logger.warning(
                "Skill with same name+version already registered, overwriting",
                extra={"extra_data": {"name": skill.name, "version": skill.version}},
            )
        self._skills[key] = skill
        _logger.info(
            "Skill registered",
            extra={"extra_data": {"name": skill.name, "version": skill.version}},
        )

    def get_by_name(self, name: str, version: str | None = None) -> SkillDefinition | None:
        if version is not None:
            return self._skills.get((name, version))

        matching = [s for (n, _), s in self._skills.items() if n == name]
        if not matching:
            return None

        matching.sort(key=lambda s: s.version, reverse=True)
        return matching[0]

    def unregister(self, name: str, version: str) -> None:
        self._skills.pop((name, version), None)

    def get_descriptions(self) -> str:
        if not self._skills:
            return ""

        lines: list[str] = []
        for (name, version), skill in self._skills.items():
            trigger = getattr(skill, "trigger_pattern", None) or ""
            trigger_str = f" [trigger: {trigger}]" if trigger else ""
            lines.append(f"Skill: {name} v{version} - {skill.description}{trigger_str}")

        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        skill = self.get_by_name(name)
        if skill is None:
            return ""
        body = getattr(skill, "body", None)
        return body if body else ""

    def match_by_trigger(self, text: str) -> list[SkillDefinition]:
        from testagent.skills.matcher import SkillMatcher

        matcher = SkillMatcher()
        return matcher.match_all(text, list(self._skills.values()))

    def count(self) -> int:
        return len(self._skills)

    def list_all(self) -> list[SkillDefinition]:
        return list(self._skills.values())
