from __future__ import annotations

import json

import pytest

from testagent.plan.models import TestCase, TestStep
from testagent.rag.app_memory import (
    serialize_cases_for_storage,
    format_retrieved_cases_for_prompt,
    format_learned_patterns_for_prompt,
    filter_by_functional_relevance,
    _extract_module_from_id,
    _extract_function_keywords,
    _is_functionally_relevant,
)
from testagent.rag.pipeline import RAGResult


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
        assert "---" in result

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
        assert "92%" in result

    def test_multiple_results_numbered(self):
        from testagent.rag.pipeline import RAGResult

        results = [
            RAGResult(doc_id="a", content="用例1", score=0.9, metadata={}),
            RAGResult(doc_id="b", content="用例2", score=0.8, metadata={}),
        ]
        result = format_retrieved_cases_for_prompt(results)
        assert "用例1" in result
        assert "用例2" in result
        assert "历史用例 1" in result
        assert "历史用例 2" in result
        assert "90%" in result
        assert "80%" in result


class TestFormatLearnedPatternsForPrompt:
    """format_learned_patterns_for_prompt formats RAG results for prompt injection."""

    def test_empty_results_returns_empty_string(self):
        result = format_learned_patterns_for_prompt([])
        assert result == ""

    def test_single_pattern_with_confidence_stars(self):
        from testagent.rag.pipeline import RAGResult
        results = [
            RAGResult(
                doc_id="p1",
                content="B站搜索页会保留历史搜索词，需先清除",
                score=0.9,
                metadata={"pattern_type": "behavior", "confidence": 0.8, "app_version": "7.45.0"},
            )
        ]
        result = format_learned_patterns_for_prompt(results)
        assert "B站搜索页会保留历史搜索词" in result
        assert "★★★★" in result or "★★★" in result  # 0.8 confidence -> 4 stars
        assert "behavior" in result or "行为模式" in result

    def test_multiple_patterns_numbered(self):
        from testagent.rag.pipeline import RAGResult
        results = [
            RAGResult(doc_id="a", content="模式1", score=0.9, metadata={"pattern_type": "behavior", "confidence": 0.9}),
            RAGResult(doc_id="b", content="模式2", score=0.8, metadata={"pattern_type": "workaround", "confidence": 0.7}),
        ]
        result = format_learned_patterns_for_prompt(results)
        assert "模式1" in result
        assert "模式2" in result
        assert "经验 1" in result or "Pattern 1" in result


# ── Functional relevance filtering ──────────────────────────────────────────


class TestExtractModuleFromId:
    def test_standard_id(self):
        assert _extract_module_from_id("TC-VIDEO-007") == "VIDEO"

    def test_search_id(self):
        assert _extract_module_from_id("TC-SEARCH-016") == "SEARCH"

    def test_flow_id(self):
        assert _extract_module_from_id("TC-FLOW-001") == "FLOW"

    def test_no_match(self):
        assert _extract_module_from_id("abc123") is None

    def test_empty_string(self):
        assert _extract_module_from_id("") is None


class TestExtractFunctionKeywords:
    def test_simple_search(self):
        # "哔哩哔哩搜索" is extracted as a whole; substring match in _is_functionally_relevant handles it
        assert _extract_function_keywords("测试哔哩哔哩搜索功能") == ["哔哩哔哩搜索"]

    def test_multiple_keywords(self):
        assert _extract_function_keywords("测试登录和支付") == ["登录", "支付"]

    def test_with_chinese_punctuation(self):
        assert _extract_function_keywords("测试搜索，视频播放") == ["搜索", "视频播放"]

    def test_english_prefix(self):
        assert _extract_function_keywords("test search functionality") == ["search"]

    def test_no_prefix(self):
        assert _extract_function_keywords("搜索功能") == ["搜索"]


class TestIsFunctionallyRelevant:
    def test_matching_module(self):
        assert _is_functionally_relevant(["搜索"], [], "SEARCH") is True

    def test_mismatching_module(self):
        assert _is_functionally_relevant(["搜索"], [], "VIDEO") is False

    def test_matching_tag(self):
        assert _is_functionally_relevant(["搜索"], ["search", "bilibili"], None) is True

    def test_no_module_no_tags_kept(self):
        assert _is_functionally_relevant(["搜索"], [], None) is True

    def test_empty_keywords_kept(self):
        assert _is_functionally_relevant([], [], "VIDEO") is True

    def test_substring_match_in_compound_keyword(self):
        """'哔哩哔哩搜索' contains '搜索' → should match SEARCH module."""
        assert _is_functionally_relevant(["哔哩哔哩搜索"], [], "SEARCH") is True

    def test_substring_mismatch_irrelevant_module(self):
        """'哔哩哔哩搜索' does not map to VIDEO."""
        assert _is_functionally_relevant(["哔哩哔哩搜索"], [], "VIDEO") is False


class TestFilterByFunctionalRelevance:
    def _make_case(self, doc_id: str, collection: str = "app_test_cases") -> RAGResult:
        return RAGResult(
            doc_id=doc_id,
            content=f"content for {doc_id}",
            score=0.9,
            metadata={"collection": collection},
        )

    def test_empty_results(self):
        assert filter_by_functional_relevance([], "测试搜索功能") == []

    def test_filters_out_irrelevant_cases(self):
        results = [
            self._make_case("TC-SEARCH-001"),
            self._make_case("TC-VIDEO-007"),
            self._make_case("TC-SEARCH-002"),
        ]
        filtered = filter_by_functional_relevance(results, "测试搜索功能")
        ids = [r.doc_id for r in filtered]
        assert "TC-SEARCH-001" in ids
        assert "TC-SEARCH-002" in ids
        assert "TC-VIDEO-007" not in ids

    def test_keeps_non_case_results(self):
        results = [
            self._make_case("TC-VIDEO-007"),
            self._make_case("pattern-1", collection="app_learned_patterns"),
            self._make_case("doc-1", collection="app_documentation"),
        ]
        filtered = filter_by_functional_relevance(results, "测试搜索功能")
        ids = [r.doc_id for r in filtered]
        assert "TC-VIDEO-007" not in ids
        assert "pattern-1" in ids
        assert "doc-1" in ids

    def test_keeps_cases_without_module(self):
        results = [
            RAGResult(doc_id="abc", content="generic", score=0.5, metadata={"collection": "app_test_cases"}),
        ]
        filtered = filter_by_functional_relevance(results, "测试搜索功能")
        assert len(filtered) == 1

    def test_no_intent_returns_all(self):
        results = [
            self._make_case("TC-SEARCH-001"),
            self._make_case("TC-VIDEO-007"),
        ]
        filtered = filter_by_functional_relevance(results, "")
        assert len(filtered) == 2
