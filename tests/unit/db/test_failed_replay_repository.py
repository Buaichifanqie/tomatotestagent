from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import UTC, datetime

from testagent.db.repository import FailedReplayRepository
from testagent.models.failed_replay import FailedCaseReplay


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def repo(mock_session):
    return FailedReplayRepository(mock_session)


class TestFailedReplayRepository:
    """FailedReplayRepository query methods."""

    def test_model_class_is_set(self, repo):
        assert repo._model_class is FailedCaseReplay

    @pytest.mark.asyncio
    async def test_get_pending_unresolved(self, repo, mock_session):
        mock_record = MagicMock()
        mock_record.resolved = 0
        mock_record.original_status = "FAILED"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_record]
        mock_session.execute = AsyncMock(return_value=mock_result)

        results = await repo.get_pending("tv.danmaku.bili")
        assert len(results) == 1
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pending_filters_resolved(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        results = await repo.get_pending("tv.danmaku.bili")
        assert results == []

    @pytest.mark.asyncio
    async def test_get_by_app_and_case_id(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_by_app_and_case_id("tv.danmaku.bili", "TC-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_creates_new(self, repo, mock_session):
        repo.get_by_app_and_case_id = AsyncMock(return_value=None)
        repo.create = AsyncMock(return_value=MagicMock())

        now = datetime.now(UTC)
        entity = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-001",
            test_case_id="TC-001",
            test_case_name="test",
            original_status="FAILED",
            test_case_data={},
            original_run_timestamp=now,
        )
        await repo.upsert(entity)
        repo.create.assert_called_once_with(entity)

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, repo, mock_session):
        existing = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-old",
            test_case_id="TC-001",
            test_case_name="old name",
            original_status="FAILED",
            test_case_data={},
            original_run_timestamp=datetime.now(UTC),
        )
        existing.id = "existing-id"
        existing.replay_count = 2

        repo.get_by_app_and_case_id = AsyncMock(return_value=existing)
        repo.update = AsyncMock(return_value=existing)

        now = datetime.now(UTC)
        new_entity = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-new",
            test_case_id="TC-001",
            test_case_name="new name",
            original_status="FAILED",
            test_case_data={"id": "TC-001"},
            original_run_timestamp=now,
        )
        await repo.upsert(new_entity)
        repo.update.assert_called_once()
        call_args = repo.update.call_args
        assert call_args[0][0] == "existing-id"

    @pytest.mark.asyncio
    async def test_cleanup_resolved_old(self, repo, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        deleted = await repo.cleanup_resolved("tv.danmaku.bili", days=30)
        assert isinstance(deleted, int)
