from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from testagent.db.repository import DefectRepository, SessionRepository, TaskRepository
from testagent.models.defect import Defect
from testagent.models.mcp_config import MCPConfig
from testagent.models.plan import TestPlan, TestTask
from testagent.models.result import TestResult
from testagent.models.session import TestSession
from testagent.models.skill import SkillDefinition
from tests.conftest import PG_URL, requires_pg


async def _seed_all_models(session: AsyncSession) -> dict[str, str]:
    s = TestSession(
        name="pg-full-test",
        status="completed",
        trigger_type="ci_push",
        input_context={"url": "https://example.com", "env": "staging"},
    )
    session.add(s)
    await session.flush()

    p = TestPlan(
        session_id=s.id,
        strategy_type="regression",
        plan_json={"steps": ["step1", "step2"], "estimated_duration": 300},
        skill_ref="api_smoke_test",
        total_tasks=2,
        completed_tasks=1,
    )
    session.add(p)
    await session.flush()

    t1 = TestTask(
        plan_id=p.id,
        task_type="api_test",
        task_config={"url": "/api/orders", "method": "POST", "module": "orders"},
        isolation_level="docker",
        priority=10,
        status="passed",
        retry_count=0,
    )
    t2 = TestTask(
        plan_id=p.id,
        task_type="web_test",
        task_config={"url": "/web/products", "method": "GET", "module": "products"},
        isolation_level="docker",
        priority=5,
        status="failed",
        retry_count=4,
    )
    session.add(t1)
    session.add(t2)
    await session.flush()

    r1 = TestResult(
        task_id=t1.id,
        status="passed",
        duration_ms=150.0,
        assertion_results={"total": 5, "passed": 5, "failed": 0},
        logs="All assertions passed",
    )
    r2 = TestResult(
        task_id=t2.id,
        status="failed",
        duration_ms=300.0,
        assertion_results={"total": 5, "passed": 3, "failed": 2},
        logs="Timeout on product list page",
    )
    session.add(r1)
    session.add(r2)
    await session.flush()

    d1 = Defect(
        result_id=r2.id,
        severity="critical",
        category="bug",
        title="Order creation returns 500",
        description="The order creation endpoint returns 500 when payload is missing on PostgreSQL database",
        root_cause={"module": "orders", "error_type": "NullPointer", "layer": "service"},
    )
    d2 = Defect(
        result_id=r2.id,
        severity="major",
        category="flaky",
        title="Product list intermittent timeout",
        description="Product list page has intermittent timeout on slow connections to PostgreSQL",
        root_cause={"module": "products", "error_type": "Timeout", "layer": "network"},
    )
    session.add(d1)
    session.add(d2)
    await session.flush()

    sk = SkillDefinition(
        name="api_smoke_test",
        version="1.0.0",
        description="API smoke test skill for PostgreSQL verification",
        trigger_pattern="smoke.*api",
        required_mcp_servers=["api_server"],
        required_rag_collections=["api_docs"],
        body="# API Smoke Test\nTest the core API endpoints",
        tags=["api", "smoke", "mvp"],
    )
    session.add(sk)
    await session.flush()

    mc = MCPConfig(
        session_id=s.id,
        server_name="api-server-pg",
        command="python -m testagent.mcp_servers.api_server",
        args={"port": 8080, "host": "0.0.0.0"},
        env={"LOG_LEVEL": "debug", "TIMEOUT": "30"},
        enabled=True,
    )
    session.add(mc)
    await session.commit()

    return {
        "session_id": s.id,
        "plan_id": p.id,
        "task_id": t1.id,
        "task2_id": t2.id,
        "result_id": r1.id,
        "result2_id": r2.id,
        "defect_id": d1.id,
        "defect2_id": d2.id,
        "skill_id": sk.id,
        "mcp_config_id": mc.id,
    }


@requires_pg
class TestAllModelsCRUDOnPostgreSQL:
    async def test_session_create_read(self, postgres_db_session: AsyncSession) -> None:
        s = TestSession(name="crud-session", status="pending", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(TestSession, s.id)
        assert loaded is not None
        assert loaded.name == "crud-session"
        assert loaded.status == "pending"

    async def test_session_update(self, postgres_db_session: AsyncSession) -> None:
        s = TestSession(name="crud-update", status="pending", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        s.status = "completed"
        s.name = "crud-updated"
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(TestSession, s.id)
        assert loaded is not None
        assert loaded.status == "completed"
        assert loaded.name == "crud-updated"

    async def test_session_delete(self, postgres_db_session: AsyncSession) -> None:
        s = TestSession(name="crud-delete", status="pending", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        await postgres_db_session.delete(s)
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(TestSession, s.id)
        assert loaded is None

    async def test_plan_create_with_jsonb(self, postgres_db_session: AsyncSession) -> None:
        s = TestSession(name="plan-session", status="planning", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": ["init", "execute", "verify"], "metadata": {"env": "staging"}},
        )
        postgres_db_session.add(p)
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(TestPlan, p.id)
        assert loaded is not None
        assert loaded.plan_json == {"steps": ["init", "execute", "verify"], "metadata": {"env": "staging"}}

    async def test_plan_update_strategy(self, postgres_db_session: AsyncSession) -> None:
        s = TestSession(name="plan-update-session", status="planning", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        p = TestPlan(session_id=s.id, strategy_type="smoke", plan_json={"steps": []})
        postgres_db_session.add(p)
        await postgres_db_session.flush()
        p.strategy_type = "regression"
        p.total_tasks = 10
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(TestPlan, p.id)
        assert loaded is not None
        assert loaded.strategy_type == "regression"
        assert loaded.total_tasks == 10

    async def test_task_create_with_config_jsonb(self, postgres_db_session: AsyncSession) -> None:
        s = TestSession(name="task-session", status="executing", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        p = TestPlan(session_id=s.id, strategy_type="smoke", plan_json={"steps": []})
        postgres_db_session.add(p)
        await postgres_db_session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/api/login", "method": "POST", "headers": {"Content-Type": "application/json"}},
        )
        postgres_db_session.add(t)
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(TestTask, t.id)
        assert loaded is not None
        assert loaded.task_config["url"] == "/api/login"
        headers = loaded.task_config["headers"]
        assert isinstance(headers, dict)
        assert headers["Content-Type"] == "application/json"

    async def test_task_update_priority_and_status(self, postgres_db_session: AsyncSession) -> None:
        s = TestSession(name="task-update", status="executing", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        p = TestPlan(session_id=s.id, strategy_type="smoke", plan_json={"steps": []})
        postgres_db_session.add(p)
        await postgres_db_session.flush()
        t = TestTask(plan_id=p.id, task_type="web_test", task_config={"url": "/home"})
        postgres_db_session.add(t)
        await postgres_db_session.flush()
        t.priority = 100
        t.status = "running"
        t.retry_count = 1
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(TestTask, t.id)
        assert loaded is not None
        assert loaded.priority == 100
        assert loaded.status == "running"
        assert loaded.retry_count == 1

    async def test_result_create_with_jsonb_assertions(self, postgres_db_session: AsyncSession) -> None:
        s = TestSession(name="result-session", status="executing", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        p = TestPlan(session_id=s.id, strategy_type="smoke", plan_json={"steps": []})
        postgres_db_session.add(p)
        await postgres_db_session.flush()
        t = TestTask(plan_id=p.id, task_type="api_test", task_config={"url": "/status"})
        postgres_db_session.add(t)
        await postgres_db_session.flush()
        r = TestResult(
            task_id=t.id,
            status="passed",
            duration_ms=42.5,
            assertion_results={
                "total": 3,
                "passed": 3,
                "failed": 0,
                "details": [{"name": "status_200", "passed": True}],
            },
            artifacts={"har": "s3://bucket/trace.har", "screenshot": "s3://bucket/ok.png"},
        )
        postgres_db_session.add(r)
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(TestResult, r.id)
        assert loaded is not None
        assert loaded.assertion_results is not None
        assert loaded.assertion_results["total"] == 3
        assert loaded.artifacts is not None
        assert loaded.artifacts["har"] == "s3://bucket/trace.har"

    async def test_result_update_status(self, postgres_db_session: AsyncSession) -> None:
        s = TestSession(name="result-update-sess", status="executing", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        p = TestPlan(session_id=s.id, strategy_type="smoke", plan_json={"steps": []})
        postgres_db_session.add(p)
        await postgres_db_session.flush()
        t = TestTask(plan_id=p.id, task_type="api_test", task_config={"url": "/data"})
        postgres_db_session.add(t)
        await postgres_db_session.flush()
        r = TestResult(task_id=t.id, status="failed", duration_ms=999.0)
        postgres_db_session.add(r)
        await postgres_db_session.flush()
        r.status = "flaky"
        r.duration_ms = 1200.0
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(TestResult, r.id)
        assert loaded is not None
        assert loaded.status == "flaky"
        assert loaded.duration_ms == 1200.0

    async def test_defect_create_with_jsonb_root_cause(self, postgres_db_session: AsyncSession) -> None:
        s = TestSession(name="defect-session", status="analyzing", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        p = TestPlan(session_id=s.id, strategy_type="smoke", plan_json={"steps": []})
        postgres_db_session.add(p)
        await postgres_db_session.flush()
        t = TestTask(plan_id=p.id, task_type="api_test", task_config={"url": "/error"})
        postgres_db_session.add(t)
        await postgres_db_session.flush()
        r = TestResult(task_id=t.id, status="failed")
        postgres_db_session.add(r)
        await postgres_db_session.flush()
        d = Defect(
            result_id=r.id,
            severity="critical",
            category="bug",
            title="Critical null pointer on /error",
            description="NullPointerException at ErrorHandler.process()",
            root_cause={
                "module": "core",
                "error_type": "NullPointerException",
                "layer": "service",
                "stack": "at ErrorHandler.process(ErrorHandler.java:42)",
            },
        )
        postgres_db_session.add(d)
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(Defect, d.id)
        assert loaded is not None
        assert loaded.root_cause is not None
        assert loaded.root_cause["module"] == "core"
        assert loaded.root_cause["error_type"] == "NullPointerException"

    async def test_defect_update_severity(self, postgres_db_session: AsyncSession) -> None:
        s = TestSession(name="defect-update-sess", status="analyzing", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        p = TestPlan(session_id=s.id, strategy_type="smoke", plan_json={"steps": []})
        postgres_db_session.add(p)
        await postgres_db_session.flush()
        t = TestTask(plan_id=p.id, task_type="api_test", task_config={"url": "/warn"})
        postgres_db_session.add(t)
        await postgres_db_session.flush()
        r = TestResult(task_id=t.id, status="failed")
        postgres_db_session.add(r)
        await postgres_db_session.flush()
        d = Defect(result_id=r.id, severity="minor", category="configuration", title="Config warning")
        postgres_db_session.add(d)
        await postgres_db_session.flush()
        d.severity = "major"
        d.status = "confirmed"
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(Defect, d.id)
        assert loaded is not None
        assert loaded.severity == "major"
        assert loaded.status == "confirmed"

    async def test_skill_create_with_all_json_fields(self, postgres_db_session: AsyncSession) -> None:
        sk = SkillDefinition(
            name="pg-crud-skill",
            version="2.0.0",
            description="PostgreSQL CRUD test skill",
            required_mcp_servers=["api_server", "playwright_server"],
            required_rag_collections=["api_docs", "locator_library"],
            tags=["v1", "pg", "crud"],
            updated_at=datetime.now(UTC),
        )
        postgres_db_session.add(sk)
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(SkillDefinition, sk.id)
        assert loaded is not None
        assert loaded.required_mcp_servers == ["api_server", "playwright_server"]  # type: ignore[comparison-overlap]
        assert loaded.required_rag_collections == ["api_docs", "locator_library"]  # type: ignore[comparison-overlap]
        assert loaded.tags == ["v1", "pg", "crud"]  # type: ignore[comparison-overlap]

    async def test_skill_update_body(self, postgres_db_session: AsyncSession) -> None:
        sk = SkillDefinition(name="pg-update-skill", version="1.0", description="Update test")
        postgres_db_session.add(sk)
        await postgres_db_session.flush()
        sk.body = "# Updated body"
        sk.version = "1.1"
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(SkillDefinition, sk.id)
        assert loaded is not None
        assert loaded.body == "# Updated body"
        assert loaded.version == "1.1"

    async def test_mcp_config_create_with_jsonb(self, postgres_db_session: AsyncSession) -> None:
        mc = MCPConfig(
            server_name="crud-mcp-server",
            command="python -m testserver",
            args={"port": 9000, "debug": True},
            env={"LOG_LEVEL": "info", "SECRET": "test123"},
            enabled=True,
        )
        postgres_db_session.add(mc)
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(MCPConfig, mc.id)
        assert loaded is not None
        assert loaded.args == {"port": 9000, "debug": True}
        assert loaded.env == {"LOG_LEVEL": "info", "SECRET": "test123"}

    async def test_mcp_config_update_enabled(self, postgres_db_session: AsyncSession) -> None:
        mc = MCPConfig(server_name="toggle-mcp-server", command="echo", enabled=True)
        postgres_db_session.add(mc)
        await postgres_db_session.flush()
        mc.enabled = False
        await postgres_db_session.flush()
        loaded = await postgres_db_session.get(MCPConfig, mc.id)
        assert loaded is not None
        assert loaded.enabled is False


@requires_pg
class TestJSONBQueryPerformance:
    async def test_explain_analyze_uses_gin_index(self, postgres_db_session: AsyncSession) -> None:
        await _seed_all_models(postgres_db_session)
        await postgres_db_session.execute(text("SET enable_seqscan = off"))
        explain_result = await postgres_db_session.execute(
            text('EXPLAIN ANALYZE SELECT * FROM defects WHERE root_cause @> \'{"module": "orders"}\'::jsonb')
        )
        plan_lines = [row[0] for row in explain_result.fetchall()]
        plan_text = "\n".join(plan_lines).lower()
        assert "index" in plan_text, f"GIN index not used in query plan:\n{plan_text}"

    async def test_explain_analyze_gin_index_on_error_type(self, postgres_db_session: AsyncSession) -> None:
        await _seed_all_models(postgres_db_session)
        await postgres_db_session.execute(text("SET enable_seqscan = off"))
        explain_result = await postgres_db_session.execute(
            text('EXPLAIN ANALYZE SELECT * FROM defects WHERE root_cause @> \'{"error_type": "Timeout"}\'::jsonb')
        )
        plan_lines = [row[0] for row in explain_result.fetchall()]
        plan_text = "\n".join(plan_lines).lower()
        assert "index" in plan_text, f"GIN index not used in query plan:\n{plan_text}"

    async def test_explain_analyze_jsonb_no_match(self, postgres_db_session: AsyncSession) -> None:
        await _seed_all_models(postgres_db_session)
        await postgres_db_session.execute(text("SET enable_seqscan = off"))
        explain_result = await postgres_db_session.execute(
            text('EXPLAIN ANALYZE SELECT * FROM defects WHERE root_cause @> \'{"module": "nonexistent"}\'::jsonb')
        )
        plan_lines = [row[0] for row in explain_result.fetchall()]
        plan_text = "\n".join(plan_lines).lower()
        assert "index" in plan_text, f"GIN index not used for no-match query:\n{plan_text}"

    async def test_gin_index_exists_in_pg_indexes(self, postgres_db_session: AsyncSession) -> None:
        result = await postgres_db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'defects' AND indexname = 'ix_defects_root_cause_gin'"
            )
        )
        row = result.fetchone()
        assert row is not None, "GIN index ix_defects_root_cause_gin not found in pg_indexes"


@requires_pg
class TestPgTrgmSearch:
    async def test_similarity_function_available(self, postgres_db_session: AsyncSession) -> None:
        result = await postgres_db_session.execute(text("SELECT similarity('hello world', 'hallo world')"))
        row = result.fetchone()
        assert row is not None
        assert isinstance(row[0], float)
        assert row[0] > 0.3

    async def test_fuzzy_search_repository(self, postgres_db_session: AsyncSession) -> None:
        ids = await _seed_all_models(postgres_db_session)
        repo = DefectRepository(postgres_db_session)
        results = await repo.fuzzy_search("order creation endpoint returns 500")
        assert len(results) >= 1
        result_ids = {d.id for d in results}
        assert ids["defect_id"] in result_ids

    async def test_fuzzy_search_ordered_by_similarity(self, postgres_db_session: AsyncSession) -> None:
        ids = await _seed_all_models(postgres_db_session)
        repo = DefectRepository(postgres_db_session)
        results = await repo.fuzzy_search("timeout on slow connections")
        assert len(results) >= 1
        assert results[0].id == ids["defect2_id"]

    async def test_fuzzy_search_no_match(self, postgres_db_session: AsyncSession) -> None:
        await _seed_all_models(postgres_db_session)
        repo = DefectRepository(postgres_db_session)
        results = await repo.fuzzy_search("completely unrelated zxcvbnm text")
        assert len(results) == 0

    async def test_pg_trgm_index_exists(self, postgres_db_session: AsyncSession) -> None:
        result = await postgres_db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'defects' AND indexname = 'ix_defects_description_gin'"
            )
        )
        row = result.fetchone()
        assert row is not None, "GIN trgm index ix_defects_description_gin not found"


@requires_pg
class TestAsyncSessionPool:
    async def test_ten_concurrent_sessions(self, postgres_db_session: AsyncSession) -> None:
        engine = create_async_engine(
            PG_URL,
            pool_size=15,
            max_overflow=5,
            pool_pre_ping=True,
        )

        async def _concurrent_insert(index: int) -> str:
            factory = async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            async with factory() as session:
                s = TestSession(
                    name=f"concurrent-{index}",
                    status="pending",
                    trigger_type="manual",
                )
                session.add(s)
                await session.commit()
                return s.id

        try:
            tasks = [_concurrent_insert(i) for i in range(10)]
            results = await asyncio.gather(*tasks)
            assert len(results) == 10
            assert len(set(results)) == 10
        finally:
            async with engine.begin() as conn:
                for i in range(10):
                    await conn.execute(
                        text("DELETE FROM test_sessions WHERE name = :name"),
                        {"name": f"concurrent-{i}"},
                    )
            await engine.dispose()

    async def test_concurrent_read_write_no_errors(self, postgres_db_session: AsyncSession) -> None:
        engine = create_async_engine(
            PG_URL,
            pool_size=15,
            max_overflow=5,
            pool_pre_ping=True,
        )

        async def _concurrent_read_write(index: int) -> dict[str, object]:
            factory = async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            async with factory() as session:
                s = TestSession(
                    name=f"rw-concurrent-{index}",
                    status="pending",
                    trigger_type="manual",
                )
                session.add(s)
                await session.flush()
                loaded = await session.get(TestSession, s.id)
                status_before = loaded.status if loaded else "unknown"
                s.status = "completed"
                await session.commit()
                return {"id": s.id, "status_before": status_before, "status_after": s.status}

        try:
            tasks = [_concurrent_read_write(i) for i in range(10)]
            results = await asyncio.gather(*tasks)
            assert len(results) == 10
            for r in results:
                assert r["status_before"] == "pending"
                assert r["status_after"] == "completed"
        finally:
            async with engine.begin() as conn:
                for i in range(10):
                    await conn.execute(
                        text("DELETE FROM test_sessions WHERE name = :name"),
                        {"name": f"rw-concurrent-{i}"},
                    )
            await engine.dispose()

    async def test_connection_pool_exhaustion_safety(self, postgres_db_session: AsyncSession) -> None:
        engine = create_async_engine(
            PG_URL,
            pool_size=5,
            max_overflow=2,
            pool_pre_ping=True,
        )

        async def _quick_operation(index: int) -> str:
            factory = async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            async with factory() as session:
                result = await session.execute(text("SELECT 1"))
                row = result.fetchone()
                assert row is not None
                return f"ok-{index}"

        try:
            tasks = [_quick_operation(i) for i in range(15)]
            results = await asyncio.gather(*tasks)
            assert len(results) == 15
            for r in results:
                assert r.startswith("ok-")
        finally:
            await engine.dispose()


@requires_pg
class TestDefectTrendsAggregation:
    async def test_trends_aggregation_returns_results(self, postgres_db_session: AsyncSession) -> None:
        await _seed_all_models(postgres_db_session)
        repo = DefectRepository(postgres_db_session)
        trends = await repo.get_defect_trends(days=30)
        assert len(trends) >= 1
        total_count = sum(t["total"] for t in trends)  # type: ignore[misc]
        assert total_count == 2

    async def test_trends_severity_breakdown(self, postgres_db_session: AsyncSession) -> None:
        await _seed_all_models(postgres_db_session)
        repo = DefectRepository(postgres_db_session)
        trends = await repo.get_defect_trends(days=30)
        assert len(trends) >= 1
        for trend in trends:
            assert trend["total"] == trend["critical"] + trend["major"] + trend["minor"] + trend["trivial"]  # type: ignore[operator]

    async def test_trends_day_field_is_string(self, postgres_db_session: AsyncSession) -> None:
        await _seed_all_models(postgres_db_session)
        repo = DefectRepository(postgres_db_session)
        trends = await repo.get_defect_trends(days=30)
        assert len(trends) >= 1
        for trend in trends:
            assert isinstance(trend["day"], str)
            assert trend["day"] is not None

    async def test_trends_zero_days_returns_empty(self, postgres_db_session: AsyncSession) -> None:
        await _seed_all_models(postgres_db_session)
        repo = DefectRepository(postgres_db_session)
        trends = await repo.get_defect_trends(days=0)
        assert len(trends) == 0

    async def test_trends_with_date_trunc_aggregation(self, postgres_db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        s = TestSession(name="trends-date", status="analyzing", trigger_type="manual")
        postgres_db_session.add(s)
        await postgres_db_session.flush()
        p = TestPlan(session_id=s.id, strategy_type="smoke", plan_json={"steps": []})
        postgres_db_session.add(p)
        await postgres_db_session.flush()
        t = TestTask(plan_id=p.id, task_type="api_test", task_config={"url": "/x"})
        postgres_db_session.add(t)
        await postgres_db_session.flush()
        r = TestResult(task_id=t.id, status="failed")
        postgres_db_session.add(r)
        await postgres_db_session.flush()
        d1 = Defect(
            result_id=r.id,
            severity="critical",
            category="bug",
            title="Recent defect 1",
            root_cause={"module": "core"},
        )
        d1.created_at = now - timedelta(days=1)
        postgres_db_session.add(d1)
        d2 = Defect(
            result_id=r.id,
            severity="minor",
            category="configuration",
            title="Recent defect 2",
            root_cause={"module": "config"},
        )
        d2.created_at = now - timedelta(days=1)
        postgres_db_session.add(d2)
        await postgres_db_session.commit()

        repo = DefectRepository(postgres_db_session)
        trends = await repo.get_defect_trends(days=2)
        assert len(trends) >= 1


@requires_pg
class TestMVPTestsOnPostgreSQL:
    async def test_jsonb_column_types_on_postgresql(self, postgres_db_session: AsyncSession) -> None:
        from sqlalchemy.dialects import postgresql

        pg_dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
        from testagent.models import (
            Defect,
            MCPConfig,
            SkillDefinition,
            TestPlan,
            TestResult,
            TestSession,
            TestTask,
        )

        assert TestSession.__table__.c.input_context.type.compile(dialect=pg_dialect) == "JSONB"
        assert TestSession.__table__.c.created_at.type.compile(dialect=pg_dialect) == "TIMESTAMP WITH TIME ZONE"
        assert TestPlan.__table__.c.plan_json.type.compile(dialect=pg_dialect) == "JSONB"
        assert TestTask.__table__.c.task_config.type.compile(dialect=pg_dialect) == "JSONB"
        assert TestTask.__table__.c.started_at.type.compile(dialect=pg_dialect) == "TIMESTAMP WITH TIME ZONE"
        assert TestTask.__table__.c.completed_at.type.compile(dialect=pg_dialect) == "TIMESTAMP WITH TIME ZONE"
        assert TestResult.__table__.c.assertion_results.type.compile(dialect=pg_dialect) == "JSONB"
        assert TestResult.__table__.c.artifacts.type.compile(dialect=pg_dialect) == "JSONB"
        assert Defect.__table__.c.root_cause.type.compile(dialect=pg_dialect) == "JSONB"
        assert MCPConfig.__table__.c.args.type.compile(dialect=pg_dialect) == "JSONB"
        assert MCPConfig.__table__.c.env.type.compile(dialect=pg_dialect) == "JSONB"
        assert SkillDefinition.__table__.c.required_mcp_servers.type.compile(dialect=pg_dialect) == "JSONB"
        assert SkillDefinition.__table__.c.tags.type.compile(dialect=pg_dialect) == "JSONB"
        assert SkillDefinition.__table__.c.updated_at.type.compile(dialect=pg_dialect) == "TIMESTAMP WITH TIME ZONE"

    async def test_full_model_graph_on_postgresql(self, postgres_db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        s = TestSession(
            name="mvp-full-pg",
            status="completed",
            trigger_type="ci_push",
            input_context={"url": "https://pg-test.com", "env": "staging", "retry": True},
            completed_at=now,
        )
        postgres_db_session.add(s)
        await postgres_db_session.flush()

        p = TestPlan(
            session_id=s.id,
            strategy_type="regression",
            plan_json={"steps": ["step1", "step2", "step3"]},
            total_tasks=3,
            completed_tasks=0,
        )
        postgres_db_session.add(p)
        await postgres_db_session.flush()

        t1 = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/api/v1/users", "method": "GET", "module": "users"},
            priority=10,
            status="passed",
        )
        t2 = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/api/v1/orders", "method": "POST", "module": "orders"},
            priority=8,
            status="failed",
        )
        t3 = TestTask(
            plan_id=p.id,
            task_type="web_test",
            task_config={"url": "/dashboard", "method": "GET", "module": "dashboard"},
            priority=5,
            status="passed",
        )
        postgres_db_session.add_all([t1, t2, t3])
        await postgres_db_session.flush()

        r1 = TestResult(
            task_id=t1.id,
            status="passed",
            duration_ms=120.0,
            assertion_results={"total": 3, "passed": 3, "failed": 0},
        )
        r2 = TestResult(
            task_id=t2.id,
            status="failed",
            duration_ms=500.0,
            assertion_results={"total": 3, "passed": 1, "failed": 2},
            artifacts={"har": "s3://bucket/orders.har"},
        )
        r3 = TestResult(
            task_id=t3.id,
            status="passed",
            duration_ms=850.0,
            assertion_results={"total": 5, "passed": 5, "failed": 0},
            artifacts={"screenshot": "s3://bucket/dashboard.png"},
        )
        postgres_db_session.add_all([r1, r2, r3])
        await postgres_db_session.flush()

        d1 = Defect(
            result_id=r2.id,
            severity="critical",
            category="bug",
            title="Orders API returns 500",
            description="The orders API endpoint returns 500 when payload is missing",
            root_cause={
                "module": "orders",
                "error_type": "NullPointerException",
                "layer": "service",
                "stack_trace": "at com.example.OrderService.create(OrderService.java:42)",
            },
        )
        d2 = Defect(
            result_id=r2.id,
            severity="major",
            category="flaky",
            title="Orders API intermittent timeout",
            description="Intermittent timeout on orders API under load",
            root_cause={"module": "orders", "error_type": "Timeout", "layer": "network"},
        )
        postgres_db_session.add_all([d1, d2])
        await postgres_db_session.commit()

        repo = SessionRepository(postgres_db_session)
        stats = await repo.get_session_stats(s.id)
        assert stats["total_tasks"] == 3
        assert stats["passed_tasks"] == 2
        assert stats["failed_tasks"] == 1
        assert stats["avg_duration_ms"] is not None

        coverage = await repo.get_coverage_by_module(s.id)
        assert "users" in coverage
        assert "orders" in coverage
        assert coverage["users"]["total"] == 1
        assert coverage["users"]["passed"] == 1
        assert coverage["orders"]["total"] == 1
        assert coverage["orders"]["passed"] == 0

        defect_repo = DefectRepository(postgres_db_session)
        orders_defects = await defect_repo.search_by_root_cause("module", "orders")
        assert len(orders_defects) == 2

        task_repo = TaskRepository(postgres_db_session)
        tasks = await task_repo.get_by_plan_id(p.id)
        assert len(tasks) == 3

    async def test_skill_and_mcp_config_on_postgresql(self, postgres_db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        sk = SkillDefinition(
            name="mvp-pg-skill",
            version="1.0.0",
            description="MVP PostgreSQL verification skill",
            required_mcp_servers=["api_server", "playwright_server"],
            required_rag_collections=["api_docs", "req_docs", "locator_library"],
            body="# MVP PG Skill\n\n## Steps\n1. Verify connectivity\n2. Run smoke tests",
            tags=["mvp", "pg", "v1"],
            updated_at=now,
        )
        postgres_db_session.add(sk)
        await postgres_db_session.flush()

        mc = MCPConfig(
            server_name="mvp-pg-mcp",
            command="testagent mcp start api_server",
            args={"host": "0.0.0.0", "port": 8080, "workers": 4},
            env={"DATABASE_URL": PG_URL, "LOG_LEVEL": "info"},
            enabled=True,
        )
        postgres_db_session.add(mc)
        await postgres_db_session.commit()

        loaded_skill = await postgres_db_session.get(SkillDefinition, sk.id)
        assert loaded_skill is not None
        assert loaded_skill.name == "mvp-pg-skill"
        assert loaded_skill.required_mcp_servers == ["api_server", "playwright_server"]  # type: ignore[comparison-overlap]
        assert loaded_skill.tags == ["mvp", "pg", "v1"]  # type: ignore[comparison-overlap]

        loaded_mcp = await postgres_db_session.get(MCPConfig, mc.id)
        assert loaded_mcp is not None
        assert loaded_mcp.server_name == "mvp-pg-mcp"
        assert loaded_mcp.args == {"host": "0.0.0.0", "port": 8080, "workers": 4}

    async def test_dialect_is_postgresql(self, postgres_db_session: AsyncSession) -> None:
        dialect = postgres_db_session.bind.dialect.name
        assert dialect == "postgresql"

    async def test_pg_trgm_extension_enabled(self, postgres_db_session: AsyncSession) -> None:
        result = await postgres_db_session.execute(text("SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'"))
        row = result.fetchone()
        assert row is not None, "pg_trgm extension not installed"
        assert row[0] == "pg_trgm"

    async def test_all_gin_indexes_exist(self, postgres_db_session: AsyncSession) -> None:
        result = await postgres_db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename IN ('defects', 'test_results') "
                "AND indexname IN ("
                "'ix_defects_root_cause_gin', "
                "'ix_defects_description_gin', "
                "'ix_test_results_assertion_results_gin'"
                ")"
            )
        )
        indexes = {row[0] for row in result.fetchall()}
        expected = {
            "ix_defects_root_cause_gin",
            "ix_defects_description_gin",
            "ix_test_results_assertion_results_gin",
        }
        assert indexes == expected, f"Missing indexes: {expected - indexes}"
