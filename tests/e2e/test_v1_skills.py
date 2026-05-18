from __future__ import annotations

import shutil
from pathlib import Path
from typing import cast

import pytest

from testagent.models.skill import SkillDefinition
from testagent.skills.executor import SkillExecutor, SkillResult
from testagent.skills.loader import RawSkill, SkillLoader
from testagent.skills.registry import SkillRegistry
from testagent.skills.scaffold import SkillScaffold
from testagent.skills.validator import SkillValidator

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio,
]

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
V1_SKILL_NAMES = {"api_regression_test", "web_visual_test", "full_regression_test", "app_smoke_test"}


def _load_raw_skill(name: str) -> RawSkill:
    loader = SkillLoader(SKILLS_DIR)
    raw_skills = loader.load_all()
    skill = next((s for s in raw_skills if s.name == name), None)
    assert skill is not None, f"Skill '{name}' not loaded by SkillLoader"
    return skill


def _raw_skill_to_definition(raw: RawSkill) -> SkillDefinition:
    return SkillDefinition(
        name=raw.name,
        version=raw.version,
        description=raw.meta.get("description", ""),
        trigger_pattern=raw.meta.get("trigger", ""),
        required_mcp_servers=raw.meta.get("required_mcp_servers", []),
        required_rag_collections=raw.meta.get("required_rag_collections", []),
        body=raw.body,
    )


async def test_skill_scaffold_generate() -> None:
    """验证 skill create 命令生成正确文件结构。

    Steps:
      1. 使用 SkillScaffold.generate 创建 e2e_checkout web_test 脚手架
      2. 验证返回 ScaffoldResult 包含 skill_dir, skill_md_path, readme_path
      3. 验证 SKILL.md 文件存在且可被 SkillLoader 加载解析
      4. 验证 SKILL.md 包含所有必须的 Front Matter 字段
      5. 验证 Markdown Body 包含目标、操作流程、断言策略、失败处理
      6. 验证 README.md 存在且非空
      7. 清理生成的文件
    """
    scaffold = SkillScaffold()
    tmp_dir = SKILLS_DIR.parent / "tmp_test_scaffold"
    try:
        result = scaffold.generate(
            name="e2e_checkout",
            template="web_test",
            output_dir=tmp_dir,
        )

        assert result.skill_dir == tmp_dir / "e2e_checkout"
        assert result.skill_md_path == tmp_dir / "e2e_checkout" / "SKILL.md"
        assert result.readme_path == tmp_dir / "e2e_checkout" / "README.md"
        assert result.skill_md_path in result.generated_files
        assert result.readme_path in result.generated_files

        assert result.skill_md_path.exists(), "SKILL.md 未生成"
        assert result.readme_path.exists(), "README.md 未生成"

        loader = SkillLoader(tmp_dir)
        raw_skills = loader.load_all()
        checkout = next((s for s in raw_skills if s.name == "e2e_checkout"), None)
        assert checkout is not None, "e2e_checkout 未被 SkillLoader 加载"

        assert checkout.name == "e2e_checkout"
        assert checkout.version == "1.0.0"
        assert checkout.meta.get("description") is not None
        assert checkout.meta.get("trigger") is not None
        assert checkout.meta.get("required_mcp_servers") == ["playwright_server"]
        assert checkout.meta.get("required_rag_collections") == ["req_docs", "locator_library"]

        body = checkout.body
        assert "## 目标" in body
        assert "## 操作流程" in body
        assert "## 断言策略" in body
        assert "## 失败处理" in body

        validator = SkillValidator()
        validation = validator.validate(checkout.meta)
        assert validation.valid, f"脚手架 SKILL.md 校验失败: {validation.errors}"

        readme_content = result.readme_path.read_text(encoding="utf-8")
        assert len(readme_content.strip()) > 0
        assert "e2e_checkout" in readme_content
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)


async def test_new_skills_loading() -> None:
    """验证 V1.0 新增 Skill 可被加载和解析。

    Steps:
      1. 使用 SkillLoader 扫描 skills/ 目录，加载所有 Skill
      2. 验证 api_regression_test, web_visual_test, full_regression_test, app_smoke_test 均已加载
      3. 对每个 V1.0 Skill 验证元数据完整性和 SkillValidator 校验通过
    """
    loader = SkillLoader(SKILLS_DIR)
    raw_skills = loader.load_all()
    loaded_names = {s.name for s in raw_skills}

    missing = V1_SKILL_NAMES - loaded_names
    assert not missing, f"以下 V1.0 Skill 未被加载: {missing}"

    for skill_name in sorted(V1_SKILL_NAMES):
        raw = next(s for s in raw_skills if s.name == skill_name)

        assert raw.name == skill_name
        assert raw.version, f"{skill_name} 缺少 version"
        assert raw.meta.get("description"), f"{skill_name} 缺少 description"
        assert raw.meta.get("trigger"), f"{skill_name} 缺少 trigger"
        mcp_meta = raw.meta.get("required_mcp_servers")
        assert isinstance(mcp_meta, list), f"{skill_name} required_mcp_servers 非列表"
        rag_meta = raw.meta.get("required_rag_collections")
        assert isinstance(rag_meta, list), f"{skill_name} required_rag_collections 非列表"

        body = raw.body
        assert "## 目标" in body, f"{skill_name} Body 缺少'目标'章节"
        assert "## 操作流程" in body, f"{skill_name} Body 缺少'操作流程'章节"
        assert "## 断言策略" in body, f"{skill_name} Body 缺少'断言策略'章节"
        assert "## 失败处理" in body, f"{skill_name} Body 缺少'失败处理'章节"

        validator = SkillValidator()
        validation = validator.validate(raw.meta)
        assert validation.valid, f"{skill_name} SkillValidator 校验失败: {validation.errors}"


async def test_api_regression_skill_execution() -> None:
    """验证 api_regression_test Skill 可执行。

    Steps:
      1. 加载 api_regression_test Skill 并转换为 SkillDefinition
      2. 注册到 SkillRegistry，验证 Layer 1 注入描述包含该 Skill
      3. 使用 SkillExecutor 执行（无 MCP Registry，走 fallback 路径）
      4. 验证执行结果 status=passed，步骤数 > 0
      5. 验证所有步骤均有 step_index 和 step_name
      6. 验证 SkillResult 元数据与 SkillDefinition 一致
    """
    raw = _load_raw_skill("api_regression_test")
    skill_def = _raw_skill_to_definition(raw)

    registry = SkillRegistry()
    registry.register(skill_def)

    by_name = registry.get_by_name("api_regression_test")
    assert by_name is not None
    assert by_name.name == "api_regression_test"
    assert by_name.version == "1.1.0"

    descriptions = registry.get_descriptions()
    assert "api_regression_test" in descriptions
    assert "API 回归测试" in descriptions

    executor = SkillExecutor(mcp_registry=None)
    result = await executor.execute(skill_def)

    assert isinstance(result, SkillResult)
    assert result.skill_name == "api_regression_test"
    assert result.skill_version == "1.1.0"
    assert result.status == "passed"
    assert len(result.step_results) > 0, "api_regression_test 应有至少一个步骤"

    for step in result.step_results:
        assert isinstance(step.step_index, int)
        assert isinstance(step.step_name, str)
        assert len(step.step_name) > 0

    assert result.duration_ms >= 0


async def test_web_visual_skill_execution() -> None:
    """验证 web_visual_test Skill 可执行。

    Steps:
      1. 加载 web_visual_test Skill 并转换为 SkillDefinition
      2. 验证 required_mcp_servers 包含 playwright_server
      3. 验证 required_rag_collections 包含 test_reports
      4. 使用 SkillExecutor 执行（无 MCP Registry）
      5. 验证执行结果 status=passed
      6. 验证步骤包含截图采集、像素差异检测、布局偏移检测等关键步骤
    """
    raw = _load_raw_skill("web_visual_test")
    skill_def = _raw_skill_to_definition(raw)

    mcp_servers = cast("list[str]", skill_def.required_mcp_servers or [])
    assert mcp_servers == ["playwright_server"]
    rag_collections = cast("list[str]", skill_def.required_rag_collections or [])
    assert rag_collections == ["test_reports"]

    registry = SkillRegistry()
    registry.register(skill_def)

    content = registry.get_content("web_visual_test")
    assert len(content) > 0, "Layer 2 注入应返回 Skill Body"

    executor = SkillExecutor(mcp_registry=None)
    result = await executor.execute(skill_def)

    assert isinstance(result, SkillResult)
    assert result.skill_name == "web_visual_test"
    assert result.skill_version == "1.0.0"
    assert result.status == "passed"
    assert len(result.step_results) > 0

    step_names = [s.step_name for s in result.step_results]
    assert any("准备" in name or "基线" in name for name in step_names), f"缺少准备阶段步骤, got: {step_names}"
    assert any("截图" in name for name in step_names), f"缺少截图采集步骤, got: {step_names}"
    assert any("像素" in name for name in step_names), f"缺少像素差异检测步骤, got: {step_names}"
    assert any("布局" in name for name in step_names), f"缺少布局偏移检测步骤, got: {step_names}"


async def test_full_regression_skill_execution() -> None:
    """验证 full_regression_test Skill 可编排 API + Web 测试。

    Steps:
      1. 加载 full_regression_test Skill 并转换为 SkillDefinition
      2. 验证 required_mcp_servers 同时包含 api_server 和 playwright_server
      3. 验证 required_rag_collections 包含 api_docs 和 defect_history
      4. 注册到 SkillRegistry，验证 Layer 1 注入描述
      5. 使用 SkillExecutor 执行
      6. 验证执行结果 status=passed
      7. 验证步骤包含 Phase 1 (API) 和 Phase 2 (Web) 编排
      8. 验证 SkillMatcher 能通过 trigger 匹配到该 Skill
    """
    raw = _load_raw_skill("full_regression_test")
    skill_def = _raw_skill_to_definition(raw)

    mcp_servers = cast("list[str]", skill_def.required_mcp_servers or [])
    assert "api_server" in mcp_servers, f"full_regression_test 缺少 api_server, got: {mcp_servers}"
    assert "playwright_server" in mcp_servers, f"full_regression_test 缺少 playwright_server, got: {mcp_servers}"

    rag_collections = cast("list[str]", skill_def.required_rag_collections or [])
    assert "api_docs" in rag_collections, f"full_regression_test 缺少 api_docs, got: {rag_collections}"
    assert "defect_history" in rag_collections, f"full_regression_test 缺少 defect_history, got: {rag_collections}"

    registry = SkillRegistry()
    registry.register(skill_def)

    descriptions = registry.get_descriptions()
    assert "full_regression_test" in descriptions
    assert "全量回归" in descriptions or "回归测试编排" in descriptions

    content = registry.get_content("full_regression_test")
    assert "Phase 1" in content or "API" in content, "Body 应包含 API 测试阶段"
    assert "Phase 2" in content or "Web" in content, "Body 应包含 Web 测试阶段"

    executor = SkillExecutor(mcp_registry=None)
    result = await executor.execute(skill_def)

    assert isinstance(result, SkillResult)
    assert result.skill_name == "full_regression_test"
    assert result.skill_version == "1.0.0"
    assert result.status == "passed"
    assert len(result.step_results) > 0

    step_names = [s.step_name for s in result.step_results]
    has_api_phase = any("API" in name or "规划" in name for name in step_names)
    has_web_phase = any("Web" in name or "综合" in name for name in step_names)
    assert has_api_phase or has_web_phase, f"步骤应包含 API/Web 阶段编排, got: {step_names}"

    matched = registry.match_by_trigger("全量回归测试")
    matched_names = [s.name for s in matched]
    assert "full_regression_test" in matched_names, f"trigger 匹配未命中 full_regression_test, got: {matched_names}"

    matched2 = registry.match_by_trigger("full regression test")
    matched_names2 = [s.name for s in matched2]
    assert "full_regression_test" in matched_names2, (
        f"trigger 匹配未命中 full_regression_test (English), got: {matched_names2}"
    )
