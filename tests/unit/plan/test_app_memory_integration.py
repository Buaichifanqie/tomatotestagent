from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from testagent.rag.app_memory import format_retrieved_cases_for_prompt
from testagent.rag.pipeline import RAGResult


class TestRetrievalInjection:
    """Test that historical cases are injected into the generation prompt."""

    def test_format_empty_results_returns_empty(self):
        result = format_retrieved_cases_for_prompt([])
        assert result == ""

    def test_format_with_results_contains_context(self):
        results = [
            RAGResult(
                doc_id="abc",
                content="用例: TC-001 正常搜索\n步骤: 1. launch 2. tap 搜索框",
                score=0.9,
                metadata={"app_package": "tv.danmaku.bili"},
            )
        ]
        formatted = format_retrieved_cases_for_prompt(results)
        assert "历史测试用例" in formatted
        assert "TC-001" in formatted
        assert "90%" in formatted

    def test_enhanced_prd_includes_history_when_available(self):
        """Simulate the enhanced_prd construction logic from plan.py."""
        prd_text = "测试哔哩哔哩搜索功能"
        app_package = "tv.danmaku.bili"

        # Simulate RAG returning historical cases
        mock_results = [
            RAGResult(
                doc_id="x",
                content="用例: TC-SEARCH-001 搜索视频",
                score=0.85,
                metadata={},
            )
        ]
        history_context = format_retrieved_cases_for_prompt(mock_results)

        # Build enhanced_prd the same way plan.py will
        enhanced_prd = prd_text
        if history_context:
            enhanced_prd = history_context + "\n\n" + enhanced_prd

        assert "TC-SEARCH-001" in enhanced_prd
        assert "测试哔哩哔哩搜索功能" in enhanced_prd
        # History comes BEFORE the user's requirement
        assert enhanced_prd.index("TC-SEARCH-001") < enhanced_prd.index("测试哔哩哔哩搜索功能")

    def test_enhanced_prd_unchanged_when_no_history(self):
        """When RAG returns nothing, enhanced_prd should be unchanged."""
        prd_text = "测试哔哩哔哩搜索功能"
        history_context = format_retrieved_cases_for_prompt([])

        enhanced_prd = prd_text
        if history_context:
            enhanced_prd = history_context + "\n\n" + enhanced_prd

        assert enhanced_prd == prd_text
