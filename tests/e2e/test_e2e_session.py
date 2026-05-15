from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from testagent.agent.analyzer import AnalyzerAgent
from testagent.agent.context import ContextAssembler
from testagent.agent.executor import ExecutorAgent
from testagent.agent.planner import PlannerAgent
from testagent.cli.main import app as cli_app
from testagent.config.settings import get_settings
from testagent.gateway.middleware import register_error_handlers
from testagent.gateway.router import router, set_session_manager
from testagent.gateway.session import SessionManager

if TYPE_CHECKING:
    from unittest.mock import MagicMock


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
    app.include_router(router)
    return app


# =============================================================================
# test_e2e_api_smoke_session
# =============================================================================


@pytest.mark.asyncio
async def test_e2e_api_smoke_session(
    mock_llm_provider: MagicMock,
    session_manager: SessionManager,
    api_app: FastAPI,
) -> None:
    """
    End-to-end session lifecycle verification:
    1. Create session via SessionManager -> status=pending
    2. PlannerAgent generates test plan -> status=planning
    3. ExecutorAgent executes at least 1 API test -> status=executing
    4. AnalyzerAgent analyzes results -> status=analyzing
    5. Verify session -> status=completed
    6. Get test report via API -> GET /api/v1/reports/{session_id}
    """
    settings = get_settings()
    context_assembler = ContextAssembler(settings=settings)

    planner = PlannerAgent(llm=mock_llm_provider, context_assembler=context_assembler)
    executor = ExecutorAgent(llm=mock_llm_provider, context_assembler=context_assembler)
    analyzer = AnalyzerAgent(llm=mock_llm_provider, context_assembler=context_assembler)

    # Step 1: Create session -> status=pending
    session = await session_manager.create_session(
        name="e2e-api-smoke",
        trigger_type="manual",
        input_context={"skill": "api_smoke_test", "env": "staging"},
    )
    session_id: str = session["id"]
    assert session["status"] == "pending"
    assert session["name"] == "e2e-api-smoke"

    # Step 2: PlannerAgent generates test plan -> status=planning
    await session_manager.transition(session_id, "planning")
    plan_result = await planner.execute(
        {
            "task_type": "plan",
            "requirement": "API smoke test for /health and /api/v1/sessions endpoints",
            "skill": "api_smoke_test",
        }
    )
    assert plan_result["agent_type"] == "planner"
    assert "plan" in plan_result
    plan = plan_result["plan"]
    assert isinstance(plan, dict)
    assert "strategy" in plan
    assert "test_tasks" in plan
    assert plan_result["message_count"] >= 2

    # Step 3: ExecutorAgent executes at least 1 API test -> status=executing
    await session_manager.transition(session_id, "executing")
    execute_result = await executor.execute(
        {
            "task_type": "api_test",
            "skill": "api_smoke_test",
            "task_config": {
                "method": "GET",
                "endpoint": "/health",
                "expected_status": 200,
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

    # Step 6: Get test report via API -> GET /api/v1/reports/{session_id}
    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/v1/reports/{session_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["session"]["id"] == session_id
        assert body["data"]["session"]["status"] == "completed"
        assert "summary" in body["data"]


# =============================================================================
# test_e2e_cli_run
# =============================================================================


def test_e2e_cli_run(
    runner: CliRunner,
    mock_llm_provider: MagicMock,
) -> None:
    """
    CLI end-to-end verification:
    1. testagent run --skill api_smoke_test --env staging
    2. Verify CLI output contains test results
    3. Verify exit code
    """
    with (
        patch(
            "testagent.llm.local_provider.LLMProviderFactory.create",
            return_value=mock_llm_provider,
        ),
    ):
        result = runner.invoke(
            cli_app,
            ["run", "--skill", "api_smoke_test", "--env", "staging"],
        )

    assert result.exit_code == 0, f"CLI exited with code {result.exit_code}: {result.stdout}"
    assert result.exit_code == 0
    assert "Summary" in result.stdout
    assert "Passed" in result.stdout or "passed" in result.stdout
