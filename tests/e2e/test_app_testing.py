from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.agent.analyzer import AnalyzerAgent
from testagent.agent.context import ContextAssembler
from testagent.agent.executor import ExecutorAgent
from testagent.agent.planner import PlannerAgent
from testagent.config.settings import get_settings
from testagent.gateway.session import SessionManager
from testagent.harness.microvm_sandbox import MicroVMSandbox
from testagent.harness.sandbox import RESOURCE_PROFILES
from testagent.harness.sandbox_factory import IsolationLevel, SandboxFactory
from testagent.llm.base import LLMResponse
from testagent.models.skill import SkillDefinition
from testagent.skills.loader import SkillLoader
from testagent.skills.registry import SkillRegistry
from testagent.skills.validator import SkillValidator

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio,
]

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"


# =============================================================================
# Helpers
# =============================================================================


def _make_planner_response(stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        content=[
            {
                "type": "text",
                "text": (
                    "App test plan generated with 3 tasks: "
                    "splash screen validation, navigation flow, core feature check."
                ),
            },
        ],
        stop_reason=stop_reason,
        usage={"input_tokens": 50, "output_tokens": 30},
    )


def _make_executor_response(stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        content=[
            {
                "type": "text",
                "text": (
                    "Executed App test: splash screen visible, navigation tabs functional, core features accessible."
                ),
            },
        ],
        stop_reason=stop_reason,
        usage={"input_tokens": 40, "output_tokens": 25},
    )


def _make_analyzer_response(stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        content=[
            {
                "type": "text",
                "text": ("Analysis complete: 3 passed, 0 failed. No ANR or Crash detected. No defects to file."),
            },
        ],
        stop_reason=stop_reason,
        usage={"input_tokens": 60, "output_tokens": 35},
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def session_manager() -> SessionManager:
    return SessionManager()


@pytest.fixture()
def mock_planner_llm() -> MagicMock:
    mock = MagicMock()
    mock.chat = AsyncMock(side_effect=[_make_planner_response()])
    mock.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return mock


@pytest.fixture()
def mock_executor_llm() -> MagicMock:
    mock = MagicMock()
    mock.chat = AsyncMock(side_effect=[_make_executor_response()])
    mock.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return mock


@pytest.fixture()
def mock_analyzer_llm() -> MagicMock:
    mock = MagicMock()
    mock.chat = AsyncMock(side_effect=[_make_analyzer_response()])
    mock.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return mock


@pytest.fixture()
def mock_mcp_registry() -> MagicMock:
    mock = MagicMock()
    appium_info = MagicMock()
    appium_info.name = "appium_server"
    appium_info.status = "healthy"
    appium_info.tools = [{"name": "create_session"}, {"name": "find_element"}, {"name": "tap"}]
    mock.is_registered = MagicMock(return_value=True)
    mock.register = AsyncMock(return_value=appium_info)
    mock.list_servers = AsyncMock(return_value=[appium_info])
    mock.lookup = AsyncMock(return_value=appium_info)
    return mock


# =============================================================================
# Tests
# =============================================================================


async def test_app_smoke_test_skill_loading() -> None:
    """验证 app_smoke_test Skill 可被 SkillLoader 加载和解析。

    Steps:
      1. 使用 SkillLoader 扫描 skills/ 目录，加载 app_smoke_test
      2. 验证 RawSkill 元数据：name, version, required_mcp_servers, required_rag_collections
      3. 验证 Markdown Body 包含必需章节：目标、操作流程、断言策略、失败处理
      4. 使用 SkillValidator 校验通过（valid=True）
    """
    loader = SkillLoader(SKILLS_DIR)
    raw_skills = loader.load_all()
    app_skill = next((s for s in raw_skills if s.name == "app_smoke_test"), None)

    assert app_skill is not None, "app_smoke_test Skill 未被 SkillLoader 加载"
    assert app_skill.name == "app_smoke_test"
    assert app_skill.version == "1.0.0"
    assert app_skill.meta.get("description") == "App 核心流程冒烟测试技能，验证移动端关键功能可用性"
    assert app_skill.meta.get("required_mcp_servers") == ["appium_server"]
    assert app_skill.meta.get("required_rag_collections") == ["req_docs", "locator_library"]

    trigger = app_skill.meta.get("trigger", "")
    assert isinstance(trigger, str)
    assert "app.*smoke" in trigger
    assert "移动端测试" in trigger
    assert "mobile.*smoke" in trigger

    body = app_skill.body
    assert "## 目标" in body
    assert "## 操作流程" in body
    assert "## 断言策略" in body
    assert "## 失败处理" in body

    validator = SkillValidator()
    result = validator.validate(app_skill.meta)
    assert result.valid, f"Skill 校验失败: {result.errors}"
    assert not result.degraded, "无 MCPRegistry 时应为 degraded=False"


async def test_app_smoke_test_skill_loading_with_mcp_check(mock_mcp_registry: MagicMock) -> None:
    """验证 app_smoke_test 在 MCP 注册检查场景下的加载行为。

    Steps:
      1. 使用 SkillLoader 加载 app_smoke_test Skill
      2. 使用含 MCPRegistry 的 SkillValidator 校验
      3. appium_server 已注册 -> valid=True, degraded=False
      4. 模拟 appium_server 未注册 -> valid=True, degraded=True
    """
    loader = SkillLoader(SKILLS_DIR)
    raw_skills = loader.load_all()
    app_skill = next((s for s in raw_skills if s.name == "app_smoke_test"), None)
    assert app_skill is not None

    # appium_server 已注册 -> valid=True, degraded=False
    validator_registered = SkillValidator(mcp_registry=mock_mcp_registry)
    result = validator_registered.validate(app_skill.meta)
    assert result.valid
    assert not result.degraded

    # appium_server 未注册 -> valid=True, degraded=True
    mock_empty = MagicMock()
    mock_empty.is_registered = MagicMock(return_value=False)
    validator_unregistered = SkillValidator(mcp_registry=mock_empty)
    result = validator_unregistered.validate(app_skill.meta)
    assert result.valid
    assert result.degraded
    assert any("appium_server" in w for w in result.warnings)


async def test_app_smoke_test_skill_registration() -> None:
    """验证 app_smoke_test Skill 以 name+version 为唯一键注册。

    Steps:
      1. 从 skills/ 加载 app_smoke_test
      2. 注册到 SkillRegistry
      3. 通过 get_by_name 和 name+version 可正确获取
      4. 重复注册相同 name+version 可覆盖
    """
    loader = SkillLoader(SKILLS_DIR)
    raw_skills = loader.load_all()
    app_skill = next((s for s in raw_skills if s.name == "app_smoke_test"), None)
    assert app_skill is not None

    registry = SkillRegistry()
    skill_def = SkillDefinition(
        name=app_skill.name,
        version=app_skill.version,
        description=app_skill.meta.get("description", ""),
        trigger_pattern=app_skill.meta.get("trigger", ""),
        required_mcp_servers=app_skill.meta.get("required_mcp_servers", []),
        required_rag_collections=app_skill.meta.get("required_rag_collections", []),
        body=app_skill.body,
    )
    registry.register(skill_def)

    by_name = registry.get_by_name("app_smoke_test")
    assert by_name is not None
    assert by_name.name == "app_smoke_test"
    assert by_name.version == "1.0.0"

    by_full = registry.get_by_name("app_smoke_test", version="1.0.0")
    assert by_full is not None
    assert by_full.name == "app_smoke_test"
    assert by_full.version == "1.0.0"

    assert registry.count() == 1


async def test_app_smoke_test_execution(
    mock_planner_llm: MagicMock,
    mock_executor_llm: MagicMock,
    mock_analyzer_llm: MagicMock,
    session_manager: SessionManager,
) -> None:
    """端到端验证 App 测试全链路：Planner → Executor → Analyzer。

    Steps:
      1. 创建 Session -> status=pending
      2. Planner 生成 App 测试计划 -> planning
      3. Executor 执行 App 冒烟测试 -> executing
      4. Analyzer 分析测试结果 -> analyzing
      5. 完成 Session -> status=completed
    """
    settings = get_settings()
    context_assembler = ContextAssembler(settings=settings)

    planner = PlannerAgent(llm=mock_planner_llm, context_assembler=context_assembler)
    executor = ExecutorAgent(llm=mock_executor_llm, context_assembler=context_assembler)
    analyzer = AnalyzerAgent(llm=mock_analyzer_llm, context_assembler=context_assembler)

    # Step 1: Create session -> status=pending
    session = await session_manager.create_session(
        name="e2e-app-smoke",
        trigger_type="manual",
        input_context={"skill": "app_smoke_test", "env": "staging"},
    )
    session_id: str = session["id"]
    assert session["status"] == "pending"
    assert session["name"] == "e2e-app-smoke"

    # Step 2: PlannerAgent generates app test plan -> status=planning
    await session_manager.transition(session_id, "planning")
    plan_result = await planner.execute(
        {
            "task_type": "plan",
            "requirement": ("App smoke test for splash screen, navigation, and core features on Android/iOS devices"),
            "skill": "app_smoke_test",
        }
    )
    assert plan_result["agent_type"] == "planner"
    assert "plan" in plan_result
    plan = plan_result["plan"]
    assert isinstance(plan, dict)
    assert "strategy" in plan
    assert "test_tasks" in plan
    assert plan_result["message_count"] >= 2

    # Step 3: ExecutorAgent executes app smoke test -> status=executing
    await session_manager.transition(session_id, "executing")
    execute_result = await executor.execute(
        {
            "task_type": "app_test",
            "skill": "app_smoke_test",
            "task_config": {
                "platform": "Android",
                "app_package": "com.example.app",
                "test_cases": [
                    {"name": "splash_screen", "action": "verify_splash_visible"},
                    {"name": "navigation_tabs", "action": "verify_tab_switch"},
                    {"name": "home_content", "action": "verify_home_elements"},
                ],
            },
        }
    )
    assert execute_result["agent_type"] == "executor"
    assert "result" in execute_result
    assert isinstance(execute_result["result"], dict)
    assert execute_result["message_count"] >= 2

    # Step 4: AnalyzerAgent analyzes results -> status=analyzing
    await session_manager.transition(session_id, "analyzing")
    analyze_result = await analyzer.execute(
        {
            "task_type": "analyze",
            "failed_results": [],
            "session_id": session_id,
        }
    )
    assert analyze_result["agent_type"] == "analyzer"
    assert "analysis" in analyze_result
    analysis = analyze_result["analysis"]
    assert isinstance(analysis, dict)
    assert "summary" in analysis
    assert "defects" in analysis
    assert "classification" in analysis
    assert analyze_result["message_count"] >= 2

    # Step 5: Verify session -> status=completed
    completed_session = await session_manager.transition(session_id, "completed")
    assert completed_session["status"] == "completed"
    assert completed_session["completed_at"] is not None


async def test_app_test_microvm_isolation() -> None:
    """验证 App 测试使用 MicroVM 隔离级别。

    Steps:
      1. SandboxFactory.decide_isolation("app_test") -> MICROVM
      2. 验证 app_test 的资源配额：4CPU/4GB/180s
      3. SandboxFactory.create(IsolationLevel.MICROVM) -> MicroVMSandbox 实例
      4. 验证 API test 和 Web test 不是 MICROVM（回归检查）
    """
    # 1. app_test -> MICROVM
    level = SandboxFactory.decide_isolation("app_test")
    assert level == IsolationLevel.MICROVM, f"期望 MICROVM, 实际 {level}"

    # 2. 验证资源配额：4CPU/4GB/180s
    profile = RESOURCE_PROFILES.get("app_test")
    assert profile is not None, "app_test 资源配额未定义"
    assert profile.cpus == 4, f"期望 4 CPU, 实际 {profile.cpus}"
    assert profile.mem_limit == "4g", f"期望 4g 内存, 实际 {profile.mem_limit}"
    assert profile.timeout == 180, f"期望 180s 超时, 实际 {profile.timeout}"
    assert profile.read_only, "app_test 应为 read_only 文件系统"

    # 3. SandboxFactory.create(MICROVM) -> MicroVMSandbox 实例
    sandbox = SandboxFactory.create(IsolationLevel.MICROVM)
    assert isinstance(sandbox, MicroVMSandbox), f"期望 MicroVMSandbox, 实际 {type(sandbox).__name__}"

    # 4. 回归检查：api_test/web_test 不是 MICROVM
    api_level = SandboxFactory.decide_isolation("api_test")
    assert api_level != IsolationLevel.MICROVM, "api_test 不应使用 MICROVM"
    web_level = SandboxFactory.decide_isolation("web_test")
    assert web_level != IsolationLevel.MICROVM, "web_test 不应使用 MICROVM"

    # force_local 可覆盖 MICROVM（仅开发调试模式）
    forced_local = SandboxFactory.decide_isolation("app_test", force_local=True)
    assert forced_local == IsolationLevel.LOCAL, f"force_local 应返回 LOCAL, 实际 {forced_local}"
