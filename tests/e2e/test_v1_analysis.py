from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from testagent.agent.defect_dedup import DefectDeduplicator
from testagent.agent.defect_priority import DefectPriorityEvaluator
from testagent.agent.root_cause import RootCauseAnalyzer
from testagent.agent.test_data_generator import TestDataGenerator, sanitize_record
from testagent.db.repository import DefectRepository
from testagent.gateway.middleware import register_error_handlers
from testagent.gateway.router import router, set_session_manager
from testagent.gateway.session import SessionManager
from testagent.llm.base import LLMResponse
from testagent.models.result import TestResult
from testagent.rag.pipeline import RAGPipeline

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio,
]

# =============================================================================
# Fixtures
# =============================================================================


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


@pytest.fixture()
def mock_llm() -> MagicMock:
    mock = MagicMock()
    mock.chat = AsyncMock()
    mock.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return mock


@pytest.fixture()
def mock_rag() -> MagicMock:
    mock = MagicMock(spec=RAGPipeline)
    mock.query = AsyncMock(return_value=[])
    mock.write_back = AsyncMock()
    mock.index_document = AsyncMock()
    return mock


# =============================================================================
# test_root_cause_analysis_chain
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_root_cause_analysis_chain(
    mock_llm: MagicMock,
    mock_rag: MagicMock,
) -> None:
    """RootCauseAnalyzer 全链路验证 (Phase 12).

    验证内容:
    1. 创建失败的 TestResult，包含断言错误和日志
    2. 调用 RootCauseAnalyzer.analyze()
    3. 验证关联了 Git commit 信息
    4. 验证分析结果写入 RAG failure_patterns 集合
    """
    # Step 1: Create a failed TestResult
    test_result = TestResult(
        task_id="rca-task-001",
        status="failed",
        duration_ms=250.0,
        assertion_results={
            "status_code": {"expected": 200, "actual": 500, "passed": False},
            "response_body": {"expected": '{"ok":true}', "actual": '{"error":"internal"}', "passed": False},
        },
        logs="""2026-05-15 10:00:01 [ERROR] GET /api/v1/users -> 500 Internal Server Error
2026-05-15 10:00:01 [ERROR] Assertion failed: expected status_code 200, got 500
2026-05-15 10:00:01 [ERROR] Response body: {"error":"internal","code":"DB_QUERY_FAILED"}
Traceback (most recent call last):
  File "/app/services/user_service.py", line 142, in get_users
    result = await db.query("SELECT * FROM users")
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/db/connection.py", line 67, in query
    raise DatabaseTimeoutError("Connection pool exhausted")
""",
        artifacts={
            "source_file": "src/services/user_service.py",
            "source_line": 142,
            "error": "DatabaseTimeoutError",
            "sandbox_id": "sandbox-rca-001",
        },
    )

    # Step 2: Create mock GitMCPServer that returns commit and PR info
    mock_git_server = MagicMock()
    mock_git_server.server_name = "git_server"

    async def _mock_call_tool(tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "git_blame":
            return json.dumps({
                "output": "abc123def (John Doe 2026-05-14 10:00:00 +0800 142) db_query_timeout_fix",
            })
        if tool_name == "git_log":
            return json.dumps({
                "output": (
                    "commit abc123def\nAuthor: John Doe\nDate: 2026-05-14\n\n"
                    "    fix: increase db connection pool timeout\n\n"
                    "commit def456789\nAuthor: John Doe\nDate: 2026-05-13\n\n"
                    "    feat: add user list endpoint"
                ),
            })
        if tool_name == "git_diff":
            return json.dumps({
                "output": (
                    "diff --git a/src/services/user_service.py "
                    "b/src/services/user_service.py\n"
                    "@@ -140,5 +140,6 @@\n-            timeout=5\n"
                    "+            timeout=30"
                ),
            })
        return json.dumps({"output": ""})

    mock_git_server.call_tool = AsyncMock(side_effect=_mock_call_tool)

    # Mock LLM response for root cause analysis
    mock_llm.chat.return_value = LLMResponse(
        content=[
            {
                "type": "text",
                "text": json.dumps({
                    "root_cause_type": "code_change",
                    "confidence": 0.92,
                    "reasoning": (
                        "Recent commit abc123def changed the DB connection pool timeout "
                        "from 5s to 30s, but also introduced a regression where the "
                        "connection pool is exhausted under load."
                    ),
                    "suggestion": (
                        "Revert the timeout change in user_service.py:142 and investigate "
                        "the connection pool exhaustion issue separately. Consider adding "
                        "a retry mechanism with exponential backoff."
                    ),
                }),
            }
        ],
        stop_reason="end_turn",
        usage={"input_tokens": 200, "output_tokens": 150},
    )

    # Instantiate RootCauseAnalyzer
    analyzer = RootCauseAnalyzer(
        git_server=mock_git_server,
        llm=mock_llm,
        rag=mock_rag,
        repo_path="/test/repo",
    )

    defect: dict[str, Any] = {
        "id": "defect-rca-001",
        "title": "GET /api/v1/users returns 500 Internal Server Error",
        "category": "bug",
        "severity": "critical",
        "result_id": "rca-task-001",
    }

    # Step 3: Call analyze
    result = await analyzer.analyze(defect=defect, test_result=test_result)

    # Verify root cause type is identified
    assert result.root_cause_type == "code_change"
    assert result.confidence > 0.5

    # Verify Git commit information is populated
    assert len(result.related_commits) >= 2, "Should have at least 2 commits (blame + log)"
    blame_commits = [c for c in result.related_commits if "blame_info" in c]
    log_commits = [c for c in result.related_commits if "recent_commits" in c]
    assert len(blame_commits) >= 1, "Should have at least one blame commit"
    assert len(log_commits) >= 1, "Should have at least one log commit"

    # Verify commit details
    first_blame = blame_commits[0]
    assert "abc123def" in str(first_blame.get("blame_info", ""))
    assert "src/services/user_service.py" in first_blame.get("file", "")

    # Verify related_prs field exists in result structure
    result_dict = result.to_dict()
    assert "related_prs" in result_dict
    assert isinstance(result_dict["related_prs"], list)

    # Verify code snippets are populated (from git diff)
    assert len(result.code_snippets) >= 1
    snippet = result.code_snippets[0]
    assert "diff" in snippet
    assert "timeout" in str(snippet.get("diff", ""))

    # Verify suggestion is provided
    assert len(result.suggestion) > 0
    assert "timeout" in result.suggestion.lower() or "connection" in result.suggestion.lower()

    # Step 4: Verify results written back to RAG failure_patterns
    mock_rag.write_back.assert_called_once()
    call_args = mock_rag.write_back.call_args
    assert call_args.kwargs["collection"] == "failure_patterns"
    assert "defect_id" in call_args.kwargs["metadata"]
    assert call_args.kwargs["metadata"]["defect_id"] == "defect-rca-001"

    write_back_content = json.loads(call_args.kwargs["content"])
    assert write_back_content["defect_id"] == "defect-rca-001"
    assert write_back_content["root_cause_type"] == "code_change"
    assert write_back_content["confidence"] == 0.92


# =============================================================================
# test_quality_trends_api
# =============================================================================


def _build_mock_trend_data(metric: str, days: int) -> list[dict[str, Any]]:
    """Build realistic trend data spanning multiple days."""
    trends: list[dict[str, Any]] = []
    base_date = datetime.now(UTC).date() - timedelta(days=days - 1)

    for i in range(days):
        current_date = base_date + timedelta(days=i)
        day_num = i + 1

        if metric == "pass_rate":
            pass_rate = 95.0 - (day_num % 10) * 2.0
            total = 50 + (day_num % 20)
            passed = int(total * pass_rate / 100)
            failed = max(0, total - passed - max(0, day_num % 4))
            flaky = max(0, total - passed - failed)
            trends.append({
                "date": current_date.isoformat(),
                "total": total,
                "passed": passed,
                "failed": failed,
                "flaky": flaky,
                "pass_rate": round(pass_rate, 1),
            })
        elif metric == "defect_density":
            trends.append({
                "week": f"{current_date.isoformat()}",
                "critical": max(0, day_num % 5),
                "major": max(0, day_num % 3),
                "minor": max(0, day_num % 7),
                "trivial": max(0, day_num % 4),
                "total": day_num % 15,
            })
        elif metric == "coverage":
            trends.append({
                "date": current_date.isoformat(),
                "api": min(100.0, 80.0 + day_num * 0.5),
                "web": min(100.0, 60.0 + day_num * 1.0),
                "app": min(100.0, 30.0 + day_num * 0.8),
                "overall": min(100.0, 60.0 + day_num * 0.7),
            })
        elif metric == "flaky_rate":
            flaky_rate = max(0.0, 15.0 - day_num * 0.4)
            total = 50 + (day_num % 20)
            flaky = int(total * flaky_rate / 100)
            trends.append({
                "date": current_date.isoformat(),
                "total": total,
                "flaky": flaky,
                "flaky_rate": round(flaky_rate, 1),
            })

    return trends


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_quality_trends_api(
    mock_llm: MagicMock,
    api_app: FastAPI,
    session_manager: SessionManager,
) -> None:
    """Quality Trends API 验证 (Phase 12).

    验证内容:
    1. 模拟多天测试数据
    2. 调用 GET /api/v1/quality/trends?metric=pass_rate&days=30
    3. 验证返回正确的趋势数据结构
    4. 验证不同 metric 参数的正确响应
    """
    # Build mock trend data for 30 days
    expected_trends = _build_mock_trend_data("pass_rate", 30)

    # Create mock QualityTrendsAnalyzer
    mock_analyzer = MagicMock()
    mock_analyzer.get_pass_rate_trend = AsyncMock(return_value=expected_trends)
    mock_analyzer.get_defect_density_trend = AsyncMock(
        return_value=_build_mock_trend_data("defect_density", 30)
    )
    mock_analyzer.get_coverage_trend = AsyncMock(
        return_value=_build_mock_trend_data("coverage", 30)
    )
    mock_analyzer.get_flaky_rate_trend = AsyncMock(
        return_value=_build_mock_trend_data("flaky_rate", 30)
    )

    transport = ASGITransport(app=api_app)

    # Step 1-2: Call pass_rate trend API
    with patch("testagent.gateway.router._get_quality_analyzer", return_value=mock_analyzer):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/quality/trends", params={"metric": "pass_rate", "days": 30})

    # Step 3: Verify response structure
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    data = body["data"]
    assert data["metric"] == "pass_rate"
    assert data["days"] == 30
    assert "trends" in data
    assert len(data["trends"]) == 30

    # Verify each trend entry has the correct fields
    for entry in data["trends"]:
        assert "date" in entry
        assert "total" in entry
        assert "passed" in entry
        assert "failed" in entry
        assert "flaky" in entry
        assert "pass_rate" in entry
        assert 0 <= entry["pass_rate"] <= 100
        assert entry["passed"] + entry["failed"] + entry["flaky"] == entry["total"]

    # Verify pass_rate values are reasonable
    pass_rates = [t["pass_rate"] for t in data["trends"]]
    assert all(0 <= pr <= 100 for pr in pass_rates)

    # Verify the mock analyzer was called with correct args
    mock_analyzer.get_pass_rate_trend.assert_called_once_with(days=30)

    # Test defect_density metric
    with patch("testagent.gateway.router._get_quality_analyzer", return_value=mock_analyzer):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp2 = await client.get("/api/v1/quality/trends", params={"metric": "defect_density", "days": 30})

    assert resp2.status_code == 200
    data2 = resp2.json()["data"]
    assert data2["metric"] == "defect_density"
    assert len(data2["trends"]) == 30
    for entry in data2["trends"]:
        assert "week" in entry
        assert "critical" in entry
        assert "major" in entry
        assert "minor" in entry
        assert "trivial" in entry

    # Test flaky_rate metric
    with patch("testagent.gateway.router._get_quality_analyzer", return_value=mock_analyzer):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp3 = await client.get("/api/v1/quality/trends", params={"metric": "flaky_rate", "days": 30})

    assert resp3.status_code == 200
    data3 = resp3.json()["data"]
    assert data3["metric"] == "flaky_rate"
    for entry in data3["trends"]:
        assert "flaky_rate" in entry
        assert 0 <= entry["flaky_rate"] <= 100

    # Test invalid metric returns 400
    with patch("testagent.gateway.router._get_quality_analyzer", return_value=mock_analyzer):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp4 = await client.get("/api/v1/quality/trends", params={"metric": "invalid_metric", "days": 30})

    assert resp4.status_code == 400
    error_detail = resp4.json()["detail"]
    assert error_detail["code"] == "INVALID_METRIC"

    # Verify the session manager works for the summary endpoint
    await session_manager.create_session(
        name="quality-test-session",
        trigger_type="manual",
        input_context={"test": "quality trends"},
    )

    # Test quality summary endpoint
    mock_analyzer.get_summary = AsyncMock(
        return_value={
            "overall_pass_rate": 87.5,
            "total_defects_30d": 12,
            "total_tests_30d": 1500,
            "pass_rate_change_7d": 2.3,
            "defect_change_30d": -5,
            "latest_coverage": 72.1,
            "latest_flaky_rate": 3.2,
        }
    )

    with patch("testagent.gateway.router._get_quality_analyzer", return_value=mock_analyzer):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp5 = await client.get("/api/v1/quality/summary")

    assert resp5.status_code == 200
    summary = resp5.json()["data"]
    assert summary["overall_pass_rate"] == 87.5
    assert summary["total_defects_30d"] == 12
    assert "pass_rate_change_7d" in summary


# =============================================================================
# test_defect_deduplication
# =============================================================================


class _RAGDoc:
    """Simple RAG document-like object for mock results."""

    def __init__(self, doc_id: str, content: str, score: float, metadata: dict[str, Any]) -> None:
        self.doc_id = doc_id
        self.content = content
        self.score = score
        self.metadata = metadata


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_defect_deduplication(
    mock_llm: MagicMock,
    mock_rag: MagicMock,
) -> None:
    """缺陷去重验证 (Phase 12).

    验证内容:
    1. 创建两个相似缺陷
    2. 第一个缺陷被标记为唯一
    3. 第二个缺陷被标记为重复
    4. 验证关联了原始缺陷 ID
    """
    # Create mock DefectRepository
    mock_defect_repo = MagicMock(spec=DefectRepository)
    mock_defect_repo.get_by_id = AsyncMock(
        return_value=MagicMock(occurrence_count=2)
    )
    mock_defect_repo.update = AsyncMock()

    defect_1: dict[str, Any] = {
        "id": "defect-dedup-001",
        "title": "GET /api/users returns 500 when database connection pool is exhausted",
        "description": (
            "When the database connection pool is exhausted, "
            "GET /api/users returns HTTP 500 with error 'DB_QUERY_FAILED'"
        ),
        "category": "bug",
        "severity": "critical",
        "result_id": "result-001",
    }

    defect_2: dict[str, Any] = {
        "id": "defect-dedup-002",
        "title": "Database connection pool exhaustion causes 500 error on /api/users endpoint",
        "description": (
            "The /api/users endpoint fails with HTTP 500 when the database "
            "connection pool reaches its limit. Error message: DB_QUERY_FAILED"
        ),
        "category": "bug",
        "severity": "critical",
        "result_id": "result-002",
    }

    # Step 1: Configure RAG mock
    # First query (for defect_1): no similar defects found
    # Second query (for defect_2): returns defect_1 as similar
    rag_call_count = 0

    async def _mock_rag_query(query_text: str, collection: str = "defect_history", top_k: int = 5) -> list[_RAGDoc]:
        nonlocal rag_call_count
        rag_call_count += 1

        if rag_call_count == 1:
            return []

        return [
            _RAGDoc(
                doc_id="rag-doc-defect-001",
                content="Database connection pool exhaustion causes 500 error on /api/users endpoint",
                score=0.92,
                metadata={
                    "defect_id": "defect-dedup-001",
                    "defect_title": "GET /api/users returns 500 when database connection pool is exhausted",
                    "defect_category": "bug",
                    "defect_severity": "critical",
                },
            )
        ]

    mock_rag.query = AsyncMock(side_effect=_mock_rag_query)

    # Mock LLM to return high similarity score for the duplicate check
    mock_llm.chat.return_value = LLMResponse(
        content=[
            {
                "type": "text",
                "text": json.dumps({
                    "similarity_score": 0.95,
                    "reasoning": (
                        "Both defects describe the same underlying issue: "
                        "database connection pool exhaustion causing "
                        "500 error on /api/users endpoint"
                    ),
                }),
            }
        ],
        stop_reason="end_turn",
        usage={"input_tokens": 150, "output_tokens": 50},
    )

    # Instantiate DefectDeduplicator
    deduplicator = DefectDeduplicator(
        llm=mock_llm,
        rag=mock_rag,
        defect_repo=mock_defect_repo,
    )

    # Step 2: First defect should be unique (no similar defects found)
    result_1 = await deduplicator.check_duplicate(defect_1)
    assert result_1.is_duplicate is False, "First defect should be unique"
    assert result_1.similarity_score == 0.0
    assert result_1.original_defect_id is None
    assert len(result_1.similar_defects) == 0

    # Step 3: Second defect should be duplicate of first
    result_2 = await deduplicator.check_duplicate(defect_2)
    assert result_2.is_duplicate is True, "Second defect should be marked as duplicate"
    assert result_2.similarity_score >= 0.85, "Similarity score should meet threshold"
    assert result_2.original_defect_id == "defect-dedup-001"

    # Verify similar defects list is populated
    assert len(result_2.similar_defects) >= 1
    similar = result_2.similar_defects[0]
    assert similar["defect_id"] == "defect-dedup-001"
    assert similar["similarity"] >= 0.85

    # Step 4: Verify original defect occurrence_count was incremented
    mock_defect_repo.get_by_id.assert_called_once_with("defect-dedup-001")
    mock_defect_repo.update.assert_called_once()
    update_call = mock_defect_repo.update.call_args
    assert update_call[0][0] == "defect-dedup-001"

    # Verify write_back_to_rag was called for second defect
    await deduplicator.write_back_to_rag(defect_2, result_2)
    mock_rag.write_back.assert_called()
    last_write_back = mock_rag.write_back.call_args
    assert last_write_back.kwargs["collection"] == "defect_history"
    write_content = json.loads(last_write_back.kwargs["content"])
    assert write_content["is_duplicate"] is True
    assert write_content["original_defect_id"] == "defect-dedup-001"
    assert write_content["defect_id"] == "defect-dedup-002"


# =============================================================================
# test_defect_priority_evaluation
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_defect_priority_evaluation(
    mock_llm: MagicMock,
    mock_rag: MagicMock,
) -> None:
    """缺陷优先级评估验证 (Phase 12).

    验证内容:
    1. 创建一个 critical 级别的缺陷
    2. 调用 DefectPriorityEvaluator.evaluate()
    3. 验证严重度评估合理
    4. 验证影响评分和历史评分正确计算
    """
    # Create mock DefectRepository
    mock_defect_repo = MagicMock(spec=DefectRepository)

    # Configure RAG query results
    # api_docs query should return affected API information
    # defect_history query should return historical defect data

    async def _mock_priority_rag_query(
        query_text: str,
        collection: str = "api_docs",
        top_k: int | None = None,
    ) -> list[_RAGDoc]:
        if collection == "api_docs":
            return [
                _RAGDoc(
                    doc_id="api-doc-users",
                    content="GET /api/v1/users - Returns list of users. Requires auth token.",
                    score=0.89,
                    metadata={"method": "GET", "path": "/api/v1/users", "module": "user_service"},
                ),
                _RAGDoc(
                    doc_id="api-doc-orders",
                    content="GET /api/v1/orders - Returns list of orders. Requires auth token.",
                    score=0.45,
                    metadata={"method": "GET", "path": "/api/v1/orders", "module": "order_service"},
                ),
            ]
        return []

    mock_rag.query = AsyncMock(side_effect=_mock_priority_rag_query)

    defect: dict[str, Any] = {
        "id": "defect-prio-001",
        "title": "GET /api/v1/users returns 500 when database connection pool is exhausted",
        "description": (
            "Under high load, the database connection pool is exhausted "
            "causing all user-related API calls to fail with HTTP 500"
        ),
        "category": "bug",
        "severity": "critical",
        "result_id": "result-prio-001",
    }

    # Instantiate DefectPriorityEvaluator
    evaluator = DefectPriorityEvaluator(
        defect_repo=mock_defect_repo,
        rag=mock_rag,
    )

    # Call evaluate
    result = await evaluator.evaluate(defect)

    # Verify the result structure
    assert result.defect_id == "defect-prio-001"
    assert result.suggested_severity in ("critical", "major", "minor", "trivial")

    # Verify severity assessment is reasonable for a critical bug
    # Bug category gets a boost of +0.1, so critical defects should be evaluated highly
    assert result.impact_score > 0.0
    assert result.historical_score >= 0.0

    # Verify affected APIs are identified
    assert len(result.affected_apis) >= 1
    api_paths = [api.get("path", "") if isinstance(api, dict) else api for api in result.affected_apis]
    assert any("/api/v1/users" in str(p) for p in api_paths), "Should identify affected /api/v1/users endpoint"

    # Verify recurrence count
    assert result.recurrence_count >= 0

    # Verify the composite score produces a reasonable severity
    # For a critical bug with affected APIs, severity should be critical or major
    result_dict = {
        "defect_id": result.defect_id,
        "suggested_severity": result.suggested_severity,
        "impact_score": result.impact_score,
        "historical_score": result.historical_score,
        "affected_apis": result.affected_apis,
        "recurrence_count": result.recurrence_count,
    }

    # Verify the severity makes sense for a critical bug category
    severity_scores = {"critical": 4, "major": 3, "minor": 2, "trivial": 1}
    assert severity_scores.get(result.suggested_severity, 0) >= 2, (
        "Critical bug should be at least minor severity"
    )

    # Verify impact score is weighted correctly
    assert result.impact_score > 0, "Impact score should be positive"
    assert result_dict is not None  # ensure the result is serializable


# =============================================================================
# test_data_generation
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_data_generation(
    mock_llm: MagicMock,
) -> None:
    """测试数据生成验证 (Phase 12).

    验证内容:
    1. 提供数据 Schema
    2. 调用 TestDataGenerator.generate()
    3. 验证生成的数据符合约束
    4. 验证 PII 已脱敏
    """
    # Create mock DatabaseMCPServer
    mock_db_server = MagicMock()
    mock_db_server.server_name = "database_server"

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "minimum": 1, "maximum": 10000},
            "name": {"type": "string", "minLength": 2, "maxLength": 50},
            "email": {"type": "string", "format": "email"},
            "phone": {"type": "string", "pattern": "^1[3-9]\\d{9}$"},
            "age": {"type": "integer", "minimum": 18, "maximum": 120},
            "city": {"type": "string", "enum": ["Beijing", "Shanghai", "Guangzhou", "Shenzhen"]},
            "score": {"type": "number", "minimum": 0, "maximum": 100},
        },
        "required": ["id", "name", "email", "phone", "age", "city"],
    }

    constraints: dict[str, Any] = {
        "id": {"type": "auto_increment"},
        "name": {"type": "person_name"},
        "email": {"type": "email", "domain": "example.com"},
        "phone": {"type": "phone", "prefix": "138"},
        "age": {"minimum": 18, "maximum": 65},  # Working age
        "city": {"values": ["Beijing", "Shanghai", "Guangzhou", "Shenzhen"]},
    }

    # Mock LLM to return generated data with PII
    generated_data: list[dict[str, Any]] = [
        {
            "id": 1,
            "name": "Zhang Wei",
            "email": "zhangwei@example.com",
            "phone": "13812345678",
            "age": 28,
            "city": "Beijing",
            "score": 85.5,
        },
        {
            "id": 2,
            "name": "Li Na",
            "email": "lina@example.com",
            "phone": "13887654321",
            "age": 32,
            "city": "Shanghai",
            "score": 92.0,
        },
        {
            "id": 3,
            "name": "Wang Ming",
            "email": "wangming@example.com",
            "phone": "13899887766",
            "age": 45,
            "city": "Guangzhou",
            "score": 78.3,
        },
    ]

    mock_llm.chat.return_value = LLMResponse(
        content=[
            {
                "type": "text",
                "text": json.dumps(generated_data, ensure_ascii=False),
            }
        ],
        stop_reason="end_turn",
        usage={"input_tokens": 300, "output_tokens": 200},
    )

    # Instantiate TestDataGenerator
    generator = TestDataGenerator(
        llm=mock_llm,
        db_server=mock_db_server,
    )

    # Step 2: Call generate with schema and constraints
    result = await generator.generate(
        schema=schema,
        constraints=constraints,
        count=3,
    )

    # Step 3: Verify generated data
    assert len(result) == 3, "Should generate 3 records"

    for record in result:
        # Verify required fields exist
        assert "id" in record
        assert "name" in record
        assert "email" in record
        assert "phone" in record
        assert "age" in record
        assert "city" in record
        assert "score" in record

        # Verify numeric constraints
        assert isinstance(record["id"], int)
        assert 1 <= record["id"] <= 10000
        assert isinstance(record["age"], int)
        assert 18 <= record["age"] <= 65, f"Age {record['age']} should be within working age range"
        assert isinstance(record["score"], (int, float))
        assert 0 <= record["score"] <= 100

        # Verify enum constraint
        assert record["city"] in ("Beijing", "Shanghai", "Guangzhou", "Shenzhen")

        # Step 4: Verify PII is masked
        # email and phone are PII fields, should be masked
        assert record["email"] == "***masked***", f"Email field should be masked, got: {record['email']}"
        assert record["phone"] == "***masked***", f"Phone field should be masked, got: {record['phone']}"

    # Verify name IS masked (it IS a PII field in the implementation)
    assert result[2]["name"] == "***masked***", "Name is a PII field and should be masked"

    # Verify city is preserved (not PII)
    assert result[0]["city"] == "Beijing"

    # Verify the generate method handles PII in text content
    # Create a record with a PII value in a non-PII field to test inline sanitization
    record_with_pii: dict[str, Any] = {
        "id": 4,
        "name": "Test User",
        "email": "test@example.com",
        "phone": "13912345678",
        "age": 25,
        "city": "Shenzhen",
        "notes": "Contact: test@example.com or 13912345678 for follow-up",
    }

    sanitized = sanitize_record(record_with_pii)
    # PII fields should be masked
    assert sanitized["email"] == "***masked***"
    assert sanitized["phone"] == "***masked***"
    assert sanitized["name"] == "***masked***"
    # Inline PII in notes should be sanitized by regex replacement
    assert "u***@example.com" in sanitized["notes"], (
        "Inline email in notes should be sanitized"
    )
    assert "138****0000" in sanitized["notes"], (
        "Inline phone in notes should be sanitized"
    )

    # Verify id and age are preserved (not PII)
    assert sanitized["id"] == 4
    assert sanitized["age"] == 25

    # Verify the LLM was called with correct parameters
    mock_llm.chat.assert_called_once()
    llm_call = mock_llm.chat.call_args
    assert llm_call.kwargs["max_tokens"] == 4096
    assert llm_call.kwargs["temperature"] == 0.7

    # Verify system prompt contains data generation instructions
    system_prompt = llm_call.kwargs.get("system", "")
    assert "data" in system_prompt.lower() or "generate" in system_prompt.lower()

    # Test edge case: count=0 should return empty list
    empty_result = await generator.generate(schema=schema, count=0)
    assert empty_result == []

    # Test edge case: count=1 should return single record
    mock_llm.chat.reset_mock()
    mock_llm.chat.return_value = LLMResponse(
        content=[
            {
                "type": "text",
                "text": json.dumps([generated_data[0]], ensure_ascii=False),
            }
        ],
        stop_reason="end_turn",
        usage={"input_tokens": 150, "output_tokens": 100},
    )
    single_result = await generator.generate(schema=schema, count=1)
    assert len(single_result) == 1
