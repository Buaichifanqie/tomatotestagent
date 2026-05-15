from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from testagent.common.errors import DatabaseError
from testagent.db.repository import (
    DefectRepository,
    SessionRepository,
    TaskRepository,
)
from testagent.models.base import Base
from testagent.models.defect import Defect
from testagent.models.plan import TestPlan, TestTask
from testagent.models.result import TestResult
from testagent.models.session import TestSession

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest_asyncio.fixture()
async def async_engine():
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture()
async def async_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(bind=async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


async def _seed_full_graph(session: AsyncSession) -> tuple[str, str, str, str]:
    s = TestSession(name="v1-unit-test", status="completed", trigger_type="manual")
    session.add(s)
    await session.flush()
    p = TestPlan(
        session_id=s.id,
        strategy_type="regression",
        plan_json={"steps": ["step1"]},
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
        title="Order creation fails",
        description="The order creation endpoint returns 500 when payload is missing",
        root_cause={"module": "orders", "error_type": "NullPointer", "layer": "service"},
    )
    d2 = Defect(
        result_id=r2.id,
        severity="major",
        category="flaky",
        title="Product list intermittent timeout",
        description="Product list page has intermittent timeout on slow connections",
        root_cause={"module": "products", "error_type": "Timeout", "layer": "network"},
    )
    d3 = Defect(
        result_id=r2.id,
        severity="minor",
        category="configuration",
        title="Config mismatch warning",
        description="Configuration file shows a minor mismatch warning during startup",
        root_cause={"module": "orders", "error_type": "ConfigMismatch", "layer": "config"},
    )
    session.add(d1)
    session.add(d2)
    session.add(d3)
    await session.flush()
    return s.id, p.id, d1.id, d2.id


class TestDefectSearchByRootCause:
    async def test_search_by_root_cause_sqlite(self, async_session: AsyncSession) -> None:
        _session_id, _plan_id, d1_id, d2_id = await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.search_by_root_cause("module", "orders")
        assert len(results) == 2
        result_ids = {d.id for d in results}
        assert d1_id in result_ids
        assert d2_id not in result_ids

    async def test_search_by_root_cause_error_type(self, async_session: AsyncSession) -> None:
        _session_id, _plan_id, _d1_id, d2_id = await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.search_by_root_cause("error_type", "Timeout")
        assert len(results) == 1
        assert results[0].id == d2_id

    async def test_search_by_root_cause_no_match(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.search_by_root_cause("module", "nonexistent")
        assert len(results) == 0

    async def test_search_by_root_cause_empty_db(self, async_session: AsyncSession) -> None:
        repo = DefectRepository(async_session)
        results = await repo.search_by_root_cause("module", "orders")
        assert len(results) == 0


class TestDefectFuzzySearch:
    async def test_fuzzy_search_sqlite_like(self, async_session: AsyncSession) -> None:
        _session_id, _plan_id, d1_id, _d2_id = await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.fuzzy_search("order creation")
        assert len(results) >= 1
        result_ids = {d.id for d in results}
        assert d1_id in result_ids

    async def test_fuzzy_search_partial_match(self, async_session: AsyncSession) -> None:
        _session_id, _plan_id, _d1_id, d2_id = await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.fuzzy_search("intermittent")
        assert len(results) == 1
        assert results[0].id == d2_id

    async def test_fuzzy_search_no_match(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.fuzzy_search("xyznonexistent")
        assert len(results) == 0

    async def test_fuzzy_search_empty_db(self, async_session: AsyncSession) -> None:
        repo = DefectRepository(async_session)
        results = await repo.fuzzy_search("anything")
        assert len(results) == 0


class TestDefectGetBySeverityAndModule:
    async def test_severity_and_module_sqlite(self, async_session: AsyncSession) -> None:
        _session_id, _plan_id, d1_id, _d2_id = await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.get_by_severity_and_module("critical", "orders")
        assert len(results) == 1
        assert results[0].id == d1_id

    async def test_severity_and_module_major(self, async_session: AsyncSession) -> None:
        _session_id, _plan_id, _d1_id, d2_id = await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.get_by_severity_and_module("major", "products")
        assert len(results) == 1
        assert results[0].id == d2_id

    async def test_severity_and_module_no_match(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.get_by_severity_and_module("critical", "products")
        assert len(results) == 0

    async def test_severity_and_module_severity_mismatch(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.get_by_severity_and_module("minor", "products")
        assert len(results) == 0

    async def test_severity_and_module_empty_db(self, async_session: AsyncSession) -> None:
        repo = DefectRepository(async_session)
        results = await repo.get_by_severity_and_module("critical", "orders")
        assert len(results) == 0


class TestDefectTrends:
    async def test_defect_trends_sqlite(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        trends = await repo.get_defect_trends(days=30)
        assert len(trends) >= 1
        total_count = sum(t["total"] for t in trends)
        assert total_count == 3
        critical_count = sum(t["critical"] for t in trends)
        assert critical_count == 1
        major_count = sum(t["major"] for t in trends)
        assert major_count == 1
        minor_count = sum(t["minor"] for t in trends)
        assert minor_count == 1

    async def test_defect_trends_day_field_is_string(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        trends = await repo.get_defect_trends(days=30)
        assert len(trends) >= 1
        for trend in trends:
            assert isinstance(trend["day"], str)
            assert trend["day"] is not None

    async def test_defect_trends_empty_db(self, async_session: AsyncSession) -> None:
        repo = DefectRepository(async_session)
        trends = await repo.get_defect_trends(days=30)
        assert len(trends) == 0

    async def test_defect_trends_respects_days_filter(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        trends = await repo.get_defect_trends(days=0)
        assert len(trends) == 0

    async def test_defect_trends_severity_breakdown(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        trends = await repo.get_defect_trends(days=30)
        assert len(trends) >= 1
        for trend in trends:
            assert "critical" in trend
            assert "major" in trend
            assert "minor" in trend
            assert "trivial" in trend
            assert "total" in trend
            assert trend["total"] == trend["critical"] + trend["major"] + trend["minor"] + trend["trivial"]


class TestSessionStats:
    async def test_get_session_stats(self, async_session: AsyncSession) -> None:
        session_id, _plan_id, _d1_id, _d2_id = await _seed_full_graph(async_session)
        repo = SessionRepository(async_session)
        stats = await repo.get_session_stats(session_id)
        assert stats["total_tasks"] == 3
        assert stats["passed_tasks"] == 2
        assert stats["failed_tasks"] == 1
        assert stats["avg_duration_ms"] is not None

    async def test_get_session_stats_avg_duration(self, async_session: AsyncSession) -> None:
        session_id, _plan_id, _d1_id, _d2_id = await _seed_full_graph(async_session)
        repo = SessionRepository(async_session)
        stats = await repo.get_session_stats(session_id)
        assert stats["avg_duration_ms"] is not None
        avg = float(stats["avg_duration_ms"])
        assert 100.0 <= avg <= 300.0

    async def test_get_session_stats_empty_session(self, async_session: AsyncSession) -> None:
        s = TestSession(name="empty-stats", status="pending", trigger_type="manual")
        async_session.add(s)
        await async_session.flush()
        repo = SessionRepository(async_session)
        stats = await repo.get_session_stats(s.id)
        assert stats["total_tasks"] == 0
        assert stats["passed_tasks"] == 0
        assert stats["failed_tasks"] == 0
        assert stats["avg_duration_ms"] is None

    async def test_get_session_stats_nonexistent(self, async_session: AsyncSession) -> None:
        repo = SessionRepository(async_session)
        stats = await repo.get_session_stats("nonexistent-session-id")
        assert stats["total_tasks"] == 0
        assert stats["passed_tasks"] == 0
        assert stats["failed_tasks"] == 0


class TestCoverageByModule:
    async def test_coverage_by_module_sqlite(self, async_session: AsyncSession) -> None:
        session_id, _plan_id, _d1_id, _d2_id = await _seed_full_graph(async_session)
        repo = SessionRepository(async_session)
        coverage = await repo.get_coverage_by_module(session_id)
        assert "orders" in coverage
        assert "products" in coverage
        assert coverage["orders"]["total"] == 2
        assert coverage["orders"]["passed"] == 2
        assert coverage["orders"]["coverage_ratio"] == 1.0
        assert coverage["products"]["total"] == 1
        assert coverage["products"]["passed"] == 0
        assert coverage["products"]["coverage_ratio"] == 0.0

    async def test_coverage_by_module_empty_session(self, async_session: AsyncSession) -> None:
        s = TestSession(name="empty-coverage", status="pending", trigger_type="manual")
        async_session.add(s)
        await async_session.flush()
        repo = SessionRepository(async_session)
        coverage = await repo.get_coverage_by_module(s.id)
        assert len(coverage) == 0

    async def test_coverage_by_module_nonexistent_session(self, async_session: AsyncSession) -> None:
        repo = SessionRepository(async_session)
        coverage = await repo.get_coverage_by_module("nonexistent-id")
        assert len(coverage) == 0


class TestFlakyTasks:
    async def test_get_flaky_tasks(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = TaskRepository(async_session)
        flaky = await repo.get_flaky_tasks(threshold=3)
        assert len(flaky) == 1
        assert flaky[0].retry_count >= 3

    async def test_get_flaky_tasks_ordered_by_retry_count(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = TaskRepository(async_session)
        flaky = await repo.get_flaky_tasks(threshold=3)
        if len(flaky) > 1:
            assert flaky[0].retry_count >= flaky[1].retry_count

    async def test_get_flaky_tasks_custom_threshold(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = TaskRepository(async_session)
        flaky = await repo.get_flaky_tasks(threshold=5)
        assert len(flaky) == 0

    async def test_get_flaky_tasks_empty_db(self, async_session: AsyncSession) -> None:
        repo = TaskRepository(async_session)
        flaky = await repo.get_flaky_tasks(threshold=3)
        assert len(flaky) == 0

    async def test_get_flaky_tasks_default_threshold(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = TaskRepository(async_session)
        flaky = await repo.get_flaky_tasks()
        assert len(flaky) == 1


class TestDialectBranching:
    async def test_dialect_is_sqlite(self, async_session: AsyncSession) -> None:
        dialect = async_session.bind.dialect.name
        assert dialect == "sqlite"

    async def test_search_by_root_cause_uses_json_extract_on_sqlite(self, async_session: AsyncSession) -> None:
        _session_id, _plan_id, d1_id, _d2_id = await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.search_by_root_cause("layer", "service")
        assert len(results) == 1
        assert results[0].id == d1_id

    async def test_fuzzy_search_uses_like_on_sqlite(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.fuzzy_search("mismatch")
        assert len(results) == 1

    async def test_severity_and_module_uses_json_extract_on_sqlite(self, async_session: AsyncSession) -> None:
        await _seed_full_graph(async_session)
        repo = DefectRepository(async_session)
        results = await repo.get_by_severity_and_module("minor", "orders")
        assert len(results) == 1


class TestRepositoryErrorHandlingV1:
    async def test_search_by_root_cause_error(self, async_session: AsyncSession) -> None:
        repo = DefectRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("connection lost")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_DEFECT_ROOT_CAUSE_SEARCH_FAILED"):
            await repo.search_by_root_cause("key", "value")
        async_session.execute = original_execute

    async def test_fuzzy_search_error(self, async_session: AsyncSession) -> None:
        repo = DefectRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("db error")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_DEFECT_FUZZY_SEARCH_FAILED"):
            await repo.fuzzy_search("test")
        async_session.execute = original_execute

    async def test_get_session_stats_error(self, async_session: AsyncSession) -> None:
        repo = SessionRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("db error")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_SESSION_STATS_FAILED"):
            await repo.get_session_stats("some-id")
        async_session.execute = original_execute

    async def test_get_flaky_tasks_error(self, async_session: AsyncSession) -> None:
        repo = TaskRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("db error")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_FLAKY_TASKS_FAILED"):
            await repo.get_flaky_tasks()
        async_session.execute = original_execute

    async def test_get_defect_trends_error(self, async_session: AsyncSession) -> None:
        repo = DefectRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("db error")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_DEFECT_TRENDS_FAILED"):
            await repo.get_defect_trends()
        async_session.execute = original_execute

    async def test_get_coverage_by_module_error(self, async_session: AsyncSession) -> None:
        repo = SessionRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("db error")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_SESSION_COVERAGE_FAILED"):
            await repo.get_coverage_by_module("some-id")
        async_session.execute = original_execute
