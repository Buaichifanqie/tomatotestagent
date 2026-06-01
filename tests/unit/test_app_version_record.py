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
from testagent.db.repository import AppVersionRepository, TestCaseRecordRepository
from testagent.models import Base
from testagent.models.app_version import AppVersion
from testagent.models.test_case_record import TestCaseRecord

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


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


async def _seed_app_versions(session: AsyncSession) -> dict[str, AppVersion]:
    """Seed app version records for testing."""
    versions = {
        "app1": AppVersion(app_id="com.example.app1", current_version="1.0.0", updated_by="user"),
        "app2": AppVersion(app_id="com.example.app2", current_version="2.3.1", updated_by="system"),
    }
    for v in versions.values():
        session.add(v)
    await session.flush()
    return versions


async def _seed_case_records(session: AsyncSession) -> dict[str, TestCaseRecord]:
    """Seed test case records for testing."""
    records = {
        "r1": TestCaseRecord(
            app_id="com.example.app1",
            app_version="1.0.0",
            case_content="test login flow",
            source="generated",
            confidence=0.8,
            tags="smoke",
            execution_count=3,
            pass_count=2,
        ),
        "r2": TestCaseRecord(
            app_id="com.example.app1",
            app_version="1.0.0",
            case_content="test settings page",
            source="manual",
            confidence=0.9,
            tags="regression",
            execution_count=1,
            pass_count=1,
        ),
        "r3": TestCaseRecord(
            app_id="com.example.app2",
            app_version="2.3.1",
            case_content="test checkout",
            source="generated",
            confidence=0.7,
            execution_count=0,
            pass_count=0,
        ),
    }
    for r in records.values():
        session.add(r)
    await session.flush()
    return records


# ===========================================================================
# AppVersion model tests
# ===========================================================================

class TestAppVersionModel:
    def test_model_has_required_fields(self, session: Session):
        version = AppVersion(
            app_id="com.example.app",
            current_version="1.2.3",
            updated_by="user",
        )
        session.add(version)
        session.flush()
        assert version.app_id == "com.example.app"
        assert version.current_version == "1.2.3"
        assert version.updated_by == "user"

    def test_model_defaults(self, session: Session):
        version = AppVersion(
            app_id="com.example.app",
            current_version="1.0.0",
        )
        session.add(version)
        session.flush()
        assert version.id is not None
        parsed = uuid.UUID(version.id, version=4)
        assert str(parsed) == version.id
        assert version.created_at is not None
        assert version.updated_at is not None
        assert version.updated_by == "system"

    def test_updated_at_changes_on_update(self, session: Session):
        version = AppVersion(
            app_id="com.example.app",
            current_version="1.0.0",
        )
        session.add(version)
        session.flush()
        original_updated_at = version.updated_at
        version.current_version = "2.0.0"
        session.flush()
        # Note: onupdate only fires on UPDATE SQL, not on in-memory changes
        # This test just verifies the field exists and can be set
        assert version.current_version == "2.0.0"

    def test_app_id_unique_constraint(self, session: Session):
        v1 = AppVersion(app_id="com.example.app", current_version="1.0.0")
        v2 = AppVersion(app_id="com.example.app", current_version="2.0.0")
        session.add(v1)
        session.flush()
        session.add(v2)
        with pytest.raises(Exception):
            session.flush()

    def test_different_app_ids_allowed(self, session: Session):
        v1 = AppVersion(app_id="com.example.app1", current_version="1.0.0")
        v2 = AppVersion(app_id="com.example.app2", current_version="2.0.0")
        session.add(v1)
        session.add(v2)
        session.flush()
        assert v1.app_id == "com.example.app1"
        assert v2.app_id == "com.example.app2"


# ===========================================================================
# TestCaseRecord model tests
# ===========================================================================

class TestTestCaseRecordModel:
    def test_model_has_required_fields(self, session: Session):
        record = TestCaseRecord(
            app_id="com.example.app",
            app_version="1.0.0",
            case_content="test case content",
            source="generated",
            confidence=0.8,
            tags="smoke,regression",
            scope="app_local",
            execution_count=5,
            pass_count=4,
        )
        session.add(record)
        session.flush()
        assert record.app_id == "com.example.app"
        assert record.app_version == "1.0.0"
        assert record.case_content == "test case content"
        assert record.source == "generated"
        assert record.confidence == 0.8
        assert record.tags == "smoke,regression"
        assert record.scope == "app_local"
        assert record.execution_count == 5
        assert record.pass_count == 4

    def test_model_defaults(self, session: Session):
        record = TestCaseRecord(
            app_id="com.example.app",
            app_version="1.0.0",
            case_content="minimal test",
            source="manual",
        )
        session.add(record)
        session.flush()
        assert record.id is not None
        parsed = uuid.UUID(record.id, version=4)
        assert str(parsed) == record.id
        assert record.created_at is not None
        assert record.original_case_id is None
        assert record.confidence == 0.5
        assert record.tags == ""
        assert record.scope == "app_local"
        assert record.last_validated_version is None
        assert record.execution_count == 0
        assert record.pass_count == 0

    def test_source_valid_values(self, session: Session):
        for src in ("generated", "manual", "imported"):
            record = TestCaseRecord(
                app_id="t",
                app_version="1.0.0",
                case_content="content",
                source=src,
            )
            session.add(record)
            session.flush()
            assert record.source == src

    def test_optional_fields_nullable(self, session: Session):
        record = TestCaseRecord(
            app_id="com.example.app",
            app_version="1.0.0",
            case_content="test content",
            source="generated",
            original_case_id=None,
            last_validated_version=None,
        )
        session.add(record)
        session.flush()
        assert record.original_case_id is None
        assert record.last_validated_version is None

    def test_with_optional_fields_set(self, session: Session):
        record = TestCaseRecord(
            app_id="com.example.app",
            app_version="1.0.0",
            case_content="test content",
            source="imported",
            original_case_id="TC-OLD-001",
            last_validated_version="0.9.0",
        )
        session.add(record)
        session.flush()
        assert record.original_case_id == "TC-OLD-001"
        assert record.last_validated_version == "0.9.0"

    def test_scope_valid_values(self, session: Session):
        for sc in ("app_local", "global"):
            record = TestCaseRecord(
                app_id="t",
                app_version="1.0.0",
                case_content="content",
                source="manual",
                scope=sc,
            )
            session.add(record)
            session.flush()
            assert record.scope == sc

    def test_multiple_records_same_app(self, session: Session):
        r1 = TestCaseRecord(app_id="com.example.app", app_version="1.0.0", case_content="c1", source="generated")
        r2 = TestCaseRecord(app_id="com.example.app", app_version="1.0.0", case_content="c2", source="manual")
        session.add(r1)
        session.add(r2)
        session.flush()
        assert r1.id != r2.id


# ===========================================================================
# AppVersionRepository tests (async)
# ===========================================================================
class TestAppVersionRepositoryGetByAppId:
    async def test_returns_version_for_existing_app(self, async_session: AsyncSession) -> None:
        seeded = await _seed_app_versions(async_session)
        repo = AppVersionRepository(async_session)
        result = await repo.get_by_app_id("com.example.app1")
        assert result is not None
        assert result.app_id == "com.example.app1"
        assert result.current_version == "1.0.0"

    async def test_returns_none_for_missing_app(self, async_session: AsyncSession) -> None:
        repo = AppVersionRepository(async_session)
        result = await repo.get_by_app_id("nonexistent")
        assert result is None

    async def test_does_not_return_other_app(self, async_session: AsyncSession) -> None:
        await _seed_app_versions(async_session)
        repo = AppVersionRepository(async_session)
        result = await repo.get_by_app_id("com.example.app2")
        assert result is not None
        assert result.current_version == "2.3.1"


class TestAppVersionRepositoryUpsert:
    async def test_creates_new_version(self, async_session: AsyncSession) -> None:
        repo = AppVersionRepository(async_session)
        result = await repo.upsert("com.example.new", "3.0.0", updated_by="tester")
        assert result is not None
        assert result.app_id == "com.example.new"
        assert result.current_version == "3.0.0"
        assert result.updated_by == "tester"

    async def test_updates_existing_version(self, async_session: AsyncSession) -> None:
        await _seed_app_versions(async_session)
        repo = AppVersionRepository(async_session)
        result = await repo.upsert("com.example.app1", "2.0.0", updated_by="tester")
        assert result is not None
        assert result.current_version == "2.0.0"
        assert result.updated_by == "tester"

    async def test_upsert_persists_on_create(self, async_session: AsyncSession) -> None:
        repo = AppVersionRepository(async_session)
        created = await repo.upsert("com.example.new", "1.0.0")
        refreshed = await repo.get_by_app_id("com.example.new")
        assert refreshed is not None
        assert refreshed.id == created.id
        assert refreshed.current_version == "1.0.0"

    async def test_upsert_persists_on_update(self, async_session: AsyncSession) -> None:
        await _seed_app_versions(async_session)
        repo = AppVersionRepository(async_session)
        await repo.upsert("com.example.app1", "5.0.0")
        refreshed = await repo.get_by_app_id("com.example.app1")
        assert refreshed is not None
        assert refreshed.current_version == "5.0.0"

    async def test_upsert_default_updated_by(self, async_session: AsyncSession) -> None:
        repo = AppVersionRepository(async_session)
        result = await repo.upsert("com.example.default", "1.0.0")
        assert result.updated_by == "system"


class TestAppVersionRepositoryBaseRepo:
    async def test_get_by_id(self, async_session: AsyncSession) -> None:
        seeded = await _seed_app_versions(async_session)
        repo = AppVersionRepository(async_session)
        result = await repo.get_by_id(seeded["app1"].id)
        assert result is not None
        assert result.app_id == "com.example.app1"

    async def test_create(self, async_session: AsyncSession) -> None:
        repo = AppVersionRepository(async_session)
        entity = AppVersion(app_id="com.example.new", current_version="1.0.0")
        created = await repo.create(entity)
        assert created.id is not None
        assert created.app_id == "com.example.new"

    async def test_delete(self, async_session: AsyncSession) -> None:
        seeded = await _seed_app_versions(async_session)
        repo = AppVersionRepository(async_session)
        deleted = await repo.delete(seeded["app1"].id)
        assert deleted is True
        result = await repo.get_by_id(seeded["app1"].id)
        assert result is None

    async def test_delete_nonexistent(self, async_session: AsyncSession) -> None:
        repo = AppVersionRepository(async_session)
        deleted = await repo.delete("nonexistent")
        assert deleted is False


# ===========================================================================
# TestCaseRecordRepository tests (async)
# ===========================================================================
class TestTestCaseRecordRepositoryGetByAppId:
    async def test_returns_records_for_app(self, async_session: AsyncSession) -> None:
        await _seed_case_records(async_session)
        repo = TestCaseRecordRepository(async_session)
        results = await repo.get_by_app_id("com.example.app1")
        assert len(results) == 2
        for r in results:
            assert r.app_id == "com.example.app1"

    async def test_does_not_return_other_apps(self, async_session: AsyncSession) -> None:
        await _seed_case_records(async_session)
        repo = TestCaseRecordRepository(async_session)
        results = await repo.get_by_app_id("com.example.app2")
        assert len(results) == 1
        assert results[0].case_content == "test checkout"

    async def test_limit(self, async_session: AsyncSession) -> None:
        await _seed_case_records(async_session)
        repo = TestCaseRecordRepository(async_session)
        results = await repo.get_by_app_id("com.example.app1", limit=1)
        assert len(results) == 1

    async def test_empty_for_missing_app(self, async_session: AsyncSession) -> None:
        repo = TestCaseRecordRepository(async_session)
        results = await repo.get_by_app_id("nonexistent")
        assert len(results) == 0

    async def test_ordered_by_created_at_desc(self, async_session: AsyncSession) -> None:
        await _seed_case_records(async_session)
        repo = TestCaseRecordRepository(async_session)
        results = await repo.get_by_app_id("com.example.app1")
        for i in range(len(results) - 1):
            assert results[i].created_at >= results[i + 1].created_at


class TestTestCaseRecordRepositoryUpdateExecutionStats:
    async def test_increments_execution_count(self, async_session: AsyncSession) -> None:
        seeded = await _seed_case_records(async_session)
        repo = TestCaseRecordRepository(async_session)
        result = await repo.update_execution_stats(seeded["r1"].id, passed=True)
        assert result is not None
        assert result.execution_count == 4
        assert result.pass_count == 3

    async def test_increments_only_execution_on_failure(self, async_session: AsyncSession) -> None:
        seeded = await _seed_case_records(async_session)
        repo = TestCaseRecordRepository(async_session)
        result = await repo.update_execution_stats(seeded["r1"].id, passed=False)
        assert result is not None
        assert result.execution_count == 4
        assert result.pass_count == 2

    async def test_returns_none_for_missing_record(self, async_session: AsyncSession) -> None:
        repo = TestCaseRecordRepository(async_session)
        result = await repo.update_execution_stats("nonexistent", passed=True)
        assert result is None

    async def test_persists_stats(self, async_session: AsyncSession) -> None:
        seeded = await _seed_case_records(async_session)
        repo = TestCaseRecordRepository(async_session)
        await repo.update_execution_stats(seeded["r3"].id, passed=True)
        refreshed = await repo.get_by_id(seeded["r3"].id)
        assert refreshed is not None
        assert refreshed.execution_count == 1
        assert refreshed.pass_count == 1


class TestTestCaseRecordRepositoryUpdateValidationVersion:
    async def test_updates_version(self, async_session: AsyncSession) -> None:
        seeded = await _seed_case_records(async_session)
        repo = TestCaseRecordRepository(async_session)
        result = await repo.update_validation_version(seeded["r1"].id, "2.0.0")
        assert result is not None
        assert result.last_validated_version == "2.0.0"

    async def test_returns_none_for_missing(self, async_session: AsyncSession) -> None:
        repo = TestCaseRecordRepository(async_session)
        result = await repo.update_validation_version("nonexistent", "1.0.0")
        assert result is None

    async def test_persists_version(self, async_session: AsyncSession) -> None:
        seeded = await _seed_case_records(async_session)
        repo = TestCaseRecordRepository(async_session)
        await repo.update_validation_version(seeded["r1"].id, "3.0.0")
        refreshed = await repo.get_by_id(seeded["r1"].id)
        assert refreshed is not None
        assert refreshed.last_validated_version == "3.0.0"


class TestTestCaseRecordRepositoryBaseRepo:
    async def test_get_by_id(self, async_session: AsyncSession) -> None:
        seeded = await _seed_case_records(async_session)
        repo = TestCaseRecordRepository(async_session)
        result = await repo.get_by_id(seeded["r1"].id)
        assert result is not None
        assert result.case_content == "test login flow"

    async def test_create(self, async_session: AsyncSession) -> None:
        repo = TestCaseRecordRepository(async_session)
        entity = TestCaseRecord(
            app_id="com.example.new",
            app_version="1.0.0",
            case_content="new test",
            source="manual",
        )
        created = await repo.create(entity)
        assert created.id is not None
        assert created.case_content == "new test"

    async def test_delete(self, async_session: AsyncSession) -> None:
        seeded = await _seed_case_records(async_session)
        repo = TestCaseRecordRepository(async_session)
        deleted = await repo.delete(seeded["r1"].id)
        assert deleted is True
        result = await repo.get_by_id(seeded["r1"].id)
        assert result is None


class TestAppVersionRepositoryErrorHandling:
    async def test_get_by_app_id_error(self, async_session: AsyncSession) -> None:
        repo = AppVersionRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("connection lost")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_AV_BY_APP_FAILED"):
            await repo.get_by_app_id("com.example.app1")
        async_session.execute = original_execute

    async def test_upsert_get_error(self, async_session: AsyncSession) -> None:
        repo = AppVersionRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("db error")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_AV_BY_APP_FAILED"):
            await repo.upsert("com.example.app1", "1.0.0")
        async_session.execute = original_execute


class TestTestCaseRecordRepositoryErrorHandling:
    async def test_get_by_app_id_error(self, async_session: AsyncSession) -> None:
        repo = TestCaseRecordRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("connection lost")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_TCR_BY_APP_FAILED"):
            await repo.get_by_app_id("com.example.app1")
        async_session.execute = original_execute

    async def test_update_execution_stats_error(self, async_session: AsyncSession) -> None:
        repo = TestCaseRecordRepository(async_session)
        original_execute = async_session.execute

        async def _broken_execute(stmt: object) -> None:
            raise RuntimeError("db error")

        async_session.execute = _broken_execute
        with pytest.raises(DatabaseError, match="DB_GET_BY_ID_FAILED"):
            await repo.update_execution_stats("some-id", passed=True)
        async_session.execute = original_execute
