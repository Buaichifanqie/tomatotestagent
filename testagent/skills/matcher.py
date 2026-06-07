from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from testagent.common import get_logger

if TYPE_CHECKING:
    from testagent.models.skill import SkillDefinition
    from testagent.skills.app_identifier import AppIdentifier

_logger = get_logger(__name__)

_PATTERN_MATCH_WEIGHT = 10.0
_KEYWORD_MATCH_WEIGHT = 1.0


@dataclass
class _ScoredMatch:
    skill: SkillDefinition
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class AppAwareMatch:
    """包含 App 识别信息的匹配结果。"""
    skill: SkillDefinition
    app_name: str = ""
    app_confidence: float = 0.0
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


class SkillMatcher:
    def match(self, text: str, skills: list[SkillDefinition]) -> SkillDefinition | None:
        matches = self.match_all(text, skills)
        return matches[0] if matches else None

    def match_all(self, text: str, skills: list[SkillDefinition]) -> list[SkillDefinition]:
        if not skills:
            return []

        text_lower = text.lower()
        text_words = set(text_lower.split())

        scored: list[_ScoredMatch] = []

        for skill in skills:
            sm = _ScoredMatch(skill=skill)

            trigger = getattr(skill, "trigger_pattern", None)
            if trigger and isinstance(trigger, str) and trigger.strip():
                try:
                    pattern = re.compile(trigger, re.IGNORECASE)
                    if pattern.search(text):
                        sm.score += _PATTERN_MATCH_WEIGHT
                        sm.reasons.append(f"trigger_pattern '{trigger}' matched")
                except re.error:
                    _logger.warning(
                        "Invalid trigger pattern in Skill, skipping pattern match",
                        extra={"extra_data": {"skill": skill.name, "pattern": trigger}},
                    )

            description = skill.description.lower()
            desc_words = set(description.split())
            overlap = text_words & desc_words
            if overlap:
                sm.score += _KEYWORD_MATCH_WEIGHT * len(overlap)
                sm.reasons.append(f"keyword overlap: {sorted(overlap)}")

            if sm.score > 0:
                scored.append(sm)

        scored.sort(key=lambda x: x.score, reverse=True)

        _logger.debug(
            "Skill matching results",
            extra={
                "extra_data": {
                    "input_text": text[:100],
                    "matches": [{"skill": s.skill.name, "score": s.score, "reasons": s.reasons} for s in scored],
                }
            },
        )

        return [s.skill for s in scored]

    def match_with_app(
        self,
        text: str,
        skills: list[SkillDefinition],
        app_identifier: AppIdentifier,
    ) -> AppAwareMatch | None:
        """匹配 Skill 并同时识别目标 App。"""
        # 先匹配通用 Skill
        generic_match = self.match(text, skills)
        if generic_match is None:
            return None

        # 再识别 App
        id_result = app_identifier.identify_with_action(text)

        return AppAwareMatch(
            skill=generic_match,
            app_name=id_result.app_name,
            app_confidence=id_result.confidence,
            score=0.0,
            reasons=[],
        )
