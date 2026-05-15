from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from testagent.agent.quality_trends import QualityTrendsAnalyzer
from testagent.db.repository import DefectRepository, SessionRepository
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


@pytest_asyncio.fixture()
async def analyzer(async_session: AsyncSession) -> QualityTrendsAnalyzer:
    session_repo = SessionRepository(async_session)
    defect_repo = DefectRepository(async_session)
    return QualityTrendsAnalyzer(session_repo=session_repo, defect_repo=defect_repo)


async def _seed_trend_data(session: AsyncSession, days_offset: list[int]) -> list[str]:
    task_ids: list[str] = []
    for offset in days_offset:
        created = datetime.now(UTC) - timedelta(days=offset)
        s = TestSession(
            name=f"trend-session-d{offset}",
            status="completed",
            trigger_type="manual",
            created_at=created,
        )
        session.add(s)
        await session.flush()

        p = TestPlan(
            session_id=s.id,
            strategy_type="regression",
            plan_json={"steps": ["step1"]},
            created_at=created,
        )
        session.add(p)
        await session.flush()

        passed = offset % 3 != 0
        status = "passed" if passed else "failed"
        retry_count = 2 if not passed and offset % 2 == 0 else 0

        t_api = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": f"/api/test/d{offset}"},
            priority=5,
            status=status,
            retry_count=retry_count,
            created_at=created,
        )
        t_web = TestTask(
            plan_id=p.id,
            task_type="web_test",
            task_config={"url": f"/web/test/d{offset}"},
            priority=5,
            status="passed",
            retry_count=0,
            created_at=created,
        )
        session.add(t_api)
        session.add(t_web)
        await session.flush()
        task_ids.append(t_api.id)
        task_ids.append(t_web.id)

        r_passed = TestResult(task_id=t_web.id, status="passed", duration_ms=100.0, created_at=created)
        r_main = TestResult(
            task_id=t_api.id,
            status=status,
            duration_ms=200.0 if passed else 500.0,
            created_at=created,
        )
        session.add(r_passed)
        session.add(r_main)
        await session.flush()

        if not passed:
            d = Defect(
                result_id=r_main.id,
                severity="critical" if offset % 2 == 0 else "major",
                category="bug",
                title=f"Failure on day {offset}",
                description=f"Task failed on day {offset}",
                created_at=created,
            )
            session.add(d)
            await session.flush()

    return task_ids


class TestQualityTrendsAnalyzer:
    async def test_pass_rate_trend_basic(self, async_session: AsyncSession, analyzer: QualityTrendsAnalyzer) -> None:
        await _seed_trend_data(async_session, [0, 1, 2, 3, 4])
        await async_session.commit()

        trends = await analyzer.get_pass_rate_trend(days=30, unit="day")

        assert len(trends) >= 4
        for entry in trends:
            assert "date" in entry
            assert "total" in entry
            assert "passed" in entry
            assert "failed" in entry
            assert "flaky" in entry
            assert "pass_rate" in entry
            assert entry["total"] == entry["passed"] + entry["failed"] + entry["flaky"]
            if entry["total"] > 0:
                expected_rate = round(entry["passed"] / entry["total"], 4)
                assert entry["pass_rate"] == expected_rate

    async def test_pass_rate_trend_date_filter(
        self, async_session: AsyncSession, analyzer: QualityTrendsAnalyzer
    ) -> None:
        await _seed_trend_data(async_session, [1, 5, 10, 20, 40])
        await async_session.commit()

        trends_10d = await analyzer.get_pass_rate_trend(days=10, unit="day")
        trends_30d = await analyzer.get_pass_rate_trend(days=30, unit="day")

        assert len(trends_10d) <= len(trends_30d)
        assert len(trends_30d) >= 4

        for entry in trends_10d:
            assert isinstance(entry["pass_rate"], float)

    async def test_pass_rate_trend_empty(self, analyzer: QualityTrendsAnalyzer) -> None:
        trends = await analyzer.get_pass_rate_trend(days=30, unit="day")
        assert trends == []

    async def test_pass_rate_trend_single_day(
        self, async_session: AsyncSession, analyzer: QualityTrendsAnalyzer
    ) -> None:
        await _seed_trend_data(async_session, [0])
        await async_session.commit()

        trends = await analyzer.get_pass_rate_trend(days=1, unit="day")
        assert len(trends) == 1
        entry = trends[0]
        assert entry["total"] == 2
        assert entry["pass_rate"] > 0

    async def test_defect_density_trend_basic(
        self, async_session: AsyncSession, analyzer: QualityTrendsAnalyzer
    ) -> None:
        await _seed_trend_data(async_session, [0, 2, 4, 6, 8])
        await async_session.commit()

        trends = await analyzer.get_defect_density_trend(days=30)

        assert len(trends) >= 1
        for entry in trends:
            assert "date" in entry
            assert "total" in entry
            assert "critical" in entry
            assert "major" in entry
            assert "minor" in entry
            assert "trivial" in entry
            assert entry["total"] == entry["critical"] + entry["major"] + entry["minor"] + entry["trivial"]

    async def test_defect_density_trend_empty(self, analyzer: QualityTrendsAnalyzer) -> None:
        trends = await analyzer.get_defect_density_trend(days=30)
        assert trends == []

    async def test_defect_density_trend_no_defects(
        self, async_session: AsyncSession, analyzer: QualityTrendsAnalyzer
    ) -> None:
        created = datetime.now(UTC) - timedelta(days=1)
        s = TestSession(name="no-defect-session", status="completed", trigger_type="manual", created_at=created)
        async_session.add(s)
        await async_session.flush()
        p = TestPlan(session_id=s.id, strategy_type="smoke", plan_json={"step": "1"}, created_at=created)
        async_session.add(p)
        await async_session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={},
            priority=5,
            status="passed",
            created_at=created,
        )
        async_session.add(t)
        await async_session.flush()
        r = TestResult(task_id=t.id, status="passed", duration_ms=50.0, created_at=created)
        async_session.add(r)
        await async_session.commit()

        trends = await analyzer.get_defect_density_trend(days=30)
        assert trends == []

    async def test_coverage_trend_basic(self, async_session: AsyncSession, analyzer: QualityTrendsAnalyzer) -> None:
        await _seed_trend_data(async_session, [0, 1, 2, 3, 4])
        await async_session.commit()

        trends = await analyzer.get_coverage_trend(days=30)

        assert len(trends) >= 4
        for entry in trends:
            assert "date" in entry
            assert "api_coverage" in entry
            assert "web_coverage" in entry
            assert "app_coverage" in entry
            assert "total_coverage" in entry
            assert 0.0 <= entry["api_coverage"] <= 1.0
            assert 0.0 <= entry["web_coverage"] <= 1.0
            assert 0.0 <= entry["app_coverage"] <= 1.0
            assert 0.0 <= entry["total_coverage"] <= 1.0

    async def test_coverage_trend_empty(self, analyzer: QualityTrendsAnalyzer) -> None:
        trends = await analyzer.get_coverage_trend(days=30)
        assert trends == []

    async def test_flaky_rate_trend_basic(self, async_session: AsyncSession, analyzer: QualityTrendsAnalyzer) -> None:
        await _seed_trend_data(async_session, [0, 1, 2, 3, 4])
        await async_session.commit()

        trends = await analyzer.get_flaky_rate_trend(days=30)

        assert len(trends) >= 4
        for entry in trends:
            assert "date" in entry
            assert "total" in entry
            assert "flaky" in entry
            assert "flaky_rate" in entry
            assert entry["flaky"] <= entry["total"]
            assert 0.0 <= entry["flaky_rate"] <= 1.0

    async def test_flaky_rate_trend_empty(self, analyzer: QualityTrendsAnalyzer) -> None:
        trends = await analyzer.get_flaky_rate_trend(days=30)
        assert trends == []

    async def test_get_summary_basic(self, async_session: AsyncSession, analyzer: QualityTrendsAnalyzer) -> None:
        await _seed_trend_data(async_session, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
        await async_session.commit()

        summary = await analyzer.get_summary()

        assert "overall_pass_rate" in summary
        assert "total_defects_30d" in summary
        assert "total_tests_30d" in summary
        assert "pass_rate_change_7d" in summary
        assert "defect_change_30d" in summary
        assert "latest_coverage" in summary
        assert "latest_flaky_rate" in summary
        assert "period" in summary
        assert summary["period"] == "30d"
        assert 0.0 <= summary["overall_pass_rate"] <= 1.0
        assert summary["total_tests_30d"] > 0

    async def test_get_summary_empty(self, analyzer: QualityTrendsAnalyzer) -> None:
        summary = await analyzer.get_summary()

        assert summary["overall_pass_rate"] == 0.0
        assert summary["total_defects_30d"] == 0
        assert summary["total_tests_30d"] == 0
        assert summary["pass_rate_change_7d"] == 0.0
        assert summary["defect_change_30d"] == 0
        assert summary["latest_coverage"] == 0.0
        assert summary["latest_flaky_rate"] == 0.0
        assert summary["period"] == "30d"

    async def test_get_summary_edge_cases(self, async_session: AsyncSession, analyzer: QualityTrendsAnalyzer) -> None:
        created = datetime.now(UTC) - timedelta(hours=1)
        s = TestSession(name="edge-session", status="completed", trigger_type="manual", created_at=created)
        async_session.add(s)
        await async_session.flush()
        p = TestPlan(session_id=s.id, strategy_type="smoke", plan_json={"step": "1"}, created_at=created)
        async_session.add(p)
        await async_session.flush()

        t_fail = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={},
            priority=5,
            status="failed",
            retry_count=1,
            created_at=created,
        )
        t_pass = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={},
            priority=5,
            status="passed",
            retry_count=0,
            created_at=created,
        )
        async_session.add(t_fail)
        async_session.add(t_pass)
        await async_session.flush()
        r1 = TestResult(task_id=t_fail.id, status="failed", duration_ms=200.0, created_at=created)
        r2 = TestResult(task_id=t_pass.id, status="passed", duration_ms=50.0, created_at=created)
        async_session.add(r1)
        async_session.add(r2)
        await async_session.flush()

        d = Defect(
            result_id=r1.id,
            severity="major",
            category="bug",
            title="Edge case failure",
            description="Test failure in edge case scenario",
            created_at=created,
        )
        async_session.add(d)
        await async_session.commit()

        summary = await analyzer.get_summary()

        assert summary["overall_pass_rate"] == 0.5
        assert summary["total_defects_30d"] == 1
        assert summary["total_tests_30d"] == 2
        assert summary["latest_coverage"] > 0
        assert summary["latest_flaky_rate"] > 0

    async def test_trend_result_format(self, async_session: AsyncSession, analyzer: QualityTrendsAnalyzer) -> None:
        await _seed_trend_data(async_session, [0, 3, 6])
        await async_session.commit()

        trends = await analyzer.get_pass_rate_trend(days=30, unit="day")

        for entry in trends:
            assert isinstance(entry["date"], str)
            assert isinstance(entry["total"], int)
            assert isinstance(entry["passed"], int)
            assert isinstance(entry["failed"], int)
            assert isinstance(entry["flaky"], int)
            assert isinstance(entry["pass_rate"], float)
            assert entry["total"] >= 0
            assert entry["passed"] >= 0
            assert 0.0 <= entry["pass_rate"] <= 1.0

        defect_trends = await analyzer.get_defect_density_trend(days=30)
        for entry in defect_trends:
            assert isinstance(entry["date"], str)
            assert isinstance(entry["total"], int)
            assert isinstance(entry["critical"], int)
            assert isinstance(entry["major"], int)
            assert isinstance(entry["minor"], int)
            assert isinstance(entry["trivial"], int)

        coverage_trends = await analyzer.get_coverage_trend(days=30)
        for entry in coverage_trends:
            assert isinstance(entry["date"], str)
            assert isinstance(entry["api_coverage"], float)
            assert isinstance(entry["web_coverage"], float)
            assert isinstance(entry["total_coverage"], float)

        flaky_trends = await analyzer.get_flaky_rate_trend(days=30)
        for entry in flaky_trends:
            assert isinstance(entry["date"], str)
            assert isinstance(entry["total"], int)
            assert isinstance(entry["flaky"], int)
            assert isinstance(entry["flaky_rate"], float)
