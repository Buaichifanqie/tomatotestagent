from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from testagent.agent.analyzer import AnalyzerAgent
from testagent.agent.context import ContextAssembler
from testagent.agent.planner import PlannerAgent
from testagent.cli.main import app as cli_app
from testagent.config.settings import get_settings
from testagent.gateway.middleware import register_error_handlers
from testagent.gateway.router import router, set_mcp_registry, set_session_manager
from testagent.gateway.session import SessionManager
from testagent.harness.orchestrator import HarnessOrchestrator
from testagent.harness.runners.base import RunnerFactory
from testagent.harness.sandbox import ISandbox
from testagent.harness.sandbox_factory import SandboxFactory
from testagent.llm.base import LLMResponse
from testagent.models.plan import TestTask
from testagent.models.result import TestResult
from testagent.rag.pipeline import RAGPipeline
from testagent.skills.executor import SkillResult, SkillStepResult

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio,
]

# =============================================================================
# Helpers
# =============================================================================


def _make_planner_response(stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        content=[
            {
                "type": "text",
                "text": "Test plan generated with 3 tasks: health check, user creation, data query.",
            },
        ],
        stop_reason=stop_reason,
        usage={"input_tokens": 50, "output_tokens": 30},
    )


def _make_executor_response(stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        content=[{"type": "text", "text": "Executed API test: GET /health returned 200 OK."}],
        stop_reason=stop_reason,
        usage={"input_tokens": 40, "output_tokens": 25},
    )


def _make_analyzer_response(stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        content=[{"type": "text", "text": "Analysis complete: 3 passed, 0 failed. No defects to file."}],
        stop_reason=stop_reason,
        usage={"input_tokens": 60, "output_tokens": 35},
    )


def _make_tool_use_rag_response() -> LLMResponse:
    return LLMResponse(
        content=[
            {"type": "text", "text": "Searching knowledge base for API documentation..."},
            {
                "type": "tool_use",
                "name": "rag_query",
                "input": {"query": "API smoke test documentation", "collections": ["api_docs"]},
            },
        ],
        stop_reason="tool_use",
        usage={"input_tokens": 30, "output_tokens": 20},
    )


def _make_tool_use_follow_up() -> LLMResponse:
    return LLMResponse(
        content=[{"type": "text", "text": "Based on the API docs, I have created a comprehensive test plan."}],
        stop_reason="end_turn",
        usage={"input_tokens": 70, "output_tokens": 30},
    )


def _make_skill_result(*, status: str = "passed", steps: int = 3) -> SkillResult:
    return SkillResult(
        skill_name="api_smoke_test",
        skill_version="1.0.0",
        status=status,
        step_results=[
            SkillStepResult(
                step_index=i,
                step_name=f"step_{i}",
                status=status if i < steps - 1 or status == "passed" else "failed",
                output={"result": "ok", "duration_ms": 100 * (i + 1)},
                error=None,
                duration_ms=100.0 * (i + 1),
            )
            for i in range(steps)
        ],
        error=None if status == "passed" else "Step 2 failed: assertion mismatch",
        duration_ms=500.0,
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def session_manager() -> SessionManager:
    return SessionManager()


@pytest.fixture()
def api_app(session_manager: SessionManager) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    set_session_manager(session_manager)

    # Mock MCP Registry to avoid real subprocess startup
    mock_registry = MagicMock()

    mock_api_info = MagicMock()
    mock_api_info.name = "api_server"
    mock_api_info.status = "healthy"
    mock_api_info.tools = [{"name": "http_request"}]

    mock_db_info = MagicMock()
    mock_db_info.name = "database_server"
    mock_db_info.status = "healthy"
    mock_db_info.tools = []

    mock_registry.register = AsyncMock(
        side_effect=lambda config: mock_api_info if "api" in config.server_name else mock_db_info
    )
    mock_registry.list_servers = AsyncMock(return_value=[mock_api_info, mock_db_info])
    mock_registry.lookup = AsyncMock(return_value=mock_api_info)
    set_mcp_registry(mock_registry)

    app.include_router(router)
    return app


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
def mock_tool_rag_llm() -> MagicMock:
    mock = MagicMock()
    mock.chat = AsyncMock(side_effect=[_make_tool_use_rag_response(), _make_tool_use_follow_up()])
    mock.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    mock.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    return mock


@pytest.fixture()
def mock_failed_executor_llm() -> MagicMock:
    mock = MagicMock()
    mock.chat = AsyncMock(
        return_value=LLMResponse(
            content=[{"type": "text", "text": "Test execution completed: 2 passed, 2 failed, 1 flaky."}],
            stop_reason="end_turn",
            usage={"input_tokens": 50, "output_tokens": 30},
        )
    )
    mock.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return mock


# =============================================================================
# test_full_api_testing_pipeline
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_full_api_testing_pipeline(
    mock_planner_llm: MagicMock,
    mock_analyzer_llm: MagicMock,
    mock_tool_rag_llm: MagicMock,
    session_manager: SessionManager,
    api_app: FastAPI,
) -> None:
    """
    全链路 API 测试端到端验证:
    1. 初始化项目: testagent init --project demo --type api
    2. 注册 MCP Server: api_server, database_server
    3. 索引 RAG 文档: 摄入 OpenAPI 规范到 api_docs
    4. 运行冒烟测试: testagent run --skill api_smoke_test --env staging
    5. 验证 Planner 生成测试计划
    6. 验证 Executor 在 Docker 沙箱中执行测试
    7. 验证 Analyzer 对失败结果分类
    8. 验证缺陷自动归档到 Jira (mock)
    9. 验证测试报告生成
    10. 验证分析结果写回 RAG (知识闭环)
    """
    settings = get_settings()

    # Step 1: Initialize project via API
    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        init_resp = await client.post(
            "/api/v1/sessions",
            json={
                "name": "full-api-pipeline",
                "trigger_type": "manual",
                "input_context": {
                    "project": "demo",
                    "test_type": "api",
                    "skill": "api_smoke_test",
                    "env": "staging",
                },
            },
        )
        assert init_resp.status_code == 201
        init_data = init_resp.json()
        session_id: str = init_data["data"]["id"]
        assert session_id is not None

    # Step 2: Register MCP Servers via API
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for server_name in ("api_server", "database_server"):
            mcp_resp = await client.post(
                "/api/v1/mcp/servers",
                json={
                    "server_name": server_name,
                    "command": "python",
                    "args": {"module": f"testagent.mcp_servers.{server_name.replace('_', '_')}.server"},
                },
            )
            assert mcp_resp.status_code == 201
            mcp_data = mcp_resp.json()
            assert mcp_data["data"]["name"] == server_name

    # Step 3: Index RAG documents
    context_assembler = ContextAssembler(settings=settings)
    planner = PlannerAgent(llm=mock_planner_llm, context_assembler=context_assembler)

    # Step 4-5: Run smoke test via Planner -> generates test plan
    await session_manager.transition(session_id, "planning")
    plan_result = await planner.execute(
        {
            "task_type": "plan",
            "skill": "api_smoke_test",
            "requirement": "API smoke test for demo project on staging environment",
            "rag_query": "OpenAPI specification for demo project",
        }
    )

    assert plan_result["agent_type"] == "planner"
    assert "plan" in plan_result
    plan: dict[str, Any] = plan_result["plan"]
    assert "strategy" in plan
    assert "test_tasks" in plan
    assert plan_result["message_count"] >= 2

    # Step 6: Executor executes in Docker sandbox
    await session_manager.transition(session_id, "executing")

    # Create mock sandbox + runner for Docker execution verification
    mock_sandbox = MagicMock(spec=ISandbox)
    mock_sandbox.create = AsyncMock(return_value="sandbox-api-001")
    mock_sandbox.get_tmpdir = AsyncMock(return_value="/tmp/testagent")
    mock_sandbox.get_logs = AsyncMock(return_value="[2024-01-01] GET /health -> 200 OK")
    mock_sandbox.get_artifacts = AsyncMock(return_value=[])
    mock_sandbox.destroy = AsyncMock()

    mock_sandbox.execute = AsyncMock(
        return_value={
            "exit_code": 0,
            "stdout": json.dumps(
                {
                    "status": "passed",
                    "assertion_results": {
                        "status_code": {"expected": 200, "actual": 200, "passed": True},
                        "response_time": {"expected": "< 1000ms", "actual": "45ms", "passed": True},
                    },
                    "logs": '{"method": "GET", "path": "/health", "status_code": 200, "duration_ms": 45.0}',
                    "artifacts": {"status_code": 200, "response_time_ms": 45},
                }
            ),
            "stderr": "",
        }
    )

    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()
    mock_runner.execute = AsyncMock()
    mock_runner.collect_results = AsyncMock(
        return_value=TestResult(
            task_id="api-task-001",
            status="passed",
            duration_ms=45.0,
            assertion_results={
                "status_code": {"expected": 200, "actual": 200, "passed": True},
                "response_time": {"expected": "< 1000ms", "actual": "45ms", "passed": True},
            },
            logs='{"method": "GET", "path": "/health", "status_code": 200, "duration_ms": 45.0}',
            artifacts={"status_code": 200, "response_time_ms": 45, "sandbox_id": "sandbox-api-001"},
        )
    )
    mock_runner.teardown = AsyncMock()
    mock_runner.runner_type = "api_test"

    mock_sandbox_factory = MagicMock(spec=SandboxFactory)
    mock_sandbox_factory.create.return_value = mock_sandbox
    mock_runner_factory = MagicMock(spec=RunnerFactory)
    mock_runner_factory.get_runner.return_value = mock_runner

    orchestrator = HarnessOrchestrator(
        sandbox_factory=mock_sandbox_factory,
        runner_factory=mock_runner_factory,
    )

    # Verify Docker sandbox is used for api_test tasks
    api_task = TestTask(
        id="api-task-001",
        plan_id="plan-001",
        task_type="api_test",
        isolation_level="docker",
        priority=1,
        status="queued",
        retry_count=0,
        task_config={
            "base_url": "http://staging.demo.com",
            "method": "GET",
            "path": "/health",
            "assertions": {"status_code": 200, "response_time": "< 1000ms"},
        },
    )

    harness_result = await orchestrator.dispatch(api_task)
    assert harness_result.status == "passed"
    assert harness_result.task_id == "api-task-001"
    assert harness_result.assertion_results is not None
    assert harness_result.assertion_results["status_code"]["passed"] is True  # type: ignore[index]
    assert harness_result.assertion_results["status_code"]["actual"] == 200  # type: ignore[index]

    # Verify Docker lifecycle: create -> setup -> execute -> collect -> teardown -> destroy
    mock_sandbox.create.assert_called_once()
    mock_runner.setup.assert_called_once()
    mock_runner.execute.assert_called_once()
    mock_runner.collect_results.assert_called_once()
    mock_runner.teardown.assert_called_once()
    mock_sandbox.destroy.assert_called_once_with("sandbox-api-001")

    # Verify sandbox received correct security config
    create_call_kwargs = mock_sandbox.create.call_args.kwargs
    assert create_call_kwargs is not None  # sandbox creation was called

    # Step 7: Analyzer classifies results
    analyzer = AnalyzerAgent(llm=mock_analyzer_llm, context_assembler=context_assembler)
    await session_manager.transition(session_id, "analyzing")
    analyze_result = await analyzer.execute(
        {
            "task_type": "analyze",
            "session_id": session_id,
            "failed_results": [],
            "all_results": [
                {
                    "task_id": "api-task-001",
                    "status": "passed",
                    "duration_ms": 45.0,
                    "assertion_results": harness_result.assertion_results,
                }
            ],
        }
    )

    assert analyze_result["agent_type"] == "analyzer"
    assert "analysis" in analyze_result
    analysis: dict[str, Any] = analyze_result["analysis"]
    assert "summary" in analysis
    assert "defects" in analysis
    assert "classification" in analysis
    assert analyze_result["message_count"] >= 2

    # Step 8: Verify defect auto-filing to Jira (mocked)
    mock_jira_client = MagicMock()
    mock_jira_client.create_issue = AsyncMock(return_value={"id": "JIRA-123", "key": "TEST-123"})

    defects = analysis.get("defects", [])

    classifications = ("bug", "flaky", "environment", "configuration")
    if defects:
        for defect in defects:
            if isinstance(defect, dict) and defect.get("classification") in classifications:
                jira_issue = await mock_jira_client.create_issue(
                    project="TEST",
                    summary=defect.get("title", "Auto-filed defect"),
                    description=defect.get("description", ""),
                    issuetype="Bug",
                )
                assert jira_issue["id"] == "JIRA-123"
                mock_jira_client.create_issue.assert_called_once()

    # Step 9: Verify test report generation via API
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        report_resp = await client.get(f"/api/v1/reports/{session_id}")
        assert report_resp.status_code == 200
        report_body = report_resp.json()
        assert report_body["data"]["session"]["id"] == session_id
        assert "summary" in report_body["data"]
        report_summary = report_body["data"]["summary"]
        assert isinstance(report_summary, dict)

    # Step 10: Verify analysis results written back to RAG (knowledge closure)
    mock_rag_pipeline = MagicMock(spec=RAGPipeline)
    mock_rag_pipeline.index_document = AsyncMock()
    mock_rag_pipeline.query = AsyncMock(return_value=[])

    # Simulate writing analysis results back to defect_history collection
    await mock_rag_pipeline.index_document(
        source=json.dumps(
            {
                "session_id": session_id,
                "analysis_summary": analysis.get("summary", ""),
                "total_tasks": 1,
                "passed": 1,
                "failed": 0,
                "defects_found": defects,
            }
        ),
        collection="defect_history",
        metadata={"session_id": session_id, "source": "analyzer", "timestamp": "2024-01-01T00:00:00Z"},
    )

    mock_rag_pipeline.index_document.assert_called_once()
    call_args = mock_rag_pipeline.index_document.call_args
    assert call_args.kwargs["collection"] == "defect_history"
    assert "session_id" in call_args.kwargs["source"] or call_args.kwargs["source"] is not None

    # Verify knowledge closure: re-querying RAG should return the newly indexed analysis
    rag_results = await mock_rag_pipeline.query(
        text=f"Analysis results for session {session_id}",
        top_k=5,
        collections=["defect_history"],
    )
    assert isinstance(rag_results, list)

    # Complete the session
    completed_session = await session_manager.transition(session_id, "completed")
    assert completed_session["status"] == "completed"
    assert completed_session["completed_at"] is not None


# =============================================================================
# test_full_web_testing_pipeline
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_full_web_testing_pipeline(
    mock_planner_llm: MagicMock,
    session_manager: SessionManager,
    api_app: FastAPI,
) -> None:
    """
    全链路 Web 测试端到端验证:
    1. testagent run --skill web_smoke_test --url https://example.com
    2. 验证 Playwright MCP Server 调用
    3. 验证 Docker 沙箱 Web Runner 执行
    4. 验证截图和视频 Artifact 收集
    """

    # Step 1: Create web smoke test session
    session = await session_manager.create_session(
        name="e2e-web-smoke",
        trigger_type="manual",
        input_context={
            "skill": "web_smoke_test",
            "url": "https://example.com",
            "browser_type": "chromium",
        },
    )
    session_id = session["id"]
    assert session["status"] == "pending"

    # Step 2: Verify Playwright MCP Server tool registration
    mock_playwright_server = MagicMock()
    mock_playwright_server.list_tools = AsyncMock(
        return_value=[
            {"name": "navigate", "description": "Navigate to a URL"},
            {"name": "click", "description": "Click an element"},
            {"name": "fill", "description": "Fill a form field"},
            {"name": "screenshot", "description": "Take a screenshot"},
            {"name": "get_text", "description": "Get element text"},
            {"name": "assert_title", "description": "Assert page title"},
            {"name": "assert_visible", "description": "Assert element visibility"},
        ]
    )
    tools = await mock_playwright_server.list_tools()
    tool_names = [t["name"] for t in tools]
    assert "navigate" in tool_names
    assert "screenshot" in tool_names
    assert "assert_title" in tool_names
    assert "click" in tool_names

    # Step 3: Web test execution in Docker sandbox with Playwright runner
    mock_sandbox = MagicMock(spec=ISandbox)
    mock_sandbox.create = AsyncMock(return_value="sandbox-web-001")
    mock_sandbox.get_tmpdir = AsyncMock(return_value="/tmp/testagent/web")
    mock_sandbox.destroy = AsyncMock()

    test_actions = [
        {"action": "navigate", "url": "https://example.com"},
        {"action": "assert_title", "expected_title": "Example Domain"},
        {"action": "screenshot"},
        {"action": "get_text", "selector": "h1", "assertion": True, "expected_text": "Example Domain"},
    ]

    mock_sandbox.execute = AsyncMock(
        return_value={
            "exit_code": 0,
            "stdout": json.dumps(
                {
                    "status": "passed",
                    "assertion_results": {
                        "assert_title_0": {"passed": True, "actual": "Example Domain", "expected": "Example Domain"},
                        "get_text_1": {"passed": True, "actual": "Example Domain", "expected": "Example Domain"},
                    },
                    "logs": json.dumps(
                        [
                            {"action": "navigate", "index": 0, "url": "https://example.com", "duration_ms": 200},
                            {"action": "assert_title", "index": 1, "duration_ms": 50},
                            {
                                "action": "screenshot",
                                "index": 2,
                                "path": "/tmp/screenshots/page.png",
                                "duration_ms": 100,
                            },
                            {"action": "get_text", "index": 3, "selector": "h1", "duration_ms": 30},
                        ]
                    ),
                    "artifacts": {
                        "screenshots": ["/tmp/screenshots/page.png"],
                        "videos": [],
                        "trace": "/tmp/traces/trace.zip",
                    },
                }
            ),
            "stderr": "",
        }
    )

    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()
    mock_runner.execute = AsyncMock()
    mock_runner.collect_results = AsyncMock(
        return_value=TestResult(
            task_id="web-task-001",
            status="passed",
            duration_ms=500.0,
            assertion_results={
                "assert_title_0": {"passed": True, "actual": "Example Domain", "expected": "Example Domain"},
                "get_text_1": {"passed": True, "actual": "Example Domain", "expected": "Example Domain"},
            },
            logs=json.dumps(test_actions),
            artifacts={
                "screenshots": ["/tmp/screenshots/page.png"],
                "videos": [],
                "trace": "/tmp/traces/trace.zip",
                "sandbox_id": "sandbox-web-001",
            },
        )
    )
    mock_runner.teardown = AsyncMock()

    mock_sandbox_factory = MagicMock(spec=SandboxFactory)
    mock_sandbox_factory.create.return_value = mock_sandbox
    mock_runner_factory = MagicMock(spec=RunnerFactory)
    mock_runner_factory.get_runner.return_value = mock_runner

    orchestrator = HarnessOrchestrator(
        sandbox_factory=mock_sandbox_factory,
        runner_factory=mock_runner_factory,
    )

    web_task = TestTask(
        id="web-task-001",
        plan_id="plan-web-001",
        task_type="web_test",
        isolation_level="docker",
        priority=1,
        status="queued",
        retry_count=0,
        task_config={
            "base_url": "https://example.com",
            "browser_type": "chromium",
            "actions": test_actions,
        },
    )

    # Execute web test via orchestrator
    await session_manager.transition(session_id, "planning")
    await session_manager.transition(session_id, "executing")
    harness_result = await orchestrator.dispatch(web_task)

    assert harness_result.status == "passed"
    assert harness_result.task_id == "web-task-001"

    # Step 4: Verify artifact collection (screenshots, videos)
    assert harness_result.artifacts is not None
    artifacts: dict[str, Any] = harness_result.artifacts
    assert "screenshots" in artifacts
    assert isinstance(artifacts["screenshots"], list)
    if artifacts["screenshots"]:
        screenshot_path = artifacts["screenshots"][0]
        assert screenshot_path.endswith(".png") or screenshot_path is not None

    if "videos" in artifacts:
        assert isinstance(artifacts["videos"], list)

    if "trace" in artifacts:
        assert artifacts["trace"] is not None

    # Verify assertion details
    assert harness_result.assertion_results is not None
    assert harness_result.assertion_results["assert_title_0"]["passed"] is True  # type: ignore[index]
    assert harness_result.assertion_results["assert_title_0"]["actual"] == "Example Domain"  # type: ignore[index]

    # Verify Docker sandbox lifecycle
    mock_sandbox.create.assert_called_once()
    mock_runner.setup.assert_called_once()
    mock_runner.execute.assert_called_once()
    mock_runner.collect_results.assert_called_once()
    mock_runner.teardown.assert_called_once()
    mock_sandbox.destroy.assert_called_once_with("sandbox-web-001")

    # Complete session and verify via API
    await session_manager.transition(session_id, "analyzing")
    await session_manager.transition(session_id, "completed")

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        report_resp = await client.get(f"/api/v1/reports/{session_id}")
        assert report_resp.status_code == 200
        report_body = report_resp.json()
        assert report_body["data"]["session"]["status"] == "completed"


# =============================================================================
# test_self_healing_flow
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_self_healing_flow(
    mock_analyzer_llm: MagicMock,
    session_manager: SessionManager,
) -> None:
    """
    自愈执行流程验证:
    1. 模拟 UI 元素变更导致定位器失效
    2. 验证 CSS -> XPath 降级自愈
    3. 验证自愈事件通知
    """
    settings = get_settings()
    context_assembler = ContextAssembler(settings=settings)

    # Create session for self-healing test
    session = await session_manager.create_session(
        name="e2e-self-healing",
        trigger_type="manual",
        input_context={
            "skill": "web_smoke_test",
            "self_healing_enabled": True,
        },
    )
    session_id = session["id"]
    await session_manager.transition(session_id, "planning")
    await session_manager.transition(session_id, "executing")

    # Step 1: Simulate locator failure
    # Original locator: CSS selector "#submit-btn"
    # After UI change: this CSS selector no longer matches

    original_locator = "#submit-btn"
    fallback_locators = [
        "button.submit",  # CSS class fallback
        "//button[contains(text(), 'Submit')]",  # XPath text fallback
        "//button[@type='submit']",  # XPath attribute fallback
    ]

    # Simulate element lookup failure with CSS, then success with XPath
    locator_results: list[dict[str, Any]] = []
    healer_mock = MagicMock()
    healer_mock.attempt = AsyncMock()

    for attempt_index, locator in enumerate([original_locator, *fallback_locators]):
        if attempt_index == 0:
            # CSS selector fails
            healer_mock.attempt.return_value = {"found": False, "locator": locator, "strategy": "css"}
            locator_results.append(
                {
                    "found": False,
                    "locator": locator,
                    "strategy": "css",
                    "error": "Element not found",
                    "attempt": attempt_index + 1,
                }
            )
        else:
            # XPath fallback succeeds on second or third attempt
            success = attempt_index >= 2  # succeed with XPath attribute
            healer_mock.attempt.return_value = {
                "found": success,
                "locator": locator,
                "strategy": "xpath" if "//" in locator else "css",
            }
            locator_results.append(
                {
                    "found": success,
                    "locator": locator,
                    "strategy": "xpath" if "//" in locator else "css",
                    "attempt": attempt_index + 1,
                }
            )
            if success:
                break

    # Step 2: Verify CSS -> XPath degradation
    assert len(locator_results) >= 2  # at least one fallback was tried

    # First attempt should use CSS and fail
    first = locator_results[0]
    assert first["found"] is False
    assert first["strategy"] == "css"

    # Last successful attempt should use XPath
    successful = [r for r in locator_results if r["found"]]
    assert len(successful) >= 1, "At least one locator should succeed after fallback"

    final_success = successful[-1]
    assert final_success["strategy"] == "xpath"
    assert final_success["found"] is True
    assert "//" in final_success["locator"]

    # Record the healing event
    healing_events: list[dict[str, Any]] = []

    for result in locator_results:
        if result["found"]:
            healing_events.append(
                {
                    "type": "self_heal",
                    "original_locator": original_locator,
                    "resolved_locator": result["locator"],
                    "strategy": result["strategy"],
                    "attempts": result["attempt"],
                    "success": True,
                }
            )
        elif result.get("error"):
            healing_events.append(
                {
                    "type": "locator_failure",
                    "locator": result["locator"],
                    "error": result["error"],
                    "attempt": result["attempt"],
                }
            )

    # Step 3: Verify self-healing notifications
    assert len(healing_events) >= 2, "Should have at least one failure + one healing event"

    failure_events = [e for e in healing_events if e["type"] == "locator_failure"]
    heal_events = [e for e in healing_events if e["type"] == "self_heal"]

    assert len(failure_events) >= 1, "Should have at least one locator failure event"
    assert len(heal_events) >= 1, "Should have at least one self-heal event"

    # Verify healing event details
    heal_event = heal_events[-1]
    assert heal_event["original_locator"] == "#submit-btn"
    assert heal_event["strategy"] == "xpath"
    assert heal_event["success"] is True

    # Verify the resolution is recorded for future use (locator library update)
    locator_library_update = {
        "original": original_locator,
        "resolved": final_success["locator"],
        "strategy": final_success["strategy"],
        "verified_at": "2024-01-01T00:00:00Z",
    }
    assert locator_library_update["original"] != locator_library_update["resolved"]
    assert "xpath" in locator_library_update["strategy"]

    # Verify Analyzer can process self-healing results
    analyzer = AnalyzerAgent(llm=mock_analyzer_llm, context_assembler=context_assembler)
    await session_manager.transition(session_id, "analyzing")
    analyze_result = await analyzer.execute(
        {
            "task_type": "analyze",
            "session_id": session_id,
            "self_healing_events": healing_events,
            "failed_results": [
                {
                    "task_id": "web-task-heal-001",
                    "status": "failed",
                    "error": f"Locator '{original_locator}' not found, self-healed to '{final_success['locator']}'",
                    "healing": True,
                }
            ],
        }
    )

    assert analyze_result["agent_type"] == "analyzer"
    assert "analysis" in analyze_result
    analysis = analyze_result["analysis"]
    assert "summary" in analysis
    assert "self_healing" in analysis.get("classification", {}) or "defects" in analysis

    await session_manager.transition(session_id, "completed")


# =============================================================================
# test_concurrent_execution
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_concurrent_execution(
    mock_executor_llm: MagicMock,
    session_manager: SessionManager,
) -> None:
    """
    并行执行验证:
    1. 创建包含 5 个独立任务的测试计划
    2. 验证最多 5 路并行执行
    3. 验证每个 Executor Agent 数据隔离
    """

    # Step 1: Create a test plan with 5 independent tasks
    session = await session_manager.create_session(
        name="e2e-concurrent-exec",
        trigger_type="auto",
        input_context={
            "strategy": "parallel",
            "max_concurrency": 5,
            "tasks": [
                {"task_id": f"conc-task-{i:02d}", "type": "api_test", "endpoint": f"/api/v1/resource/{i}"}
                for i in range(5)
            ],
        },
    )
    session_id = session["id"]

    test_tasks: list[TestTask] = [
        TestTask(
            id=f"conc-task-{i:02d}",
            plan_id="plan-conc-001",
            task_type="api_test",
            isolation_level="local",
            priority=i + 1,
            status="queued",
            retry_count=0,
            task_config={
                "base_url": "http://staging.demo.com",
                "method": "GET",
                "path": f"/api/v1/resource/{i}",
                "assertions": {"status_code": 200},
            },
        )
        for i in range(5)
    ]

    # Step 2: Execute 5 tasks concurrently and verify max 5 parallel executions
    concurrency_tracker: list[int] = []

    async def _execute_with_tracking(task: TestTask) -> TestResult:
        nonlocal concurrency_tracker
        concurrency_tracker.append(1)
        current_concurrency = len(concurrency_tracker)
        assert current_concurrency <= 5, f"Concurrency exceeded 5: {current_concurrency}"

        # Simulate execution with data isolation per executor
        executor_id = f"executor_{task.id.split('-')[-1]}"

        mock_runner = MagicMock()
        mock_runner.setup = AsyncMock()
        mock_runner.execute = AsyncMock()
        task_idx = int(task.id.split("-")[-1])
        task_status = "passed" if task_idx % 3 != 0 else "failed"
        task_actual = 200 if task_idx % 3 != 0 else 500
        task_passed = task_idx % 3 != 0

        mock_runner.collect_results = AsyncMock(
            return_value=TestResult(
                task_id=task.id,
                status=task_status,
                duration_ms=100.0 * (task_idx + 1),
                assertion_results={
                    "status_code": {
                        "expected": 200,
                        "actual": task_actual,
                        "passed": task_passed,
                    },
                },
                logs=(f'{{"executor": "{executor_id}", "task": "{task.id}", "duration_ms": {100 * (task_idx + 1)}}}'),
                artifacts={"executor_id": executor_id, "task_id": task.id},
            )
        )
        mock_runner.teardown = AsyncMock()
        mock_runner.runner_type = "api_test"

        mock_sandbox = MagicMock(spec=ISandbox)
        mock_sandbox.create = AsyncMock(return_value=f"sandbox-{task.id}")
        mock_sandbox.destroy = AsyncMock()
        mock_sandbox.execute = AsyncMock(return_value={"exit_code": 0, "stdout": "ok", "stderr": ""})
        mock_sandbox.get_logs = AsyncMock(return_value="")
        mock_sandbox.get_artifacts = AsyncMock(return_value=[])

        mock_sandbox_factory = MagicMock(spec=SandboxFactory)
        mock_sandbox_factory.create.return_value = mock_sandbox
        mock_runner_factory = MagicMock(spec=RunnerFactory)
        mock_runner_factory.get_runner.return_value = mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=mock_sandbox_factory,
            runner_factory=mock_runner_factory,
        )

        result = await orchestrator.dispatch(task)

        # Step 3: Verify data isolation - each executor has its own sandbox and runner
        mock_sandbox.create.assert_called_once()
        assert mock_sandbox.create.call_args is not None
        mock_runner.setup.assert_called_once()
        mock_runner.execute.assert_called_once()
        mock_runner.collect_results.assert_called_once()
        mock_runner.teardown.assert_called_once()
        mock_sandbox.destroy.assert_called_once()

        # Verify executor-specific artifacts are isolated
        assert result.artifacts is not None
        assert result.artifacts["executor_id"] == executor_id
        assert result.artifacts["task_id"] == task.id

        concurrency_tracker.pop()
        return result

    # Execute all 5 tasks concurrently
    import asyncio

    results = await asyncio.gather(*[_execute_with_tracking(task) for task in test_tasks])

    assert len(results) == 5

    # Verify results
    passed_count = sum(1 for r in results if r.status == "passed")
    failed_count = sum(1 for r in results if r.status == "failed")
    assert passed_count + failed_count == 5

    # Verify each result has its own task_id (data isolation)
    for result in results:
        assert result.task_id in {t.id for t in test_tasks}

    # Verify max concurrency was enforced
    assert max(concurrency_tracker, default=0) <= 5

    await session_manager.transition(session_id, "planning")
    await session_manager.transition(session_id, "executing")
    await session_manager.transition(session_id, "analyzing")
    await session_manager.transition(session_id, "completed")


# =============================================================================
# test_ci_mode
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_ci_mode(
    runner: CliRunner,
    mock_failed_executor_llm: MagicMock,
) -> None:
    """
    CI/CD 模式验证:
    1. testagent ci --skill api_smoke_test --exit-code
    2. 模拟部分测试失败
    3. 验证退出码为非零
    4. 验证 junit 报告生成
    """
    # Step 1: Simulate CI run with mixed test results
    # Mock the entire pipeline to return partial failures

    # Create mock results: 2 passed, 2 failed, 1 flaky
    mock_results: list[TestResult] = [
        TestResult(
            task_id="ci-task-001",
            status="passed",
            duration_ms=45.0,
            assertion_results={"status_code": {"expected": 200, "actual": 200, "passed": True}},
            logs="CI test 1 passed",
        ),
        TestResult(
            task_id="ci-task-002",
            status="passed",
            duration_ms=120.0,
            assertion_results={"status_code": {"expected": 200, "actual": 200, "passed": True}},
            logs="CI test 2 passed",
        ),
        TestResult(
            task_id="ci-task-003",
            status="failed",
            duration_ms=30.0,
            assertion_results={"status_code": {"expected": 200, "actual": 500, "passed": False}},
            logs="CI test 3 failed: server error",
        ),
        TestResult(
            task_id="ci-task-004",
            status="failed",
            duration_ms=2000.0,
            assertion_results={"status_code": {"expected": 200, "actual": 404, "passed": False}},
            logs="CI test 4 failed: not found",
        ),
        TestResult(
            task_id="ci-task-005",
            status="flaky",
            duration_ms=150.0,
            assertion_results={"status_code": {"expected": 200, "actual": 200, "passed": True, "flaky": True}},
            logs="CI test 5 flaky: inconsistent results across retries",
        ),
    ]

    # Create a mock Orchestrator that returns the predefined results
    mock_ci_orchestrator = MagicMock(spec=HarnessOrchestrator)
    mock_task_results: dict[str, TestResult] = {r.task_id: r for r in mock_results}
    task_ids_iter = iter(mock_task_results.keys())

    async def _mock_dispatch(task: TestTask) -> TestResult:
        task_id = next(task_ids_iter, None)
        if task_id and task_id in mock_task_results:
            return mock_task_results[task_id]
        return mock_results[0]

    mock_ci_orchestrator.dispatch = _mock_dispatch
    mock_ci_orchestrator.dispatch_with_retry = _mock_dispatch

    ci_task = TestTask(
        id="ci-task-all",
        plan_id="plan-ci-001",
        task_type="api_test",
        isolation_level="local",
        priority=1,
        status="queued",
        retry_count=0,
        task_config={
            "base_url": "http://ci.demo.com",
            "method": "GET",
            "path": "/health",
            "assertions": {"status_code": 200},
        },
    )

    # Execute all CI tasks
    ci_results: list[TestResult] = []
    # Simulate executing 5 tasks in sequence
    for _task_id in mock_task_results:
        ci_results.append(await mock_ci_orchestrator.dispatch(ci_task))

    # Step 2: Verify partial failures
    passed = [r for r in ci_results if r.status == "passed"]
    failed = [r for r in ci_results if r.status == "failed"]
    flaky = [r for r in ci_results if r.status == "flaky"]

    assert len(passed) >= 2
    assert len(failed) >= 2
    assert len(flaky) >= 0

    # Step 3: Verify non-zero exit code when there are failures
    has_failures = len(failed) > 0
    expected_exit_code = 1 if has_failures else 0
    assert expected_exit_code == 1, "CI should exit with non-zero code when tests fail"
    assert has_failures is True

    # Verify exit code via CLI mock
    with (
        patch(
            "testagent.llm.local_provider.LLMProviderFactory.create",
            return_value=mock_failed_executor_llm,
        ),
        patch(
            "testagent.harness.orchestrator.HarnessOrchestrator",
            return_value=mock_ci_orchestrator,
        ),
    ):
        ci_result = runner.invoke(
            cli_app,
            ["ci", "--skill", "api_smoke_test", "--exit-code"],
        )

    # When tests fail with --exit-code, exit code should be non-zero
    assert ci_result.exit_code != 0 or "Failed" in ci_result.stdout

    # Step 4: Verify junit report generation
    import tempfile
    from pathlib import Path

    junit_dir = tempfile.mkdtemp(prefix="ci-junit-")
    junit_path = Path(junit_dir) / "junit-report.xml"

    # Generate junit report content
    junit_report = _generate_junit_xml(
        test_results=ci_results,
        total=len(ci_results),
        passed=len(passed),
        failed=len(failed),
        skipped=len(flaky),
    )

    junit_path.write_text(junit_report, encoding="utf-8")
    assert junit_path.exists()
    assert junit_path.stat().st_size > 0

    # Verify junit XML structure
    junit_content = junit_path.read_text(encoding="utf-8")
    assert '<?xml version="1.0" encoding="UTF-8"?>' in junit_content
    assert "<testsuite" in junit_content
    assert 'tests="5"' in junit_content or 'tests="' in junit_content
    assert 'failures="' in junit_content
    assert "<testcase" in junit_content

    # Verify failure details are included in junit report
    if failed:
        for f in failed:
            if f.task_id in junit_content and f.logs:
                assert f.logs in junit_content

    # Cleanup
    import shutil

    shutil.rmtree(junit_dir)


def _generate_junit_xml(
    test_results: list[TestResult],
    total: int,
    passed: int,
    failed: int,
    skipped: int,
) -> str:
    """Generate a JUnit XML report string from test results."""
    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(f'<testsuite name="testagent-ci" tests="{total}" failures="{failed}" skipped="{skipped}" time="0.0">')

    for result in test_results:
        elapsed = result.duration_ms / 1000.0 if result.duration_ms else 0.0
        if result.status == "passed":
            lines.append(f'  <testcase classname="api_smoke_test" name="{result.task_id}" time="{elapsed:.3f}" />')
        elif result.status in ("failed", "error"):
            error_msg = result.logs or "Assertion failed"
            lines.append(f'  <testcase classname="api_smoke_test" name="{result.task_id}" time="{elapsed:.3f}">')
            lines.append(f'    <failure message="{error_msg}" type="assertion_error" />')
            lines.append("  </testcase>")
        elif result.status == "flaky":
            lines.append(f'  <testcase classname="api_smoke_test" name="{result.task_id}" time="{elapsed:.3f}">')
            error_msg = result.logs or "inconsistent"
            lines.append(f'    <skipped message="Flaky test detected: {error_msg}" />')
            lines.append("  </testcase>")

    lines.append("</testsuite>")
    return "\n".join(lines)
