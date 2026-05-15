from __future__ import annotations

from pathlib import Path

import pytest

from testagent.skills.parser import MarkdownParser
from testagent.skills.scaffold import ScaffoldResult, SkillScaffold


class TestSkillScaffold:
    """Test SkillScaffold generator."""

    def test_valid_templates(self) -> None:
        assert frozenset({"api_test", "web_test", "app_test", "empty"}) == SkillScaffold.VALID_TEMPLATES

    def test_generate_api_test(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="my_api_test", template="api_test", output_dir=tmp_path)

        assert isinstance(result, ScaffoldResult)
        assert result.skill_dir == tmp_path / "my_api_test"
        assert result.skill_md_path == tmp_path / "my_api_test" / "SKILL.md"
        assert result.readme_path == tmp_path / "my_api_test" / "README.md"
        assert len(result.generated_files) == 2

        assert result.skill_dir.exists()
        assert result.skill_md_path.exists()
        assert result.readme_path.exists()

    def test_generate_web_test(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="my_web_test", template="web_test", output_dir=tmp_path)

        assert result.skill_dir == tmp_path / "my_web_test"
        assert result.skill_md_path.exists()
        assert result.readme_path.exists()

        content = result.skill_md_path.read_text(encoding="utf-8")
        assert "playwright_server" in content
        assert "req_docs" in content
        assert "locator_library" in content

    def test_generate_app_test(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="my_app_test", template="app_test", output_dir=tmp_path)

        assert result.skill_dir == tmp_path / "my_app_test"
        assert result.skill_md_path.exists()
        assert result.readme_path.exists()

        content = result.skill_md_path.read_text(encoding="utf-8")
        assert "appium_server" in content
        assert "locator_library" in content

    def test_generate_empty(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="custom_skill", template="empty", output_dir=tmp_path)

        assert result.skill_dir == tmp_path / "custom_skill"
        assert result.skill_md_path.exists()
        assert result.readme_path.exists()

        content = result.skill_md_path.read_text(encoding="utf-8")
        assert "required_mcp_servers: []" in content
        assert "required_rag_collections: []" in content

    def test_generate_with_default_template(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="default_skill", template="api_test", output_dir=tmp_path)
        assert result.skill_dir == tmp_path / "default_skill"
        assert result.skill_md_path.exists()

    def test_generate_unknown_template(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        with pytest.raises(ValueError, match="Unknown template: invalid_tmpl"):
            scaffold.generate(name="bad_skill", template="invalid_tmpl", output_dir=tmp_path)

    def test_generate_existing_directory(self, tmp_path: Path) -> None:
        (tmp_path / "existing_skill").mkdir(parents=True)
        scaffold = SkillScaffold()
        result = scaffold.generate(name="existing_skill", template="empty", output_dir=tmp_path)
        assert result.skill_md_path.exists()
        assert result.readme_path.exists()

    def test_generated_skill_md_contains_required_frontmatter_fields(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="validated_skill", template="api_test", output_dir=tmp_path)
        content = result.skill_md_path.read_text(encoding="utf-8")

        assert content.startswith("---")
        assert "name: validated_skill" in content
        assert 'version: "1.0.0"' in content
        assert "description:" in content
        assert "trigger:" in content
        assert "required_mcp_servers:" in content
        assert "required_rag_collections:" in content

    def test_generated_skill_md_has_proper_body_sections(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="sections_test", template="api_test", output_dir=tmp_path)
        content = result.skill_md_path.read_text(encoding="utf-8")

        assert "## 目标" in content
        assert "## 操作流程" in content
        assert "## 断言策略" in content
        assert "## 失败处理" in content

    def test_generate_readme_contains_usage_instructions(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="usage_test", template="web_test", output_dir=tmp_path)
        content = result.readme_path.read_text(encoding="utf-8")

        assert "# usage_test" in content
        assert "## 使用方式" in content
        assert "testagent run --skill usage_test --env staging" in content
        assert "playwright_server" in content
        assert "req_docs" in content
        assert "locator_library" in content
        assert "SKILL.md" in content
        assert "README.md" in content

    def test_generate_empty_template_readme(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="empty_test", template="empty", output_dir=tmp_path)
        content = result.readme_path.read_text(encoding="utf-8")

        assert "*无*" in content
        assert "testagent run --skill empty_test --env staging" in content


class TestSkillScaffoldParsing:
    """Test that generated SKILL.md files are parseable by MarkdownParser."""

    def test_api_test_parses_correctly(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="parse_api", template="api_test", output_dir=tmp_path)

        content = result.skill_md_path.read_text(encoding="utf-8")
        parser = MarkdownParser()
        meta, body = parser.parse(content)

        assert meta["name"] == "parse_api"
        assert meta["version"] == "1.0.0"
        assert "description" in meta
        assert "trigger" in meta
        assert meta["required_mcp_servers"] == ["api_server", "database_server"]
        assert meta["required_rag_collections"] == ["api_docs", "defect_history"]
        assert "## 目标" in body
        assert "## 操作流程" in body

    def test_web_test_parses_correctly(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="parse_web", template="web_test", output_dir=tmp_path)

        content = result.skill_md_path.read_text(encoding="utf-8")
        parser = MarkdownParser()
        meta, body = parser.parse(content)

        assert meta["name"] == "parse_web"
        assert meta["required_mcp_servers"] == ["playwright_server"]
        assert meta["required_rag_collections"] == ["req_docs", "locator_library"]
        assert "## 目标" in body

    def test_app_test_parses_correctly(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="parse_app", template="app_test", output_dir=tmp_path)

        content = result.skill_md_path.read_text(encoding="utf-8")
        parser = MarkdownParser()
        meta, body = parser.parse(content)

        assert meta["name"] == "parse_app"
        assert meta["required_mcp_servers"] == ["appium_server"]
        assert meta["required_rag_collections"] == ["req_docs", "locator_library"]
        assert "## 目标" in body

    def test_empty_template_parses_correctly(self, tmp_path: Path) -> None:
        scaffold = SkillScaffold()
        result = scaffold.generate(name="parse_empty", template="empty", output_dir=tmp_path)

        content = result.skill_md_path.read_text(encoding="utf-8")
        parser = MarkdownParser()
        meta, body = parser.parse(content)

        assert meta["name"] == "parse_empty"
        assert meta["required_mcp_servers"] == []
        assert meta["required_rag_collections"] == []
        assert "## 目标" in body
        assert "在此填写" in body


class TestScaffoldResult:
    """Test ScaffoldResult dataclass."""

    def test_default_generated_files_empty(self) -> None:
        result = ScaffoldResult(
            skill_dir=Path("/tmp/skill"),
            skill_md_path=Path("/tmp/skill/SKILL.md"),
            readme_path=Path("/tmp/skill/README.md"),
        )
        assert result.generated_files == []

    def test_with_generated_files(self) -> None:
        result = ScaffoldResult(
            skill_dir=Path("/tmp/skill"),
            skill_md_path=Path("/tmp/skill/SKILL.md"),
            readme_path=Path("/tmp/skill/README.md"),
            generated_files=[Path("/tmp/skill/SKILL.md"), Path("/tmp/skill/README.md")],
        )
        assert len(result.generated_files) == 2


class TestSkillScaffoldDefaults:
    """Test SkillScaffold default description and trigger generation."""

    def test_default_description_api(self) -> None:
        scaffold = SkillScaffold()
        desc = scaffold._default_description("my_skill", "api_test")
        assert "API 测试" in desc

    def test_default_description_web(self) -> None:
        scaffold = SkillScaffold()
        desc = scaffold._default_description("my_skill", "web_test")
        assert "Web 页面测试" in desc

    def test_default_description_app(self) -> None:
        scaffold = SkillScaffold()
        desc = scaffold._default_description("my_skill", "app_test")
        assert "移动端" in desc

    def test_default_description_empty(self) -> None:
        scaffold = SkillScaffold()
        desc = scaffold._default_description("my_skill", "empty")
        assert "自定义测试技能" in desc

    def test_default_description_fallback(self) -> None:
        scaffold = SkillScaffold()
        desc = scaffold._default_description("my_skill", "unknown_template")
        assert "自定义测试技能" in desc

    def test_default_trigger(self) -> None:
        scaffold = SkillScaffold()
        trigger = scaffold._default_trigger("my_skill")
        assert trigger == "my_skill.*test|test.*my_skill|my_skill"
