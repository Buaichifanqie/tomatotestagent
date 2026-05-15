from __future__ import annotations

from pathlib import Path

import pytest

from testagent.common.errors import SkillParseError
from testagent.gateway.mcp_registry import MCPRegistry, MCPServerInfo
from testagent.models.skill import SkillDefinition
from testagent.skills import (
    MarkdownParser,
    SkillLoader,
    SkillMatcher,
    SkillRegistry,
    SkillValidator,
    ValidationResult,
)
from testagent.skills.loader import RawSkill


def _make_skill_meta(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "name": "test_skill",
        "version": "1.0.0",
        "description": "A test skill for unit testing",
        "trigger": r"test.*skill",
        "required_mcp_servers": [],
        "required_rag_collections": [],
    }
    defaults.update(overrides)
    return defaults


def _make_skill_definition(
    name: str = "test_skill",
    version: str = "1.0.0",
    description: str = "A test skill",
    trigger_pattern: str | None = r"test.*skill",
    required_mcp_servers: list[str] | None = None,
    required_rag_collections: list[str] | None = None,
    body: str | None = "## Test Body\n\nTest content.",
) -> SkillDefinition:
    return SkillDefinition(
        name=name,
        version=version,
        description=description,
        trigger_pattern=trigger_pattern,
        required_mcp_servers=required_mcp_servers or [],
        required_rag_collections=required_rag_collections or [],
        body=body,
    )


VALID_SKILL_MD = """---
name: api_smoke_test
version: "1.0.0"
description: API冒烟测试
trigger: "api.*smoke"
required_mcp_servers:
  - api_server
required_rag_collections:
  - api_docs
---

## 目标

验证API基本可用性。

## 操作流程

1. 发送GET请求
2. 验证响应

## 断言策略

状态码200

## 失败处理

重试一次
"""


class TestMarkdownParser:
    def test_parse_valid_frontmatter_and_body(self) -> None:
        parser = MarkdownParser()
        meta, body = parser.parse(VALID_SKILL_MD)

        assert meta["name"] == "api_smoke_test"
        assert meta["version"] == "1.0.0"
        assert meta["description"] == "API冒烟测试"
        assert meta["trigger"] == "api.*smoke"
        assert meta["required_mcp_servers"] == ["api_server"]
        assert meta["required_rag_collections"] == ["api_docs"]
        assert "## 目标" in body
        assert "## 操作流程" in body
        assert "## 断言策略" in body
        assert "## 失败处理" in body

    def test_parse_no_frontmatter_raises_error(self) -> None:
        parser = MarkdownParser()
        with pytest.raises(SkillParseError) as exc_info:
            parser.parse("Just plain markdown without front matter")
        assert "SKILL_PARSE_NO_FRONTMATTER" in str(exc_info.value)

    def test_parse_invalid_yaml_raises_error(self) -> None:
        parser = MarkdownParser()
        content = "---\ninvalid: [unclosed\n---\nBody"
        with pytest.raises(SkillParseError) as exc_info:
            parser.parse(content)
        assert "SKILL_PARSE_INVALID_YAML" in str(exc_info.value)

    def test_parse_empty_frontmatter_raises_error(self) -> None:
        parser = MarkdownParser()
        content = "---\n---\nBody"
        with pytest.raises(SkillParseError) as exc_info:
            parser.parse(content)
        assert "SKILL_PARSE_EMPTY_FRONTMATTER" in str(exc_info.value)

    def test_parse_non_mapping_frontmatter_raises_error(self) -> None:
        parser = MarkdownParser()
        content = "---\n- list_item_1\n- list_item_2\n---\nBody"
        with pytest.raises(SkillParseError) as exc_info:
            parser.parse(content)
        assert "SKILL_PARSE_NOT_MAPPING" in str(exc_info.value)

    def test_parse_no_closing_delimiter_raises_error(self) -> None:
        parser = MarkdownParser()
        content = "---\nname: test\nversion: '1.0'\n"
        with pytest.raises(SkillParseError) as exc_info:
            parser.parse(content)
        assert "SKILL_PARSE_NO_FRONTMATTER" in str(exc_info.value)

    def test_parse_body_preserves_markdown_formatting(self) -> None:
        parser = MarkdownParser()
        fm = (
            "---\nname: test\nversion: '1.0'\ndescription: desc\n"
            "trigger: t\nrequired_mcp_servers: []\n"
            "required_rag_collections: []\n---\n"
        )
        content = fm + "# Heading\n\n**bold** and *italic*\n\n```python\nprint('hello')\n```\n"
        _meta, body = parser.parse(content)
        assert "# Heading" in body
        assert "**bold**" in body
        assert "```python" in body
        assert "print('hello')" in body

    def test_parse_large_body(self) -> None:
        parser = MarkdownParser()
        large_body = "\n".join(f"Line {i} of test content" for i in range(1000))
        fm = (
            "---\nname: large\nversion: '1.0'\n"
            "description: d\ntrigger: t\n"
            "required_mcp_servers: []\nrequired_rag_collections: []\n---\n"
        )
        content = fm + large_body
        _meta, body = parser.parse(content)
        assert body == large_body


class TestSkillLoader:
    def _write_skill_file(self, dir_path: Path, subdir: str, content: str) -> Path:
        skill_dir = dir_path / subdir
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content, encoding="utf-8")
        return skill_file

    def test_scan_finds_all_skill_files(self, tmp_path: Path) -> None:
        self._write_skill_file(tmp_path, "api_test", VALID_SKILL_MD)
        self._write_skill_file(tmp_path, "web_test", VALID_SKILL_MD)
        self._write_skill_file(tmp_path, "other", VALID_SKILL_MD)

        loader = SkillLoader(tmp_path)
        found = loader.scan()

        assert len(found) == 3
        assert all(p.name == "SKILL.md" for p in found)

    def test_scan_empty_directory(self, tmp_path: Path) -> None:
        loader = SkillLoader(tmp_path)
        found = loader.scan()
        assert found == []

    def test_scan_directory_not_exists(self, tmp_path: Path) -> None:
        loader = SkillLoader(Path("/nonexistent/path/12345"))
        found = loader.scan()
        assert found == []

    def test_scan_only_matches_skill_md(self, tmp_path: Path) -> None:
        self._write_skill_file(tmp_path, "api_test", VALID_SKILL_MD)
        (tmp_path / "README.md").write_text("# README", encoding="utf-8")
        (tmp_path / "other.txt").write_text("text", encoding="utf-8")

        loader = SkillLoader(tmp_path)
        found = loader.scan()

        assert len(found) == 1

    def test_load_parses_valid_skill(self, tmp_path: Path) -> None:
        skill_file = self._write_skill_file(tmp_path, "api_test", VALID_SKILL_MD)
        loader = SkillLoader(tmp_path)
        raw = loader.load(skill_file)

        assert isinstance(raw, RawSkill)
        assert raw.name == "api_smoke_test"
        assert raw.version == "1.0.0"
        assert raw.file_path == skill_file
        assert "api.*smoke" in str(raw.meta)
        assert "## 目标" in raw.body

    def test_load_all_skips_invalid_files(self, tmp_path: Path) -> None:
        self._write_skill_file(tmp_path, "good", VALID_SKILL_MD)
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_text("No front matter here", encoding="utf-8")

        loader = SkillLoader(tmp_path)
        results = loader.load_all()

        assert len(results) == 1
        assert results[0].name == "api_smoke_test"

    def test_load_all_returns_empty_on_no_files(self, tmp_path: Path) -> None:
        loader = SkillLoader(tmp_path)
        results = loader.load_all()
        assert results == []


class TestValidationResult:
    def test_default_values(self) -> None:
        result = ValidationResult(valid=True)
        assert result.valid is True
        assert result.errors == []
        assert result.warnings == []
        assert result.degraded is False

    def test_with_errors(self) -> None:
        result = ValidationResult(valid=False, errors=["Missing name"], warnings=[], degraded=False)
        assert result.valid is False
        assert len(result.errors) == 1

    def test_with_degraded(self) -> None:
        result = ValidationResult(
            valid=True,
            errors=[],
            warnings=["MCP server not found"],
            degraded=True,
        )
        assert result.degraded is True
        assert len(result.warnings) == 1


class TestSkillValidator:
    def test_validate_all_required_fields_present(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta()
        result = validator.validate(meta)
        assert result.valid is True
        assert result.errors == []
        assert result.degraded is False

    def test_validate_missing_name(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta()
        del meta["name"]
        result = validator.validate(meta)
        assert result.valid is False
        assert any("name" in e for e in result.errors)

    def test_validate_missing_version(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta()
        del meta["version"]
        result = validator.validate(meta)
        assert result.valid is False
        assert any("version" in e for e in result.errors)

    def test_validate_missing_description(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta()
        del meta["description"]
        result = validator.validate(meta)
        assert result.valid is False
        assert any("description" in e for e in result.errors)

    def test_validate_missing_trigger(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta()
        del meta["trigger"]
        result = validator.validate(meta)
        assert result.valid is False
        assert any("trigger" in e for e in result.errors)

    def test_validate_missing_required_mcp_servers(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta()
        del meta["required_mcp_servers"]
        result = validator.validate(meta)
        assert result.valid is False
        assert any("required_mcp_servers" in e for e in result.errors)

    def test_validate_missing_required_rag_collections(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta()
        del meta["required_rag_collections"]
        result = validator.validate(meta)
        assert result.valid is False
        assert any("required_rag_collections" in e for e in result.errors)

    def test_validate_empty_trigger_string(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta(trigger="   ")
        result = validator.validate(meta)
        assert result.valid is False
        assert any("trigger" in e for e in result.errors)

    def test_validate_trigger_not_a_string(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta(trigger=123)
        result = validator.validate(meta)
        assert result.valid is False
        assert any("trigger" in e for e in result.errors)

    def test_validate_invalid_trigger_regex(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta(trigger="[unclosed")
        result = validator.validate(meta)
        assert result.valid is False
        assert any("Invalid trigger pattern" in e for e in result.errors)

    def test_validate_mcp_servers_not_a_list(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta(required_mcp_servers="not_a_list")
        result = validator.validate(meta)
        assert result.valid is False
        assert any("required_mcp_servers" in e for e in result.errors)

    def test_validate_rag_collections_not_a_list(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta(required_rag_collections="not_a_list")
        result = validator.validate(meta)
        assert result.valid is False
        assert any("required_rag_collections" in e for e in result.errors)

    def test_validate_null_field_values(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta(name=None)
        result = validator.validate(meta)
        assert result.valid is False
        assert any("name" in e for e in result.errors)

    def test_validate_mcp_server_registered_not_degraded(self) -> None:
        from testagent.config.settings import TestAgentSettings

        settings = TestAgentSettings()
        registry = MCPRegistry(settings)
        registry._servers["api_server"] = MCPServerInfo(name="api_server", command="echo", status="healthy")

        validator = SkillValidator(mcp_registry=registry)
        meta = _make_skill_meta(required_mcp_servers=["api_server"])
        result = validator.validate(meta)
        assert result.valid is True
        assert result.degraded is False

    def test_validate_mcp_server_not_registered_marks_degraded(self) -> None:
        from testagent.config.settings import TestAgentSettings

        settings = TestAgentSettings()
        registry = MCPRegistry(settings)

        validator = SkillValidator(mcp_registry=registry)
        meta = _make_skill_meta(required_mcp_servers=["unknown_server"])
        result = validator.validate(meta)
        assert result.valid is True
        assert result.degraded is True
        assert len(result.warnings) == 1
        assert "unknown_server" in result.warnings[0]

    def test_validate_mcp_servers_empty_list_not_degraded(self) -> None:
        from testagent.config.settings import TestAgentSettings

        settings = TestAgentSettings()
        registry = MCPRegistry(settings)

        validator = SkillValidator(mcp_registry=registry)
        meta = _make_skill_meta(required_mcp_servers=[])
        result = validator.validate(meta)
        assert result.valid is True
        assert result.degraded is False

    def test_validate_multiple_errors_returned_at_once(self) -> None:
        validator = SkillValidator()
        meta: dict[str, object] = {}
        result = validator.validate(meta)
        assert result.valid is False
        assert len(result.errors) == 6

    def test_validate_no_registry_skips_mcp_check(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta(required_mcp_servers=["any_server_will_pass"])
        result = validator.validate(meta)
        assert result.valid is True
        assert result.degraded is False

    def test_validate_trigger_valid_regex(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta(trigger=r"api\.(smoke|regression)")
        result = validator.validate(meta)
        assert result.valid is True

    def test_validate_version_semver_accepted(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta(version="2.0.0-alpha.1")
        result = validator.validate(meta)
        assert result.valid is True

    def test_validate_description_long_accepted(self) -> None:
        validator = SkillValidator()
        meta = _make_skill_meta(description="A" * 500)
        result = validator.validate(meta)
        assert result.valid is True


class TestSkillRegistry:
    def test_register_skill(self) -> None:
        registry = SkillRegistry()
        skill = _make_skill_definition()
        registry.register(skill)
        assert registry.count() == 1

    def test_register_duplicate_name_version_overwrites(self) -> None:
        registry = SkillRegistry()
        skill1 = _make_skill_definition(name="dup", version="1.0", description="First")
        skill2 = _make_skill_definition(name="dup", version="1.0", description="Second")
        registry.register(skill1)
        registry.register(skill2)
        assert registry.count() == 1
        retrieved = registry.get_by_name("dup", version="1.0")
        assert retrieved is not None
        assert retrieved.description == "Second"

    def test_register_same_name_different_version(self) -> None:
        registry = SkillRegistry()
        skill1 = _make_skill_definition(name="test", version="1.0.0")
        skill2 = _make_skill_definition(name="test", version="2.0.0")
        registry.register(skill1)
        registry.register(skill2)
        assert registry.count() == 2

    def test_get_by_name_without_version_returns_latest(self) -> None:
        registry = SkillRegistry()
        skill1 = _make_skill_definition(name="test", version="1.0.0")
        skill2 = _make_skill_definition(name="test", version="2.0.0")
        skill3 = _make_skill_definition(name="test", version="1.5.0")
        registry.register(skill1)
        registry.register(skill2)
        registry.register(skill3)

        result = registry.get_by_name("test")
        assert result is not None
        assert result.version == "2.0.0"

    def test_get_by_name_with_version_exact_match(self) -> None:
        registry = SkillRegistry()
        skill1 = _make_skill_definition(name="test", version="1.0.0")
        skill2 = _make_skill_definition(name="test", version="2.0.0")
        registry.register(skill1)
        registry.register(skill2)

        result = registry.get_by_name("test", version="1.0.0")
        assert result is not None
        assert result.version == "1.0.0"

    def test_get_by_name_not_found_returns_none(self) -> None:
        registry = SkillRegistry()
        result = registry.get_by_name("nonexistent")
        assert result is None

    def test_get_by_name_version_not_found_returns_none(self) -> None:
        registry = SkillRegistry()
        skill = _make_skill_definition(name="test", version="1.0.0")
        registry.register(skill)
        result = registry.get_by_name("test", version="99.0.0")
        assert result is None

    def test_unregister_removes_skill(self) -> None:
        registry = SkillRegistry()
        skill = _make_skill_definition(name="test", version="1.0.0")
        registry.register(skill)
        assert registry.count() == 1
        registry.unregister("test", "1.0.0")
        assert registry.count() == 0

    def test_unregister_nonexistent_skill_no_error(self) -> None:
        registry = SkillRegistry()
        registry.unregister("nonexistent", "1.0.0")
        assert registry.count() == 0

    def test_get_descriptions_format(self) -> None:
        registry = SkillRegistry()
        skill1 = _make_skill_definition(
            name="api_test",
            version="1.0",
            description="API test skill",
            trigger_pattern=r"api.*test",
        )
        skill2 = _make_skill_definition(
            name="web_test",
            version="1.0",
            description="Web test skill",
            trigger_pattern=None,
        )
        registry.register(skill1)
        registry.register(skill2)

        desc = registry.get_descriptions()

        assert "Skill: api_test" in desc
        assert "v1.0" in desc
        assert "API test skill" in desc
        assert "[trigger: api.*test]" in desc
        assert "Skill: web_test" in desc
        assert "Web test skill" in desc

    def test_get_descriptions_empty_registry(self) -> None:
        registry = SkillRegistry()
        desc = registry.get_descriptions()
        assert desc == ""

    def test_get_content_returns_body(self) -> None:
        registry = SkillRegistry()
        skill = _make_skill_definition(name="test", version="1.0", body="Full skill body content")
        registry.register(skill)

        content = registry.get_content("test")
        assert content == "Full skill body content"

    def test_get_content_not_found_returns_empty(self) -> None:
        registry = SkillRegistry()
        content = registry.get_content("nonexistent")
        assert content == ""

    def test_get_content_skill_without_body(self) -> None:
        registry = SkillRegistry()
        skill = _make_skill_definition(name="test", version="1.0", body=None)
        registry.register(skill)

        content = registry.get_content("test")
        assert content == ""

    def test_list_all_returns_all_skills(self) -> None:
        registry = SkillRegistry()
        skill1 = _make_skill_definition(name="a", version="1.0")
        skill2 = _make_skill_definition(name="b", version="1.0")
        registry.register(skill1)
        registry.register(skill2)

        all_skills = registry.list_all()
        assert len(all_skills) == 2

    def test_match_by_trigger_delegates_to_matcher(self) -> None:
        registry = SkillRegistry()
        skill1 = _make_skill_definition(
            name="api_test",
            version="1.0",
            trigger_pattern=r"api.*smoke",
            description="API smoke test",
        )
        skill2 = _make_skill_definition(
            name="web_test",
            version="1.0",
            trigger_pattern=r"web.*smoke",
            description="Web smoke test",
        )
        registry.register(skill1)
        registry.register(skill2)

        results = registry.match_by_trigger("run api smoke test")
        assert len(results) > 0
        assert results[0].name == "api_test"


class TestSkillMatcher:
    def test_match_by_trigger_pattern(self) -> None:
        matcher = SkillMatcher()
        skills = [
            _make_skill_definition(name="api_test", trigger_pattern=r"api.*smoke", description="API"),
            _make_skill_definition(name="web_test", trigger_pattern=r"web.*smoke", description="Web"),
        ]

        result = matcher.match("run api smoke test", skills)
        assert result is not None
        assert result.name == "api_test"

    def test_match_by_keyword_overlap(self) -> None:
        matcher = SkillMatcher()
        skills = [
            _make_skill_definition(
                name="api_test",
                trigger_pattern=None,
                description="API smoke test for endpoints",
            ),
            _make_skill_definition(
                name="web_test",
                trigger_pattern=None,
                description="Web visual regression",
            ),
        ]

        result = matcher.match("I need to run a smoke test", skills)
        assert result is not None
        assert result.name == "api_test"

    def test_no_match_returns_none(self) -> None:
        matcher = SkillMatcher()
        skills = [
            _make_skill_definition(
                name="api_test",
                trigger_pattern=r"^api\b",
                description="endpoint health verification",
            ),
        ]

        result = matcher.match("web page validation", skills)
        assert result is None

    def test_match_returns_highest_score(self) -> None:
        matcher = SkillMatcher()
        skills = [
            _make_skill_definition(
                name="low_match",
                trigger_pattern=None,
                description="some general testing tool",
            ),
            _make_skill_definition(
                name="high_match",
                trigger_pattern=r"smoke.*test",
                description="smoke test for APIs",
            ),
        ]

        result = matcher.match("run smoke test on the API", skills)
        assert result is not None
        assert result.name == "high_match"

    def test_match_all_returns_sorted_by_score(self) -> None:
        matcher = SkillMatcher()
        skills = [
            _make_skill_definition(
                name="third",
                trigger_pattern=None,
                description="smoke check",
            ),
            _make_skill_definition(
                name="first",
                trigger_pattern=r"api.*smoke",
                description="API smoke test for endpoints",
            ),
            _make_skill_definition(
                name="second",
                trigger_pattern=None,
                description="smoke test API endpoints",
            ),
        ]

        results = matcher.match_all("run api smoke test", skills)
        assert len(results) > 0
        assert results[0].name == "first"

    def test_match_all_empty_skills_returns_empty(self) -> None:
        matcher = SkillMatcher()
        results = matcher.match_all("some text", [])
        assert results == []

    def test_match_empty_skills_returns_none(self) -> None:
        matcher = SkillMatcher()
        result = matcher.match("some text", [])
        assert result is None

    def test_invalid_trigger_pattern_is_skipped(self) -> None:
        matcher = SkillMatcher()
        skills = [
            _make_skill_definition(
                name="bad_regex",
                trigger_pattern="[invalid",
                description="should be skipped",
            ),
            _make_skill_definition(
                name="good",
                trigger_pattern=None,
                description="smoke test",
            ),
        ]

        result = matcher.match("run smoke test", skills)
        assert result is not None
        assert result.name == "good"

    def test_match_case_insensitive_trigger(self) -> None:
        matcher = SkillMatcher()
        skills = [
            _make_skill_definition(
                name="api_test",
                trigger_pattern=r"API.*SMOKE",
                description="test",
            ),
        ]

        result = matcher.match("run api smoke test", skills)
        assert result is not None
        assert result.name == "api_test"

    def test_match_case_insensitive_keyword(self) -> None:
        matcher = SkillMatcher()
        skills = [
            _make_skill_definition(
                name="api_test",
                trigger_pattern=None,
                description="API SMOKE TEST",
            ),
        ]

        result = matcher.match("run api smoke test", skills)
        assert result is not None
        assert result.name == "api_test"

    def test_match_both_pattern_and_keyword_scores_sum(self) -> None:
        matcher = SkillMatcher()
        skills = [
            _make_skill_definition(
                name="pattern_only",
                trigger_pattern=r"smoke",
                description="unrelated words here",
            ),
            _make_skill_definition(
                name="pattern_and_keyword",
                trigger_pattern=r"smoke",
                description="smoke testing framework",
            ),
        ]

        result = matcher.match("smoke test", skills)
        assert result is not None
        assert result.name == "pattern_and_keyword"
