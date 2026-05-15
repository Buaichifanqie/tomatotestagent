from __future__ import annotations

import os
import socket
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from testagent.db.migrations import async_upgrade_head
from testagent.db.repository import (
    DefectRepository,
    SessionRepository,
    TaskRepository,
)
from testagent.models.defect import Defect
from testagent.models.plan import TestPlan, TestTask
from testagent.models.result import TestResult
from testagent.models.session import TestSession

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

PG_HOST = os.environ.get("TESTAGENT_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("TESTAGENT_PG_PORT", "5432"))
PG_USER = os.environ.get("TESTAGENT_PG_USER", "testagent")
PG_PASSWORD = os.environ.get("TESTAGENT_PG_PASSWORD", "testagent")
PG_DB = os.environ.get("TESTAGENT_PG_DB", "testagent_test")

PG_URL = f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"


def _pg_is_available() -> bool:
    if os.environ.get("TESTAGENT_PG_HOST") is None:
        return False
    try:
        with socket.create_connection((PG_HOST, PG_PORT), timeout=2):
            return True
    except OSError:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_is_available(),
    reason="PostgreSQL not available; set TESTAGENT_PG_HOST and ensure PG is running",
)


@pytest_asyncio.fixture
async def pg_engine():
    engine = create_async_engine(
        PG_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    await async_upgrade_head(database_url=PG_URL)
    yield engine
    async with engine.begin() as conn:
        table_names = [
            "defects",
            "test_results",
            "test_tasks",
            "test_plans",
            "mcp_configs",
            "skill_definitions",
            "test_sessions",
        ]
        for tname in table_names:
            await conn.execute(text(f"DROP TABLE IF EXISTS {tname} CASCADE"))
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(
        bind=pg_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield session


async def _seed_pg_full_graph(session: AsyncSession) -> tuple[str, str, str, str]:
    s = TestSession(name="pg-v1-test", status="completed", trigger_type="manual")
    session.add(s)
    await session.flush()
    p = TestPlan(
        session_id=s.id,
        strategy_type="regression",
        plan_json={"steps": ["pg-step1"]},
    )
    session.add(p)
    await session.flush()
    t1 = TestTask(
        plan_id=p.id,
        task_type="api_test",
        task_config={"url": "/api/orders", "module": "orders"},
        priority=10,
        status="passed",
    )
    t2 = TestTask(
        plan_id=p.id,
        task_type="web_test",
        task_config={"url": "/web/products", "module": "products"},
        priority=5,
        status="failed",
        retry_count=4,
    )
    t3 = TestTask(
        plan_id=p.id,
        task_type="api_test",
        task_config={"url": "/api/orders", "module": "orders"},
        priority=8,
        status="passed",
    )
    session.add(t1)
    session.add(t2)
    session.add(t3)
    await session.flush()
    r1 = TestResult(task_id=t1.id, status="passed", duration_ms=150.0)
    r2 = TestResult(task_id=t2.id, status="failed", duration_ms=300.0)
    r3 = TestResult(task_id=t3.id, status="passed", duration_ms=100.0)
    session.add(r1)
    session.add(r2)
    session.add(r3)
    await session.flush()
    d1 = Defect(
        result_id=r2.id,
        severity="critical",
        category="bug",
        title="Order creation fails on PostgreSQL",
        description="The order creation endpoint returns 500 when payload is missing on PostgreSQL database",
        root_cause={"module": "orders", "error_type": "NullPointer", "layer": "service"},
    )
    d2 = Defect(
        result_id=r2.id,
        severity="major",
        category="flaky",
        title="Product list intermittent timeout on PG",
        description="Product list page has intermittent timeout on slow connections to PostgreSQL",
        root_cause={"module": "products", "error_type": "Timeout", "layer": "network"},
    )
    d3 = Defect(
        result_id=r2.id,
        severity="minor",
        category="configuration",
        title="Config mismatch warning in PG setup",
        description="Configuration file shows a minor mismatch warning during PostgreSQL startup",
        root_cause={"module": "orders", "error_type": "ConfigMismatch", "layer": "config"},
    )
    session.add(d1)
    session.add(d2)
    session.add(d3)
    await session.flush()
    await session.commit()
    return s.id, p.id, d1.id, d2.id


@requires_pg
class TestDefectSearchByRootCausePG:
    async def test_jsonb_query_root_cause(self, pg_session: AsyncSession) -> None:
        _session_id, _plan_id, d1_id, _d2_id = await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        results = await repo.search_by_root_cause("module", "orders")
        assert len(results) == 2
        result_ids = {d.id for d in results}
        assert d1_id in result_ids

    async def test_jsonb_query_nested_key(self, pg_session: AsyncSession) -> None:
        _session_id, _plan_id, _d1_id, d2_id = await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        results = await repo.search_by_root_cause("error_type", "Timeout")
        assert len(results) == 1
        assert results[0].id == d2_id

    async def test_jsonb_query_layer_key(self, pg_session: AsyncSession) -> None:
        _session_id, _plan_id, d1_id, _d2_id = await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        results = await repo.search_by_root_cause("layer", "service")
        assert len(results) == 1
        assert results[0].id == d1_id

    async def test_jsonb_query_no_match(self, pg_session: AsyncSession) -> None:
        await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        results = await repo.search_by_root_cause("module", "nonexistent")
        assert len(results) == 0


@requires_pg
class TestDefectFuzzySearchPG:
    async def test_pg_trgm_similarity_search(self, pg_session: AsyncSession) -> None:
        _session_id, _plan_id, d1_id, _d2_id = await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        results = await repo.fuzzy_search("order creation endpoint returns 500")
        assert len(results) >= 1
        result_ids = {d.id for d in results}
        assert d1_id in result_ids

    async def test_pg_trgm_similarity_threshold(self, pg_session: AsyncSession) -> None:
        await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        results = await repo.fuzzy_search("completely unrelated gibberish text")
        assert len(results) == 0

    async def test_pg_trgm_ordered_by_similarity(self, pg_session: AsyncSession) -> None:
        _session_id, _plan_id, _d1_id, d2_id = await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        results = await repo.fuzzy_search("timeout on slow connections")
        assert len(results) >= 1
        assert results[0].id == d2_id


@requires_pg
class TestDefectGetBySeverityAndModulePG:
    async def test_jsonb_severity_and_module(self, pg_session: AsyncSession) -> None:
        _session_id, _plan_id, d1_id, _d2_id = await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        results = await repo.get_by_severity_and_module("critical", "orders")
        assert len(results) == 1
        assert results[0].id == d1_id

    async def test_jsonb_severity_and_module_major(self, pg_session: AsyncSession) -> None:
        _session_id, _plan_id, _d1_id, d2_id = await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        results = await repo.get_by_severity_and_module("major", "products")
        assert len(results) == 1
        assert results[0].id == d2_id

    async def test_jsonb_severity_and_module_no_match(self, pg_session: AsyncSession) -> None:
        await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        results = await repo.get_by_severity_and_module("critical", "products")
        assert len(results) == 0


@requires_pg
class TestDefectTrendsPG:
    async def test_date_trunc_aggregation(self, pg_session: AsyncSession) -> None:
        await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        trends = await repo.get_defect_trends(days=30)
        assert len(trends) >= 1
        total_count = sum(t["total"] for t in trends)
        assert total_count == 3

    async def test_date_trunc_severity_breakdown(self, pg_session: AsyncSession) -> None:
        await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        trends = await repo.get_defect_trends(days=30)
        assert len(trends) >= 1
        for trend in trends:
            assert trend["total"] == trend["critical"] + trend["major"] + trend["minor"] + trend["trivial"]

    async def test_date_trunc_day_field_format(self, pg_session: AsyncSession) -> None:
        await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        trends = await repo.get_defect_trends(days=30)
        assert len(trends) >= 1
        for trend in trends:
            assert isinstance(trend["day"], str)
            assert trend["day"] is not None

    async def test_date_trunc_days_filter(self, pg_session: AsyncSession) -> None:
        await _seed_pg_full_graph(pg_session)
        repo = DefectRepository(pg_session)
        trends = await repo.get_defect_trends(days=0)
        assert len(trends) == 0


@requires_pg
class TestSessionStatsPG:
    async def test_session_stats_postgresql(self, pg_session: AsyncSession) -> None:
        session_id, _plan_id, _d1_id, _d2_id = await _seed_pg_full_graph(pg_session)
        repo = SessionRepository(pg_session)
        stats = await repo.get_session_stats(session_id)
        assert stats["total_tasks"] == 3
        assert stats["passed_tasks"] == 2
        assert stats["failed_tasks"] == 1
        assert stats["avg_duration_ms"] is not None

    async def test_session_stats_avg_duration_precision(self, pg_session: AsyncSession) -> None:
        session_id, _plan_id, _d1_id, _d2_id = await _seed_pg_full_graph(pg_session)
        repo = SessionRepository(pg_session)
        stats = await repo.get_session_stats(session_id)
        assert stats["avg_duration_ms"] is not None
        avg = float(stats["avg_duration_ms"])
        assert 100.0 <= avg <= 300.0


@requires_pg
class TestCoverageByModulePG:
    async def test_jsonb_coverage_aggregation(self, pg_session: AsyncSession) -> None:
        session_id, _plan_id, _d1_id, _d2_id = await _seed_pg_full_graph(pg_session)
        repo = SessionRepository(pg_session)
        coverage = await repo.get_coverage_by_module(session_id)
        assert "orders" in coverage
        assert "products" in coverage
        assert coverage["orders"]["total"] == 2
        assert coverage["orders"]["passed"] == 2
        assert coverage["orders"]["coverage_ratio"] == 1.0
        assert coverage["products"]["total"] == 1
        assert coverage["products"]["passed"] == 0
        assert coverage["products"]["coverage_ratio"] == 0.0


@requires_pg
class TestFlakyTasksPG:
    async def test_flaky_tasks_postgresql(self, pg_session: AsyncSession) -> None:
        await _seed_pg_full_graph(pg_session)
        repo = TaskRepository(pg_session)
        flaky = await repo.get_flaky_tasks(threshold=3)
        assert len(flaky) == 1
        assert flaky[0].retry_count >= 3

    async def test_flaky_tasks_custom_threshold(self, pg_session: AsyncSession) -> None:
        await _seed_pg_full_graph(pg_session)
        repo = TaskRepository(pg_session)
        flaky = await repo.get_flaky_tasks(threshold=5)
        assert len(flaky) == 0


@requires_pg
class TestDialectIsPostgreSQL:
    async def test_dialect_is_postgresql(self, pg_session: AsyncSession) -> None:
        dialect = pg_session.bind.dialect.name
        assert dialect == "postgresql"
