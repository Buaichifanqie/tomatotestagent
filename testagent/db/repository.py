from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import Select, case, func, select

from testagent.common.errors import DatabaseError
from testagent.common.logging import get_logger
from testagent.models.base import BaseModel
from testagent.models.defect import Defect
from testagent.models.plan import TestPlan, TestTask
from testagent.models.result import TestResult
from testagent.models.session import TestSession

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class Repository[T: BaseModel]:
    _model_class: type[T]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, entity_id: str) -> T | None:
        try:
            stmt = select(self._model_class).where(self._model_class.id == entity_id)
            result = await self._session.execute(stmt)
            return result.scalar_one_or_none()
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get {self._model_class.__name__} by id: {entity_id}",
                code="DB_GET_BY_ID_FAILED",
                details={"model": self._model_class.__name__, "id": entity_id, "error": str(exc)},
            ) from exc

    async def get_all(self, offset: int = 0, limit: int = 100) -> list[T]:
        try:
            stmt = select(self._model_class).offset(offset).limit(limit).order_by(self._model_class.created_at.desc())
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get all {self._model_class.__name__}",
                code="DB_GET_ALL_FAILED",
                details={
                    "model": self._model_class.__name__,
                    "offset": offset,
                    "limit": limit,
                    "error": str(exc),
                },
            ) from exc

    async def create(self, entity: T) -> T:
        try:
            self._session.add(entity)
            await self._session.flush()
            return entity
        except Exception as exc:
            raise DatabaseError(
                f"Failed to create {self._model_class.__name__}",
                code="DB_CREATE_FAILED",
                details={"model": self._model_class.__name__, "error": str(exc)},
            ) from exc

    async def update(self, entity_id: str, data: dict[str, object]) -> T | None:
        try:
            entity = await self.get_by_id(entity_id)
            if entity is None:
                return None
            for key, value in data.items():
                if hasattr(entity, key):
                    setattr(entity, key, value)
            await self._session.flush()
            return entity
        except DatabaseError:
            raise
        except Exception as exc:
            raise DatabaseError(
                f"Failed to update {self._model_class.__name__} id={entity_id}",
                code="DB_UPDATE_FAILED",
                details={"model": self._model_class.__name__, "id": entity_id, "error": str(exc)},
            ) from exc

    async def delete(self, entity_id: str) -> bool:
        try:
            entity = await self.get_by_id(entity_id)
            if entity is None:
                return False
            await self._session.delete(entity)
            await self._session.flush()
            return True
        except DatabaseError:
            raise
        except Exception as exc:
            raise DatabaseError(
                f"Failed to delete {self._model_class.__name__} id={entity_id}",
                code="DB_DELETE_FAILED",
                details={"model": self._model_class.__name__, "id": entity_id, "error": str(exc)},
            ) from exc

    def _base_query(self) -> Select[tuple[T]]:
        return select(self._model_class)


class SessionRepository(Repository[TestSession]):
    _model_class = TestSession

    async def get_by_status(self, status: str) -> list[TestSession]:
        try:
            stmt = self._base_query().where(TestSession.status == status).order_by(TestSession.created_at.desc())
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get sessions by status: {status}",
                code="DB_SESSION_BY_STATUS_FAILED",
                details={"status": status, "error": str(exc)},
            ) from exc

    async def get_session_stats(self, session_id: str) -> dict[str, object]:
        try:
            total_tasks = (
                select(func.count(TestTask.id))
                .join(TestPlan, TestTask.plan_id == TestPlan.id)
                .where(TestPlan.session_id == session_id)
            )
            passed_tasks = (
                select(func.count(TestTask.id))
                .join(TestPlan, TestTask.plan_id == TestPlan.id)
                .where(TestPlan.session_id == session_id, TestTask.status == "passed")
            )
            failed_tasks = (
                select(func.count(TestTask.id))
                .join(TestPlan, TestTask.plan_id == TestPlan.id)
                .where(TestPlan.session_id == session_id, TestTask.status == "failed")
            )
            avg_duration = (
                select(func.avg(TestResult.duration_ms))
                .join(TestTask, TestResult.task_id == TestTask.id)
                .join(TestPlan, TestTask.plan_id == TestPlan.id)
                .where(TestPlan.session_id == session_id, TestResult.duration_ms.is_not(None))
            )
            total_result = await self._session.execute(total_tasks)
            passed_result = await self._session.execute(passed_tasks)
            failed_result = await self._session.execute(failed_tasks)
            avg_result = await self._session.execute(avg_duration)
            return {
                "total_tasks": total_result.scalar() or 0,
                "passed_tasks": passed_result.scalar() or 0,
                "failed_tasks": failed_result.scalar() or 0,
                "avg_duration_ms": avg_result.scalar(),
            }
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get session stats for session_id: {session_id}",
                code="DB_SESSION_STATS_FAILED",
                details={"session_id": session_id, "error": str(exc)},
            ) from exc

    async def get_coverage_by_module(self, session_id: str) -> dict[str, dict[str, object]]:
        try:
            dialect = self._session.bind.dialect.name
            if dialect == "postgresql":
                module_expr = func.jsonb_extract_path_text(TestTask.task_config, "module")
            else:
                module_expr = func.json_extract(TestTask.task_config, "$.module")

            total_stmt = (
                select(
                    module_expr.label("module"),
                    func.count(TestTask.id).label("total"),
                )
                .join(TestPlan, TestTask.plan_id == TestPlan.id)
                .where(TestPlan.session_id == session_id)
                .group_by(module_expr)
            )
            total_result = await self._session.execute(total_stmt)
            total_rows = total_result.all()
            total_by_module = {row.module: row.total for row in total_rows if row.module is not None}

            passed_stmt = (
                select(
                    module_expr.label("module"),
                    func.count(TestTask.id).label("passed"),
                )
                .join(TestPlan, TestTask.plan_id == TestPlan.id)
                .where(TestPlan.session_id == session_id, TestTask.status == "passed")
                .group_by(module_expr)
            )
            passed_result = await self._session.execute(passed_stmt)
            passed_rows = passed_result.all()
            passed_by_module = {row.module: row.passed for row in passed_rows if row.module is not None}

            coverage: dict[str, dict[str, object]] = {}
            for module, total in total_by_module.items():
                passed = passed_by_module.get(module, 0)
                coverage[module] = {
                    "total": total,
                    "passed": passed,
                    "coverage_ratio": passed / total if total > 0 else 0.0,
                }
            return coverage
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get coverage by module for session_id: {session_id}",
                code="DB_SESSION_COVERAGE_FAILED",
                details={"session_id": session_id, "error": str(exc)},
            ) from exc


class TaskRepository(Repository[TestTask]):
    _model_class = TestTask

    async def get_by_plan_id(self, plan_id: str) -> list[TestTask]:
        try:
            stmt = (
                self._base_query()
                .where(TestTask.plan_id == plan_id)
                .order_by(TestTask.priority.desc(), TestTask.created_at.asc())
            )
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get tasks by plan_id: {plan_id}",
                code="DB_TASK_BY_PLAN_FAILED",
                details={"plan_id": plan_id, "error": str(exc)},
            ) from exc

    async def get_dependent_tasks(self, task_id: str) -> list[TestTask]:
        try:
            stmt = self._base_query().where(TestTask.depends_on == task_id)
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get dependent tasks for task_id: {task_id}",
                code="DB_TASK_DEPENDENTS_FAILED",
                details={"task_id": task_id, "error": str(exc)},
            ) from exc

    async def get_flaky_tasks(self, threshold: int = 3) -> list[TestTask]:
        try:
            stmt = (
                self._base_query()
                .where(TestTask.retry_count >= threshold)
                .order_by(TestTask.retry_count.desc(), TestTask.created_at.desc())
            )
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get flaky tasks with threshold: {threshold}",
                code="DB_FLAKY_TASKS_FAILED",
                details={"threshold": threshold, "error": str(exc)},
            ) from exc


class DefectRepository(Repository[Defect]):
    _model_class = Defect

    async def get_by_severity(self, severity: str) -> list[Defect]:
        try:
            stmt = self._base_query().where(Defect.severity == severity).order_by(Defect.created_at.desc())
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get defects by severity: {severity}",
                code="DB_DEFECT_BY_SEVERITY_FAILED",
                details={"severity": severity, "error": str(exc)},
            ) from exc

    async def get_by_category(self, category: str) -> list[Defect]:
        try:
            stmt = self._base_query().where(Defect.category == category).order_by(Defect.created_at.desc())
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get defects by category: {category}",
                code="DB_DEFECT_BY_CATEGORY_FAILED",
                details={"category": category, "error": str(exc)},
            ) from exc

    async def search_by_root_cause(self, key: str, value: str) -> list[Defect]:
        try:
            dialect = self._session.bind.dialect.name
            if dialect == "postgresql":
                stmt = (
                    self._base_query()
                    .where(func.jsonb_extract_path_text(Defect.root_cause, key) == value)
                    .order_by(Defect.created_at.desc())
                )
            else:
                stmt = (
                    self._base_query()
                    .where(func.json_extract(Defect.root_cause, f"$.{key}") == value)
                    .order_by(Defect.created_at.desc())
                )
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseError(
                f"Failed to search defects by root_cause key={key}, value={value}",
                code="DB_DEFECT_ROOT_CAUSE_SEARCH_FAILED",
                details={"key": key, "value": value, "error": str(exc)},
            ) from exc

    async def fuzzy_search(self, text: str) -> list[Defect]:
        try:
            dialect = self._session.bind.dialect.name
            if dialect == "postgresql":
                stmt = (
                    self._base_query()
                    .where(func.similarity(Defect.description, text) > 0.3)
                    .order_by(func.similarity(Defect.description, text).desc())
                )
            else:
                stmt = self._base_query().where(Defect.description.like(f"%{text}%")).order_by(Defect.created_at.desc())
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseError(
                f"Failed to fuzzy search defects with text: {text}",
                code="DB_DEFECT_FUZZY_SEARCH_FAILED",
                details={"text": text, "error": str(exc)},
            ) from exc

    async def get_by_severity_and_module(self, severity: str, module: str) -> list[Defect]:
        try:
            dialect = self._session.bind.dialect.name
            if dialect == "postgresql":
                stmt = (
                    self._base_query()
                    .where(Defect.severity == severity)
                    .where(func.jsonb_extract_path_text(Defect.root_cause, "module") == module)
                    .order_by(Defect.created_at.desc())
                )
            else:
                stmt = (
                    self._base_query()
                    .where(Defect.severity == severity)
                    .where(func.json_extract(Defect.root_cause, "$.module") == module)
                    .order_by(Defect.created_at.desc())
                )
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseError(
                f"Failed to get defects by severity={severity} and module={module}",
                code="DB_DEFECT_SEVERITY_MODULE_FAILED",
                details={"severity": severity, "module": module, "error": str(exc)},
            ) from exc

    async def get_defect_trends(self, days: int = 30) -> list[dict[str, object]]:
        try:
            since = datetime.now(UTC) - timedelta(days=days)
            dialect = self._session.bind.dialect.name
            if dialect == "postgresql":
                day_expr = func.date_trunc("day", Defect.created_at)
            else:
                day_expr = func.date(Defect.created_at)
            stmt = (
                select(
                    day_expr.label("day"),
                    func.count(Defect.id).label("total"),
                    func.sum(case((Defect.severity == "critical", 1), else_=0)).label("critical"),
                    func.sum(case((Defect.severity == "major", 1), else_=0)).label("major"),
                    func.sum(case((Defect.severity == "minor", 1), else_=0)).label("minor"),
                    func.sum(case((Defect.severity == "trivial", 1), else_=0)).label("trivial"),
                )
                .where(Defect.created_at >= since)
                .group_by(day_expr)
                .order_by(day_expr.asc())
            )
            result = await self._session.execute(stmt)
            trends: list[dict[str, object]] = []
            for row in result.all():
                day_val = row.day
                if isinstance(day_val, datetime):
                    day_str = day_val.isoformat()
                elif day_val is not None:
                    day_str = str(day_val)
                else:
                    day_str = None
                trends.append(
                    {
                        "day": day_str,
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
                f"Failed to get defect trends for days={days}",
                code="DB_DEFECT_TRENDS_FAILED",
                details={"days": days, "error": str(exc)},
            ) from exc
