from __future__ import annotations

import json

import pytest

from testagent.plan.models import TestCase, TestStep
from testagent.rag.app_memory import serialize_cases_for_storage, format_retrieved_cases_for_prompt


class TestSerializeCasesForStorage:
    """serialize_cases_for_storage converts TestCase list to searchable text."""

    def test_empty_list(self):
        result = serialize_cases_for_storage([])
        assert result == ""

    def test_single_case(self):
        cases = [
            TestCase(
                id="TC-SEARCH-001",
                title="正常搜索",
                priority="P0",
                is_core=True,
                steps=[
                    TestStep(step=1, action="launch", target="tv.danmaku.bili"),
                    TestStep(step=2, action="tap", target="搜索框"),
                    TestStep(step=3, action="type", target="搜索框", value="测试"),
                ],
            )
        ]
        result = serialize_cases_for_storage(cases)
        assert "TC-SEARCH-001" in result
        assert "正常搜索" in result
        assert "P0" in result
        assert "launch" in result
        assert "tv.danmaku.bili" in result
        assert "type" in result
        assert "测试" in result

    def test_multiple_cases(self):
        cases = [
            TestCase(id="TC-001", title="用例1", steps=[TestStep(step=1, action="tap", target="按钮")]),
            TestCase(id="TC-002", title="用例2", steps=[TestStep(step=1, action="type", target="输入框", value="文本")]),
        ]
        result = serialize_cases_for_storage(cases)
        assert "TC-001" in result
        assert "TC-002" in result
        # Cases are separated by a delimiter
        assert "---" in result or "\n\n" in result

    def test_output_is_valid_text(self):
        """Output should be plain text suitable for RAG chunking, not JSON."""
        cases = [
            TestCase(id="TC-001", title="测试", steps=[TestStep(step=1, action="tap", target="X")]),
        ]
        result = serialize_cases_for_storage(cases)
        # Should NOT be raw JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)


class TestFormatRetrievedCasesForPrompt:
    """format_retrieved_cases_for_prompt formats RAG results for prompt injection."""

    def test_empty_results(self):
        result = format_retrieved_cases_for_prompt([])
        assert result == ""

    def test_single_result(self):
        from testagent.rag.pipeline import RAGResult

        results = [
            RAGResult(
                doc_id="abc123",
                content="用例: TC-001 正常搜索\n步骤: 1. launch 2. tap 搜索框",
                score=0.92,
                metadata={"app_package": "tv.danmaku.bili", "plan_name": "test"},
            )
        ]
        result = format_retrieved_cases_for_prompt(results)
        assert "TC-001" in result
        assert "0.92" in result or "92%" in result

    def test_multiple_results_numbered(self):
        from testagent.rag.pipeline import RAGResult

        results = [
            RAGResult(doc_id="a", content="用例1", score=0.9, metadata={}),
            RAGResult(doc_id="b", content="用例2", score=0.8, metadata={}),
        ]
        result = format_retrieved_cases_for_prompt(results)
        assert "用例1" in result
        assert "用例2" in result
