from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, func, select

from testagent.common.errors import DatabaseError
from testagent.common.logging import get_logger
from testagent.models.defect import Defect
from testagent.models.plan import TestPlan, TestTask
from testagent.models.result import TestResult
from testagent.models.session import TestSession

if TYPE_CHECKING:
    from testagent.db.repository import DefectRepository, SessionRepository

logger = get_logger(__name__)

_TASK_COMPLETED_STATUSES = frozenset({"passed", "failed", "flaky", "skipped"})


def _date_trunc(column: Any, unit: str, dialect: str) -> Any:
    if dialect == "postgresql":
        return func.date_trunc(unit, column)
    if unit == "day":
        return func.date(column)
    if unit == "week":
        return func.date(column)
    return func.date(column)


def _format_date_val(date_val: Any) -> str:
    if isinstance(date_val, datetime):
        return date_val.isoformat()
    if date_val is not None:
        return str(date_val)
    return ""


class QualityTrendsAnalyzer:
    def __init__(self, session_repo: SessionRepository, defect_repo: DefectRepository) -> None:
        self._session_repo = session_repo
        self._defect_repo = defect_repo
        session = session_repo._session
        self._dialect = session.bind.dialect.name if session.bind else "sqlite"

    async def get_pass_rate_trend(self, days: int = 30, unit: str = "day") -> list[dict[str, Any]]:
        since = datetime.now(UTC) - timedelta(days=days)
        date_expr = _date_trunc(TestResult.created_at, unit, self._dialect)
        try:
            stmt = (
                select(
                    date_expr.label("date"),
                    func.count(TestResult.id).label("total"),
                    func.sum(case((TestResult.status == "passed", 1), else_=0)).label("passed"),
                    func.sum(case((TestResult.status.in_(["failed", "error"]), 1), else_=0)).label("failed"),
                    func.sum(case((TestResult.status == "flaky", 1), else_=0)).label("flaky"),
                )
                .where(TestResult.created_at >= since)
                .group_by(date_expr)
                .order_by(date_expr.asc())
            )
            result = await self._session_repo._session.execute(stmt)
            trends: list[dict[str, Any]] = []
            for row in result.all():
                total = row.total or 0
                passed = row.passed or 0
                trends.append(
                    {
                        "date": _format_date_val(row.date),
                        "total": total,
                        "passed": passed,
                        "failed": row.failed or 0,
                        "flaky": row.flaky or 0,
                        "pass_rate": round(passed / total, 4) if total > 0 else 0.0,
                    }
                )
            return trends
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get pass rate trend for days={days}, unit={unit}",
                code="DB_PASS_RATE_TREND_FAILED",
                details={"days": days, "unit": unit, "error": str(exc)},
            ) from exc

    async def get_defect_density_trend(self, days: int = 30) -> list[dict[str, Any]]:
        since = datetime.now(UTC) - timedelta(days=days)
        date_expr = _date_trunc(Defect.created_at, "week", self._dialect)
        try:
            stmt = (
                select(
                    date_expr.label("date"),
                    func.count(Defect.id).label("total"),
                    func.sum(case((Defect.severity == "critical", 1), else_=0)).label("critical"),
                    func.sum(case((Defect.severity == "major", 1), else_=0)).label("major"),
                    func.sum(case((Defect.severity == "minor", 1), else_=0)).label("minor"),
                    func.sum(case((Defect.severity == "trivial", 1), else_=0)).label("trivial"),
                )
                .where(Defect.created_at >= since)
                .group_by(date_expr)
                .order_by(date_expr.asc())
            )
            result = await self._defect_repo._session.execute(stmt)
            trends: list[dict[str, Any]] = []
            for row in result.all():
                trends.append(
                    {
                        "date": _format_date_val(row.date),
                        "total": row.total or 0,
                        "critical": row.critical or 0,
                        "major": row.major or 0,
                        "minor": row.minor or 0,
                        "trivial": row.trivial or 0,
                    }
                )
            return trends
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get defect density trend for days={days}",
                code="DB_DEFECT_DENSITY_TREND_FAILED",
                details={"days": days, "error": str(exc)},
            ) from exc

    async def get_coverage_trend(self, days: int = 30) -> list[dict[str, Any]]:
        since = datetime.now(UTC) - timedelta(days=days)
        date_expr = _date_trunc(TestTask.created_at, "day", self._dialect)
        try:
            total_stmt = (
                select(
                    date_expr.label("date"),
                    TestTask.task_type,
                    func.count(TestTask.id).label("cnt"),
                )
                .join(TestPlan, TestTask.plan_id == TestPlan.id)
                .join(TestSession, TestPlan.session_id == TestSession.id)
                .where(TestTask.created_at >= since)
                .group_by(date_expr, TestTask.task_type)
                .order_by(date_expr.asc())
            )
            total_rows = (await self._session_repo._session.execute(total_stmt)).all()

            completed_stmt = (
                select(
                    date_expr.label("date"),
                    TestTask.task_type,
                    func.count(TestTask.id).label("cnt"),
                )
                .join(TestPlan, TestTask.plan_id == TestPlan.id)
                .join(TestSession, TestPlan.session_id == TestSession.id)
                .where(TestTask.created_at >= since, TestTask.status.in_(_TASK_COMPLETED_STATUSES))
                .group_by(date_expr, TestTask.task_type)
                .order_by(date_expr.asc())
            )
            completed_rows = (await self._session_repo._session.execute(completed_stmt)).all()

            total_map: dict[str, dict[str, int]] = {}
            for row in total_rows:
                d = _format_date_val(row.date)
                if d not in total_map:
                    total_map[d] = {}
                total_map[d][row.task_type] = row.cnt

            completed_map: dict[str, dict[str, int]] = {}
            for row in completed_rows:
                d = _format_date_val(row.date)
                if d not in completed_map:
                    completed_map[d] = {}
                completed_map[d][row.task_type] = row.cnt

            trends: list[dict[str, Any]] = []
            for date_key in sorted(total_map):
                totals = total_map[date_key]
                completeds = completed_map.get(date_key, {})
                api_total = totals.get("api_test", 0)
                web_total = totals.get("web_test", 0)
                app_total = totals.get("app_test", 0)
                api_done = completeds.get("api_test", 0)
                web_done = completeds.get("web_test", 0)
                app_done = completeds.get("app_test", 0)
                total_all = api_total + web_total + app_total
                done_all = api_done + web_done + app_done
                trends.append(
                    {
                        "date": date_key,
                        "api_coverage": round(api_done / api_total, 4) if api_total > 0 else 0.0,
                        "web_coverage": round(web_done / web_total, 4) if web_total > 0 else 0.0,
                        "app_coverage": round(app_done / app_total, 4) if app_total > 0 else 0.0,
                        "total_coverage": round(done_all / total_all, 4) if total_all > 0 else 0.0,
                    }
                )
            return trends
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get coverage trend for days={days}",
                code="DB_COVERAGE_TREND_FAILED",
                details={"days": days, "error": str(exc)},
            ) from exc

    async def get_flaky_rate_trend(self, days: int = 30) -> list[dict[str, Any]]:
        since = datetime.now(UTC) - timedelta(days=days)
        date_expr = _date_trunc(TestTask.created_at, "day", self._dialect)
        try:
            total_stmt = (
                select(
                    date_expr.label("date"),
                    func.count(TestTask.id).label("total"),
                )
                .where(TestTask.created_at >= since)
                .group_by(date_expr)
                .order_by(date_expr.asc())
            )
            total_rows = (await self._session_repo._session.execute(total_stmt)).all()

            flaky_stmt = (
                select(
                    date_expr.label("date"),
                    func.count(TestTask.id).label("flaky"),
                )
                .where(TestTask.created_at >= since, TestTask.retry_count > 0)
                .group_by(date_expr)
                .order_by(date_expr.asc())
            )
            flaky_rows = (await self._session_repo._session.execute(flaky_stmt)).all()

            flaky_map: dict[str, int] = {}
            for row in flaky_rows:
                flaky_map[_format_date_val(row.date)] = row.flaky or 0

            trends: list[dict[str, Any]] = []
            for row in total_rows:
                d = _format_date_val(row.date)
                total = row.total or 0
                flaky_count = flaky_map.get(d, 0)
                trends.append(
                    {
                        "date": d,
                        "total": total,
                        "flaky": flaky_count,
                        "flaky_rate": round(flaky_count / total, 4) if total > 0 else 0.0,
                    }
                )
            return trends
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get flaky rate trend for days={days}",
                code="DB_FLAKY_RATE_TREND_FAILED",
                details={"days": days, "error": str(exc)},
            ) from exc

    async def get_summary(self) -> dict[str, Any]:
        try:
            trends_7d = await self.get_pass_rate_trend(days=7)
            trends_30d = await self.get_pass_rate_trend(days=30)
            defect_trends_30d = await self.get_defect_density_trend(days=30)
            coverage_trends_30d = await self.get_coverage_trend(days=30)
            flaky_trends_30d = await self.get_flaky_rate_trend(days=30)

            total_passed = sum(t["passed"] for t in trends_30d)
            total_all = sum(t["total"] for t in trends_30d)
            overall_pass_rate = round(total_passed / total_all, 4) if total_all > 0 else 0.0

            total_defects = sum(t["total"] for t in defect_trends_30d)

            latest_coverage = coverage_trends_30d[-1] if coverage_trends_30d else {}
            latest_flaky = flaky_trends_30d[-1] if flaky_trends_30d else {}

            pass_rate_change = 0.0
            if len(trends_7d) >= 2:
                current = trends_7d[-1]["pass_rate"]
                previous = trends_7d[0]["pass_rate"]
                pass_rate_change = round(current - previous, 4)

            defect_change = 0
            if len(defect_trends_30d) >= 2:
                defect_change = defect_trends_30d[-1]["total"] - defect_trends_30d[0]["total"]

            return {
                "overall_pass_rate": overall_pass_rate,
                "total_defects_30d": total_defects,
                "total_tests_30d": total_all,
                "pass_rate_change_7d": pass_rate_change,
                "defect_change_30d": defect_change,
                "latest_coverage": latest_coverage.get("total_coverage", 0.0),
                "latest_flaky_rate": latest_flaky.get("flaky_rate", 0.0),
                "period": "30d",
            }
        except Exception as exc:
            raise DatabaseError(
                "Failed to get quality summary",
                code="DB_QUALITY_SUMMARY_FAILED",
                details={"error": str(exc)},
            ) from exc
