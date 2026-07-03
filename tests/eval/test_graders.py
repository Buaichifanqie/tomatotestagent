from __future__ import annotations

import pytest

from testagent.eval.graders.base import BaseGrader
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
