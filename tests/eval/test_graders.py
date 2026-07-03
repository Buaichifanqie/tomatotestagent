from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from testagent.eval.graders.base import BaseGrader
from testagent.eval.graders.llm_rubric import LlmRubricGrader
from testagent.eval.graders.state_check import StateCheckGrader
from testagent.eval.models import (
    EvalTask,
    GraderConfig,
    GraderResult,
    Transcript,
    TranscriptSummary,
)


# ── BaseGrader ──────────────────────────────────────────────────────────────────


class TestBaseGrader:
    """Test BaseGrader abstract behaviour."""

    def test_abstract_cannot_instantiate(self) -> None:
        """BaseGrader cannot be instantiated directly because grade is abstract."""
        config = GraderConfig(grader_type="base")
        with pytest.raises(TypeError):
            BaseGrader(config)  # type: ignore[abstract]


# ── StateCheckGrader ────────────────────────────────────────────────────────────


class TestStateCheckGrader:
    """Test StateCheckGrader."""

    @pytest.fixture
    def grader(self) -> StateCheckGrader:
        config = GraderConfig(grader_type="state_check", expect={})
        return StateCheckGrader(config)

    @pytest.fixture
    def empty_task(self) -> EvalTask:
        return EvalTask(id="test", description="", instruction="")

    @pytest.mark.asyncio
    async def test_no_expectations(self) -> None:
        """Empty config → passed=True, score=1.0."""
        config = GraderConfig(grader_type="state_check")
        grader = StateCheckGrader(config)
        transcript = Transcript(messages=[], summary=None)
        task = EvalTask(id="t1", description="", instruction="")

        result = await grader.grade(transcript, task)

        assert result.passed is True
        assert result.score == 1.0
        assert result.grader_type == "state_check"

    @pytest.mark.asyncio
    async def test_exact_page_match(self) -> None:
        """final_page matches expected current_page → passed."""
        config = GraderConfig(
            grader_type="state_check",
            expect={"current_page": "page_home"},
        )
        grader = StateCheckGrader(config)
        summary = TranscriptSummary(final_page="page_home")
        transcript = Transcript(messages=[], summary=summary)
        task = EvalTask(id="t1", description="", instruction="")

        result = await grader.grade(transcript, task)

        assert result.passed is True
        assert result.score == 1.0

    @pytest.mark.asyncio
    async def test_page_mismatch(self) -> None:
        """final_page != expected current_page → failed."""
        config = GraderConfig(
            grader_type="state_check",
            expect={"current_page": "page_home"},
        )
        grader = StateCheckGrader(config)
        summary = TranscriptSummary(final_page="page_search")
        transcript = Transcript(messages=[], summary=summary)
        task = EvalTask(id="t1", description="", instruction="")

        result = await grader.grade(transcript, task)

        assert result.passed is False
        assert result.score == 0.0
        assert "page_home" in result.details
        assert "page_search" in result.details

    @pytest.mark.asyncio
    async def test_elements_present(self) -> None:
        """Element name found in tool result text → passed."""
        config = GraderConfig(
            grader_type="state_check",
            expect={"elements_present": ["search-bar", "login-button"]},
        )
        grader = StateCheckGrader(config)
        messages = [
            {
                "role": "tool",
                "content": "Found search-bar and login-button on the page.",
            },
        ]
        transcript = Transcript(messages=messages, summary=TranscriptSummary())
        task = EvalTask(id="t1", description="", instruction="")

        result = await grader.grade(transcript, task)

        assert result.passed is True
        assert result.score == 1.0

    @pytest.mark.asyncio
    async def test_elements_missing(self) -> None:
        """Element name not found in tool result text → failed."""
        config = GraderConfig(
            grader_type="state_check",
            expect={"elements_present": ["search-bar", "missing-element"]},
        )
        grader = StateCheckGrader(config)
        messages = [
            {
                "role": "tool",
                "content": "Found search-bar and login-button on the page.",
            },
        ]
        transcript = Transcript(messages=messages, summary=TranscriptSummary())
        task = EvalTask(id="t1", description="", instruction="")

        result = await grader.grade(transcript, task)

        assert result.passed is False
        assert result.score == 0.0
        assert "missing-element" in result.details


# ── Mock LLM for testing ──────────────────────────────────────────────────────────


class MockLLM:
    """Mock LLM provider that returns a canned response."""

    def __init__(self, response_text: str, **usage_kwargs: int) -> None:
        self.response_text = response_text
        self._usage = type(
            "MockUsage",
            (),
            {
                "input_tokens": usage_kwargs.get("input_tokens", 50),
                "output_tokens": usage_kwargs.get("output_tokens", 20),
                "total_tokens": usage_kwargs.get("total_tokens", 70),
            },
        )()

    async def chat(self, **kwargs: object) -> object:
        class MockResp:
            content = [{"type": "text", "text": ""}]
            usage = None

        resp = MockResp()
        resp.content = [{"type": "text", "text": self.response_text}]
        resp.usage = self._usage
        return resp


# ── LlmRubricGrader ──────────────────────────────────────────────────────────────


class TestLlmRubricGrader:
    """Test LlmRubricGrader (LLM-as-Judge)."""

    @pytest.mark.asyncio
    async def test_rubric_pass(self) -> None:
        """Mock returns score=5 → passed=True, score=1.0."""
        mock = MockLLM('{"score": 5, "passed": true, "reason": "Perfect execution"}')
        config = GraderConfig(grader_type="llm_rubric", rubric="Test rubric")
        grader = LlmRubricGrader(config, llm_provider=mock)
        transcript = Transcript(messages=[], summary=TranscriptSummary())
        task = EvalTask(id="t1", description="Test task", instruction="")

        result = await grader.grade(transcript, task)

        assert result.passed is True
        assert result.score == 1.0
        assert result.grader_type == "llm_rubric"

    @pytest.mark.asyncio
    async def test_rubric_fail(self) -> None:
        """Mock returns score=2 → passed=False, score=0.4."""
        mock = MockLLM('{"score": 2, "passed": false, "reason": "Bad execution"}')
        config = GraderConfig(grader_type="llm_rubric", rubric="Test rubric")
        grader = LlmRubricGrader(config, llm_provider=mock)
        transcript = Transcript(messages=[], summary=TranscriptSummary())
        task = EvalTask(id="t1", description="Test task", instruction="")

        result = await grader.grade(transcript, task)

        assert result.passed is False
        assert result.score == 0.4
        assert result.grader_type == "llm_rubric"

    @pytest.mark.asyncio
    async def test_rubric_parse_error(self) -> None:
        """Mock returns non-JSON text → passed=False, score=0.0, details='parse error'."""
        mock = MockLLM("This is not JSON at all")
        config = GraderConfig(grader_type="llm_rubric", rubric="Test rubric")
        grader = LlmRubricGrader(config, llm_provider=mock)
        transcript = Transcript(messages=[], summary=TranscriptSummary())
        task = EvalTask(id="t1", description="Test task", instruction="")

        result = await grader.grade(transcript, task)

        assert result.passed is False
        assert result.score == 0.0
        assert result.details == "parse error"

    @pytest.mark.asyncio
    async def test_rubric_with_recorder(self) -> None:
        """Verify recorder.record_usage() was called with right token counts."""
        mock = MockLLM(
            '{"score": 5, "passed": true, "reason": "Perfect"}',
            input_tokens=50,
            output_tokens=20,
            total_tokens=70,
        )
        recorder = MagicMock(spec=["record_usage"])
        config = GraderConfig(grader_type="llm_rubric", rubric="Test rubric")
        grader = LlmRubricGrader(config, llm_provider=mock, recorder=recorder)
        transcript = Transcript(messages=[], summary=TranscriptSummary())
        task = EvalTask(id="t1", description="Test task", instruction="")

        result = await grader.grade(transcript, task)

        assert result.passed is True
        recorder.record_usage.assert_called_once_with(
            input_tokens=50, output_tokens=20, total_tokens=70
        )

    @pytest.mark.asyncio
    async def test_rubric_no_recorder(self) -> None:
        """Without recorder, should not crash."""
        mock = MockLLM('{"score": 3, "passed": false, "reason": "Ok"}')
        config = GraderConfig(grader_type="llm_rubric", rubric="Test rubric")
        grader = LlmRubricGrader(config, llm_provider=mock)
        transcript = Transcript(messages=[], summary=TranscriptSummary())
        task = EvalTask(id="t1", description="Test task", instruction="")

        result = await grader.grade(transcript, task)

        assert result.passed is False
        assert result.score == 0.6
        assert result.grader_type == "llm_rubric"
