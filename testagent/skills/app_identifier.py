from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from testagent.common import get_logger

_logger = get_logger(__name__)

# 置信度阈值
HIGH_CONFIDENCE_THRESHOLD = 0.9
MEDIUM_CONFIDENCE_THRESHOLD = 0.5


@dataclass
class IdentificationResult:
    app_name: str
    confidence: float
    action: Literal["auto", "confirm", "ask"]
    candidates: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class _TriggerEntry:
    app_name: str
    trigger: str
    is_regex: bool
    weight: float


class AppIdentifier:
    """识别用户输入中的目标 App，返回置信度评分和建议动作。"""

    def __init__(self, skills_dir: Path | str) -> None:
        self._skills_dir = Path(skills_dir)
        self._triggers: list[_TriggerEntry] = []
        self._load_triggers()

    def _load_triggers(self) -> None:
        """从所有 App Skill 的 SKILL.md 中加载触发词。"""
        apps_dir = self._skills_dir / "apps"
        if not apps_dir.exists():
            _logger.debug(
                "Apps skills directory not found, no app triggers loaded",
                extra={"extra_data": {"dir": str(apps_dir)}},
            )
            return

        for skill_dir in sorted(apps_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            self._load_skill_triggers(skill_file, skill_dir.name)

    def _load_skill_triggers(self, skill_file: Path, app_name: str) -> None:
        """从单个 SKILL.md 中提取触发词。"""
        try:
            content = skill_file.read_text(encoding="utf-8")
            if not content.startswith("---"):
                return
            parts = content.split("---", 2)
            if len(parts) < 3:
                return
            meta = yaml.safe_load(parts[1])
            if not isinstance(meta, dict):
                return

            # 加载 triggers 列表
            triggers = meta.get("triggers", [])
            if isinstance(triggers, list):
                for trigger in triggers:
                    if isinstance(trigger, str):
                        if trigger.startswith("regex:"):
                            pattern = trigger[6:].strip()
                            self._triggers.append(
                                _TriggerEntry(
                                    app_name=app_name,
                                    trigger=pattern,
                                    is_regex=True,
                                    weight=0.85,
                                )
                            )
                        else:
                            self._triggers.append(
                                _TriggerEntry(
                                    app_name=app_name,
                                    trigger=trigger.lower(),
                                    is_regex=False,
                                    weight=0.95,
                                )
                            )

            # 包名作为高权重触发词
            app_info = meta.get("app_info", {})
            if isinstance(app_info, dict):
                package_name = app_info.get("package_name", "")
                if package_name:
                    self._triggers.append(
                        _TriggerEntry(
                            app_name=app_name,
                            trigger=str(package_name).lower(),
                            is_regex=False,
                            weight=0.99,
                        )
                    )

        except Exception as exc:
            _logger.warning(
                "Failed to load triggers from App Skill",
                extra={"extra_data": {"file": str(skill_file), "error": str(exc)}},
            )

    def identify(self, user_input: str) -> list[tuple[str, float]]:
        """识别用户输入中的目标 App。

        Returns:
            按置信度降序排列的 [(app_name, confidence), ...] 列表。
        """
        candidates: dict[str, float] = {}
        input_lower = user_input.lower()

        for entry in self._triggers:
            matched = False
            if entry.is_regex:
                try:
                    if re.search(entry.trigger, input_lower, re.IGNORECASE):
                        matched = True
                except re.error:
                    _logger.warning(
                        "Invalid regex trigger pattern",
                        extra={"extra_data": {"pattern": entry.trigger}},
                    )
            else:
                if entry.trigger in input_lower:
                    matched = True

            if matched:
                current = candidates.get(entry.app_name, 0.0)
                candidates[entry.app_name] = max(current, entry.weight)

        result = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
        return result

    def identify_with_action(self, user_input: str) -> IdentificationResult:
        """识别并决定后续动作（auto/confirm/ask）。"""
        candidates = self.identify(user_input)

        if not candidates:
            return IdentificationResult(
                app_name="",
                confidence=0.0,
                action="ask",
                candidates=[],
            )

        top_app, top_confidence = candidates[0]

        if top_confidence >= HIGH_CONFIDENCE_THRESHOLD:
            return IdentificationResult(
                app_name=top_app,
                confidence=top_confidence,
                action="auto",
                candidates=candidates,
            )
        if top_confidence >= MEDIUM_CONFIDENCE_THRESHOLD:
            return IdentificationResult(
                app_name=top_app,
                confidence=top_confidence,
                action="confirm",
                candidates=candidates[:3],
            )
        return IdentificationResult(
            app_name="",
            confidence=top_confidence,
            action="ask",
            candidates=candidates[:3],
        )

    def list_apps(self) -> list[str]:
        """列出所有已注册的 App 名称。"""
        return list({entry.app_name for entry in self._triggers})
