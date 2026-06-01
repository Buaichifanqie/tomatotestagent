from __future__ import annotations

from datetime import UTC, datetime

import pytest

from testagent.models.retrieval_trace import RetrievalTrace


class TestRetrievalTraceModel:
    """Test RetrievalTrace ORM model fields."""

    def test_model_has_required_fields(self):
        trace = RetrievalTrace(
            app_id="com.bilibili.app",
            query="测试搜索功能",
            query_stage="single_batch",
            retrieved_items=[{"id": "doc1", "score": 0.9}],
            generated_case_ids=["TC-001", "TC-002"],
        )
        assert trace.app_id == "com.bilibili.app"
        assert trace.query == "测试搜索功能"
        assert trace.query_stage == "single_batch"
        assert trace.retrieved_items == [{"id": "doc1", "score": 0.9}]
        assert trace.generated_case_ids == ["TC-001", "TC-002"]
        assert trace.adoption_score is None

    def test_model_defaults(self):
        trace = RetrievalTrace(
            app_id="test",
            query="q",
            query_stage="single_batch",
        )
        # id and created_at are column defaults (fire on INSERT), not instance defaults
        assert trace.retrieved_items is None
        assert trace.generated_case_ids is None
        assert trace.adoption_score is None
