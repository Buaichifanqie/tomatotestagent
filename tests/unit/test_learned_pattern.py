from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from testagent.models import Base
from testagent.models.learned_pattern import LearnedPattern


PATTERN_TYPES = ("behavior", "workaround", "anti_pattern", "failure_mode")
SOURCE_TYPES = ("modification_delta", "failure_analysis", "manual_entry")
SCOPES = ("app_local", "global")
REVIEW_STATUSES = ("pending", "approved", "rejected")


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as sess:
        yield sess


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
