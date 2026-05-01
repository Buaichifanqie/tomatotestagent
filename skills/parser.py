from __future__ import annotations

import re

import yaml

from testagent.common import get_logger
from testagent.common.errors import SkillParseError

_logger = get_logger(__name__)


class MarkdownParser:
    FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n(.*)", re.DOTALL)

    def parse(self, content: str) -> tuple[dict[str, object], str]:
        if content.startswith("---\n---\n"):
            raise SkillParseError(
                message="Front Matter is empty, must contain YAML mapping",
                code="SKILL_PARSE_EMPTY_FRONTMATTER",
            )

        match = self.FRONTMATTER_PATTERN.match(content)
        if not match:
            raise SkillParseError(
                message="Failed to parse YAML Front Matter. Expected '---\\n...\\n---\\n...' format",
                code="SKILL_PARSE_NO_FRONTMATTER",
            )

        yaml_str = match.group(1)
        body = match.group(2).strip()

        try:
            meta = yaml.safe_load(yaml_str)
        except yaml.YAMLError as exc:
            raise SkillParseError(
                message=f"Invalid YAML in Front Matter: {exc}",
                code="SKILL_PARSE_INVALID_YAML",
                details={"error": str(exc)},
            ) from exc

        if meta is None:
            raise SkillParseError(
                message="Front Matter is empty, must contain YAML mapping",
                code="SKILL_PARSE_EMPTY_FRONTMATTER",
            )

        if not isinstance(meta, dict):
            raise SkillParseError(
                message=f"Front Matter must be a YAML mapping, got {type(meta).__name__}",
                code="SKILL_PARSE_NOT_MAPPING",
            )

        return meta, body
