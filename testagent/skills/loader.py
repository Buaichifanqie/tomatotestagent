from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from testagent.common import get_logger
from testagent.skills.parser import MarkdownParser

if TYPE_CHECKING:
    from pathlib import Path

_logger = get_logger(__name__)

SKILL_FILE_GLOB = "*/SKILL.md"


@dataclass
class RawSkill:
    name: str
    version: str
    file_path: Path
    meta: dict[str, object]
    body: str
    parse_errors: list[str] = field(default_factory=list)


class SkillLoader:
    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir
        self._parser = MarkdownParser()

    def scan(self) -> list[Path]:
        if not self._skills_dir.exists():
            _logger.warning(
                "Skills directory not found",
                extra={"extra_data": {"dir": str(self._skills_dir)}},
            )
            return []
        return sorted(self._skills_dir.glob(SKILL_FILE_GLOB))

    def load(self, path: Path) -> RawSkill:
        content = path.read_text(encoding="utf-8")
        meta, body = self._parser.parse(content)
        return RawSkill(
            name=str(meta.get("name", "")),
            version=str(meta.get("version", "")),
            file_path=path,
            meta=meta,
            body=body,
        )

    def load_all(self) -> list[RawSkill]:
        raw_skills: list[RawSkill] = []
        for path in self.scan():
            try:
                raw = self.load(path)
                raw_skills.append(raw)
            except Exception as exc:
                _logger.warning(
                    "Failed to parse Skill file, skipping",
                    extra={"extra_data": {"path": str(path), "error": str(exc)}},
                )
        return raw_skills
