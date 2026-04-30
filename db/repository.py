from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Select, select

from testagent.common.errors import DatabaseError
from testagent.common.logging import get_logger
from testagent.models.base import BaseModel
from testagent.models.defect import Defect
from testagent.models.plan import TestTask
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
