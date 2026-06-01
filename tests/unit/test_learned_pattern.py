from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from testagent.common.errors import DatabaseError
from testagent.db.repository import LearnedPatternRepository
from testagent.models import Base
from testagent.models.learned_pattern import LearnedPattern

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


PATTERN_TYPES = ("behavior", "workaround", "anti_pattern", "failure_mode")
SOURCE_TYPES = ("modification_delta", "failure_analysis", "manual_entry")
SCOPES = ("app_local", "global")
REVIEW_STATUSES = ("pending", "approved", "rejected")


# ---------------------------------------------------------------------------
# Synchronous fixtures for model-level tests
# ---------------------------------------------------------------------------
@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as sess:
        yield sess


# ---------------------------------------------------------------------------
# Async fixtures for repository tests
# ---------------------------------------------------------------------------
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


async def _seed_patterns(session: AsyncSession) -> dict[str, LearnedPattern]:
    """Seed a small set of learned patterns for testing.

    Returns a dict mapping a short label to the persisted instance so tests
    can reference specific rows by name.
    """
    patterns = {
        "app1_pending": LearnedPattern(
            app_id="com.example.app1",
            pattern="always tap confirm twice",
            pattern_type="behavior",
            source_type="manual_entry",
            review_status="pending",
            confidence=0.6,
        ),
        "app1_approved": LearnedPattern(
            app_id="com.example.app1",
            pattern="swipe left to dismiss toast",
            pattern_type="workaround",
            source_type="modification_delta",
            source_case_id="TC-100",
            review_status="approved",
            confidence=0.9,
        ),
        "app1_rejected": LearnedPattern(
            app_id="com.example.app1",
            pattern="never use back button",
            pattern_type="anti_pattern",
            source_type="failure_analysis",
            review_status="rejected",
            review_reason="too aggressive",
            confidence=0.2,
        ),
        "app2_pending": LearnedPattern(
            app_id="com.example.app2",
            pattern="scroll to load more items",
            pattern_type="behavior",
            source_type="manual_entry",
            review_status="pending",
            confidence=0.7,
        ),
    }
    for p in patterns.values():
        session.add(p)
    await session.flush()
    return patterns


# ===========================================================================
# Model tests (synchronous, unchanged)
# ===========================================================================
class TestLearnedPatternModel:
    def test_model_has_required_fields(self, session: Session):
        pattern = LearnedPattern(
            app_id="com.bilibili.app",
            app_version="7.45.0",
            pattern="B站搜索页会保留历史搜索词，需先清除",
            pattern_type="behavior",
            source_case_id="TC-001",
            source_type="modification_delta",
            confidence=0.8,
            scope="app_local",
            review_status="approved",
            review_reason=None,
            occurrence_count=3,
        )
        session.add(pattern)
        session.flush()
        assert pattern.app_id == "com.bilibili.app"
        assert pattern.app_version == "7.45.0"
        assert pattern.pattern == "B站搜索页会保留历史搜索词，需先清除"
        assert pattern.pattern_type == "behavior"
        assert pattern.source_case_id == "TC-001"
        assert pattern.source_type == "modification_delta"
        assert pattern.confidence == 0.8
        assert pattern.scope == "app_local"
        assert pattern.review_status == "approved"
        assert pattern.review_reason is None
        assert pattern.occurrence_count == 3

    def test_model_defaults(self, session: Session):
        pattern = LearnedPattern(
            app_id="test",
            pattern="test pattern",
            pattern_type="behavior",
            source_type="manual_entry",
        )
        session.add(pattern)
        session.flush()
        assert pattern.id is not None
        parsed = uuid.UUID(pattern.id, version=4)
        assert str(parsed) == pattern.id
        assert pattern.created_at is not None
        assert pattern.app_version is None
        assert pattern.source_case_id is None
        assert pattern.confidence == 0.5
        assert pattern.scope == "app_local"
        assert pattern.review_status == "pending"
        assert pattern.review_reason is None
        assert pattern.occurrence_count == 1

    def test_pattern_type_valid_values(self, session: Session):
        for pt in PATTERN_TYPES:
            p = LearnedPattern(app_id="t", pattern="p", pattern_type=pt, source_type="manual_entry")
            session.add(p)
            session.flush()
            assert p.pattern_type == pt

    def test_source_type_valid_values(self, session: Session):
        for st in SOURCE_TYPES:
            p = LearnedPattern(app_id="t", pattern="p", pattern_type="behavior", source_type=st)
            session.add(p)
            session.flush()
            assert p.source_type == st

    def test_scope_valid_values(self, session: Session):
        for sc in SCOPES:
            p = LearnedPattern(app_id="t", pattern="p", pattern_type="behavior", source_type="manual_entry", scope=sc)
            session.add(p)
            session.flush()
            assert p.scope == sc

    def test_review_status_valid_values(self, session: Session):
        for rs in REVIEW_STATUSES:
            p = LearnedPattern(app_id="t", pattern="p", pattern_type="behavior", source_type="manual_entry", review_status=rs)
            session.add(p)
            session.flush()
            assert p.review_status == rs


# ===========================================================================
# Repository tests (async)
# ===========================================================================
class TestLearnedPatternRepositoryGetByAppId:
    async def test_returns_patterns_for_app(self, async_session: AsyncSession) -> None:
        seeded = await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        results = await repo.get_by_app_id("com.example.app1")
        assert len(results) == 3
        result_ids = {r.id for r in results}
        assert seeded["app1_pending"].id in result_ids
        assert seeded["app1_approved"].id in result_ids
        assert seeded["app1_rejected"].id in result_ids

    async def test_does_not_return_other_apps(self, async_session: AsyncSession) -> None:
        await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        results = await repo.get_by_app_id("com.example.app2")
        assert len(results) == 1
        assert results[0].app_id == "com.example.app2"

    async def test_status_filter_pending(self, async_session: AsyncSession) -> None:
        await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        results = await repo.get_by_app_id("com.example.app1", status_filter="pending")
        assert len(results) == 1
        assert results[0].review_status == "pending"

    async def test_status_filter_approved(self, async_session: AsyncSession) -> None:
        await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        results = await repo.get_by_app_id("com.example.app1", status_filter="approved")
        assert len(results) == 1
        assert results[0].review_status == "approved"

    async def test_status_filter_rejected(self, async_session: AsyncSession) -> None:
        await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        results = await repo.get_by_app_id("com.example.app1", status_filter="rejected")
        assert len(results) == 1
        assert results[0].review_status == "rejected"

    async def test_limit(self, async_session: AsyncSession) -> None:
        await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        results = await repo.get_by_app_id("com.example.app1", limit=2)
        assert len(results) == 2

    async def test_empty_db(self, async_session: AsyncSession) -> None:
        repo = LearnedPatternRepository(async_session)
        results = await repo.get_by_app_id("nonexistent")
        assert len(results) == 0

    async def test_ordered_by_created_at_desc(self, async_session: AsyncSession) -> None:
        """Patterns returned in descending order of created_at."""
        seeded = await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        results = await repo.get_by_app_id("com.example.app1")
        for i in range(len(results) - 1):
            assert results[i].created_at >= results[i + 1].created_at


class TestLearnedPatternRepositoryApprove:
    async def test_approve_sets_status(self, async_session: AsyncSession) -> None:
        seeded = await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        pattern = await repo.approve(seeded["app1_pending"].id)
        assert pattern is not None
        assert pattern.review_status == "approved"

    async def test_approve_returns_none_for_missing(self, async_session: AsyncSession) -> None:
        repo = LearnedPatternRepository(async_session)
        result = await repo.approve("nonexistent-id")
        assert result is None

    async def test_approve_persists(self, async_session: AsyncSession) -> None:
        seeded = await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        await repo.approve(seeded["app1_pending"].id)
        refreshed = await repo.get_by_id(seeded["app1_pending"].id)
        assert refreshed is not None
        assert refreshed.review_status == "approved"


class TestLearnedPatternRepositoryReject:
    async def test_reject_sets_status_and_reason(self, async_session: AsyncSession) -> None:
        seeded = await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        pattern = await repo.reject(seeded["app1_pending"].id, reason="flaky evidence")
        assert pattern is not None
        assert pattern.review_status == "rejected"
        assert pattern.review_reason == "flaky evidence"

    async def test_reject_default_empty_reason(self, async_session: AsyncSession) -> None:
        seeded = await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        pattern = await repo.reject(seeded["app1_pending"].id)
        assert pattern is not None
        assert pattern.review_status == "rejected"
        assert pattern.review_reason == ""

    async def test_reject_returns_none_for_missing(self, async_session: AsyncSession) -> None:
        repo = LearnedPatternRepository(async_session)
        result = await repo.reject("nonexistent-id", reason="nope")
        assert result is None

    async def test_reject_persists(self, async_session: AsyncSession) -> None:
        seeded = await _seed_patterns(async_session)
        repo = LearnedPatternRepository(async_session)
        await repo.reject(seeded["app1_pending"].id, reason="bad data")
        refreshed = await repo.get_by_id(seeded["app1_pending"].id)
        assert refreshed is not None
        assert refreshed.review_status == "rejected"
        assert refreshed.review_reason == "bad data"


class TestLearnedPatternRepositoryErrorHandling:
    async def test_get_by_app_id_error(self, async_session: AsyncSession) -> None:
        repo = LearnedPatternRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("connection lost")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_LP_BY_APP_FAILED"):
            await repo.get_by_app_id("com.example.app1")
        async_session.execute = original_execute

    async def test_approve_error(self, async_session: AsyncSession) -> None:
        repo = LearnedPatternRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("db error")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_GET_BY_ID_FAILED"):
            await repo.approve("some-id")
        async_session.execute = original_execute

    async def test_reject_error(self, async_session: AsyncSession) -> None:
        repo = LearnedPatternRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("db error")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_GET_BY_ID_FAILED"):
            await repo.reject("some-id", reason="reason")
        async_session.execute = original_execute
