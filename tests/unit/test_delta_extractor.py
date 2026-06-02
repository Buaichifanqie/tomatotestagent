from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.plan.delta_extractor import (
    _edit_distance,
    _has_meaningful_step_change,
    _is_meaningful_target_change,
    extract_delta_summary,
    process_deltas_and_confirm,
    should_trigger_extraction,
)
from testagent.plan.models import TestCase, TestStep


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_step(step: int, action: str = "tap", target: str = "", value: str = "") -> dict[str, Any]:
    return {"step": step, "action": action, "target": target, "value": value}


def _make_test_case(
    tc_id: str = "TC-001",
    title: str = "Test",
    steps: list[dict[str, Any]] | None = None,
) -> TestCase:
    if steps is None:
        steps = [_make_step(1, "tap", "btn_ok")]
    return TestCase(
        id=tc_id,
        title=title,
        steps=[TestStep(**s) for s in steps],
    )


# ── _edit_distance ──────────────────────────────────────────────────────────


class TestEditDistance:
    def test_identical_strings(self) -> None:
        assert _edit_distance("abc", "abc") == 0

    def test_empty_strings(self) -> None:
        assert _edit_distance("", "") == 0

    def test_one_empty(self) -> None:
        assert _edit_distance("", "abc") == 3
        assert _edit_distance("abc", "") == 3

    def test_single_substitution(self) -> None:
        assert _edit_distance("abc", "adc") == 1

    def test_single_insertion(self) -> None:
        assert _edit_distance("abc", "abcd") == 1

    def test_single_deletion(self) -> None:
        assert _edit_distance("abcd", "abc") == 1

    def test_completely_different(self) -> None:
        assert _edit_distance("abc", "xyz") == 3

    def test_symmetric(self) -> None:
        assert _edit_distance("kitten", "sitting") == _edit_distance("sitting", "kitten")

    def test_known_values(self) -> None:
        assert _edit_distance("kitten", "sitting") == 3
        assert _edit_distance("saturday", "sunday") == 3


# ── _is_meaningful_target_change ─────────────────────────────────────────────


class TestIsMeaningfulTargetChange:
    def test_both_empty_not_meaningful(self) -> None:
        assert _is_meaningful_target_change("", "") is False

    def test_one_empty_is_meaningful(self) -> None:
        assert _is_meaningful_target_change("", "btn_ok") is True
        assert _is_meaningful_target_change("btn_ok", "") is True

    def test_identical_not_meaningful(self) -> None:
        assert _is_meaningful_target_change("btn_ok", "btn_ok") is False

    def test_typo_not_meaningful(self) -> None:
        # edit_distance=1, len_diff=0
        assert _is_meaningful_target_change("btn_ok", "btn_ol") is False

    def test_short_typo_not_meaningful(self) -> None:
        # edit_distance=1, len_diff=1
        assert _is_meaningful_target_change("btn_ok", "btn_okk") is False

    def test_different_target_meaningful(self) -> None:
        assert _is_meaningful_target_change("btn_ok", "btn_cancel") is True

    def test_longer_diff_meaningful(self) -> None:
        # edit_distance > 2
        assert _is_meaningful_target_change("btn", "submit_button") is True

    def test_len_diff_exceeds_threshold(self) -> None:
        # edit_distance <= 2 but len_diff > 2
        assert _is_meaningful_target_change("ok", "ok_extra") is True


# ── _has_meaningful_step_change ──────────────────────────────────────────────


class TestHasMeaningfulStepChange:
    def test_identical_steps(self) -> None:
        old = [_make_step(1, "tap", "btn_ok")]
        new = [_make_step(1, "tap", "btn_ok")]
        assert _has_meaningful_step_change(old, new) is False

    def test_step_count_change(self) -> None:
        old = [_make_step(1, "tap", "btn_ok")]
        new = [_make_step(1, "tap", "btn_ok"), _make_step(2, "input", "field", "text")]
        assert _has_meaningful_step_change(old, new) is True

    def test_action_change(self) -> None:
        old = [_make_step(1, "tap", "btn_ok")]
        new = [_make_step(1, "scroll", "btn_ok")]
        assert _has_meaningful_step_change(old, new) is True

    def test_target_change_meaningful(self) -> None:
        old = [_make_step(1, "tap", "btn_ok")]
        new = [_make_step(1, "tap", "btn_cancel")]
        assert _has_meaningful_step_change(old, new) is True

    def test_target_change_typo_not_meaningful(self) -> None:
        old = [_make_step(1, "tap", "btn_ok")]
        new = [_make_step(1, "tap", "btn_ol")]
        assert _has_meaningful_step_change(old, new) is False

    def test_step_order_change(self) -> None:
        old = [_make_step(1, "tap", "a"), _make_step(2, "tap", "b")]
        new = [_make_step(1, "tap", "b"), _make_step(2, "tap", "a")]
        assert _has_meaningful_step_change(old, new) is True

    def test_empty_steps(self) -> None:
        assert _has_meaningful_step_change([], []) is False


# ── should_trigger_extraction ────────────────────────────────────────────────


class TestShouldTriggerExtraction:
    def test_no_changes_returns_false(self) -> None:
        original = {"TC-001": [_make_step(1, "tap", "btn_ok")]}
        modified = [_make_test_case("TC-001", steps=[_make_step(1, "tap", "btn_ok")])]
        assert should_trigger_extraction(original, modified) is False

    def test_meaningful_change_returns_true(self) -> None:
        original = {"TC-001": [_make_step(1, "tap", "btn_ok")]}
        modified = [_make_test_case("TC-001", steps=[_make_step(1, "tap", "btn_cancel")])]
        assert should_trigger_extraction(original, modified) is True

    def test_new_tc_not_in_original_returns_false(self) -> None:
        original: dict[str, list[dict]] = {}
        modified = [_make_test_case("TC-NEW", steps=[_make_step(1, "tap", "btn")])]
        # TC not in original → no old steps to compare → no meaningful change
        assert should_trigger_extraction(original, modified) is False

    def test_multiple_tcs_one_changed(self) -> None:
        original = {
            "TC-001": [_make_step(1, "tap", "btn_ok")],
            "TC-002": [_make_step(1, "scroll", "page")],
        }
        modified = [
            _make_test_case("TC-001", steps=[_make_step(1, "tap", "btn_ok")]),
            _make_test_case("TC-002", steps=[_make_step(1, "input", "field", "text")]),
        ]
        assert should_trigger_extraction(original, modified) is True


# ── extract_delta_summary ───────────────────────────────────────────────────


class TestExtractDeltaSummary:
    @pytest.mark.asyncio
    async def test_extracts_changed_cases(self) -> None:
        original = {"TC-001": [_make_step(1, "tap", "btn_ok")]}
        modified = [_make_test_case("TC-001", steps=[_make_step(1, "tap", "btn_cancel")])]
        llm_callable = AsyncMock(return_value="User prefers cancel button")

        deltas = await extract_delta_summary(original, modified, llm_callable)

        assert len(deltas) == 1
        assert deltas[0]["case_id"] == "TC-001"
        assert deltas[0]["experience"] == "User prefers cancel button"
        assert deltas[0]["old_steps"] == [_make_step(1, "tap", "btn_ok")]
        # new_steps comes from model_dump() which includes all TestStep fields
        assert deltas[0]["new_steps"][0]["action"] == "tap"
        assert deltas[0]["new_steps"][0]["target"] == "btn_cancel"
        llm_callable.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_unchanged_cases(self) -> None:
        original = {"TC-001": [_make_step(1, "tap", "btn_ok")]}
        modified = [_make_test_case("TC-001", steps=[_make_step(1, "tap", "btn_ok")])]
        llm_callable = AsyncMock()

        deltas = await extract_delta_summary(original, modified, llm_callable)

        assert len(deltas) == 0
        llm_callable.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_tc_not_in_original(self) -> None:
        original: dict[str, list[dict]] = {}
        modified = [_make_test_case("TC-NEW", steps=[_make_step(1, "tap", "btn")])]
        llm_callable = AsyncMock()

        deltas = await extract_delta_summary(original, modified, llm_callable)

        assert len(deltas) == 0

    @pytest.mark.asyncio
    async def test_llm_callable_receives_prompt(self) -> None:
        original = {"TC-001": [_make_step(1, "tap", "btn_ok")]}
        modified = [_make_test_case("TC-001", steps=[_make_step(1, "scroll", "page")])]
        llm_callable = AsyncMock(return_value="experience text")

        await extract_delta_summary(original, modified, llm_callable)

        call_args = llm_callable.call_args[0][0]
        assert "TC-001" in call_args
        assert "btn_ok" in call_args
        assert "scroll" in call_args


# ── process_deltas_and_confirm ───────────────────────────────────────────────


class TestProcessDeltasAndConfirm:
    @pytest.mark.asyncio
    async def test_no_changes_returns_early(self) -> None:
        original = {"TC-001": [_make_step(1, "tap", "btn_ok")]}
        modified = [_make_test_case("TC-001", steps=[_make_step(1, "tap", "btn_ok")])]
        llm_callable = AsyncMock()
        rag_pipeline = MagicMock()
        rag_pipeline.query = AsyncMock(return_value=[])

        await process_deltas_and_confirm(
            modified, original, "com.test.app", "test_plan", llm_callable, rag_pipeline
        )

        rag_pipeline.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_choice_writes_to_rag(self) -> None:
        original = {"TC-001": [_make_step(1, "tap", "btn_ok")]}
        modified = [_make_test_case("TC-001", steps=[_make_step(1, "tap", "btn_cancel")])]
        llm_callable = AsyncMock(return_value="User prefers cancel")
        rag_pipeline = MagicMock()
        rag_pipeline.query = AsyncMock(return_value=[])
        rag_pipeline.write_back = AsyncMock()

        with patch("testagent.plan.delta_extractor.typer") as mock_typer:
            mock_typer.prompt.return_value = "s"

            await process_deltas_and_confirm(
                modified, original, "com.test.app", "test_plan", llm_callable, rag_pipeline
            )

            rag_pipeline.write_back.assert_called_once()
            call_kwargs = rag_pipeline.write_back.call_args
            assert call_kwargs.kwargs["collection"] == "app_learned_patterns"
            assert call_kwargs.kwargs["chunk_size"] == 256

    @pytest.mark.asyncio
    async def test_edit_choice_prompts_for_text(self) -> None:
        original = {"TC-001": [_make_step(1, "tap", "btn_ok")]}
        modified = [_make_test_case("TC-001", steps=[_make_step(1, "tap", "btn_cancel")])]
        llm_callable = AsyncMock(return_value="original experience")
        rag_pipeline = MagicMock()
        rag_pipeline.query = AsyncMock(return_value=[])
        rag_pipeline.write_back = AsyncMock()

        with patch("testagent.plan.delta_extractor.typer") as mock_typer:
            mock_typer.prompt.side_effect = ["e", "edited experience text"]

            await process_deltas_and_confirm(
                modified, original, "com.test.app", "test_plan", llm_callable, rag_pipeline
            )

            rag_pipeline.write_back.assert_called_once()
            call_kwargs = rag_pipeline.write_back.call_args
            assert "edited experience text" in call_kwargs.kwargs["content"]

    @pytest.mark.asyncio
    async def test_ignore_choice_skips_write(self) -> None:
        original = {"TC-001": [_make_step(1, "tap", "btn_ok")]}
        modified = [_make_test_case("TC-001", steps=[_make_step(1, "tap", "btn_cancel")])]
        llm_callable = AsyncMock(return_value="experience")
        rag_pipeline = MagicMock()
        rag_pipeline.query = AsyncMock(return_value=[])
        rag_pipeline.write_back = AsyncMock()

        with patch("testagent.plan.delta_extractor.typer") as mock_typer:
            mock_typer.prompt.return_value = "i"

            await process_deltas_and_confirm(
                modified, original, "com.test.app", "test_plan", llm_callable, rag_pipeline
            )

            rag_pipeline.write_back.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_increments_occurrence(self) -> None:
        original = {"TC-001": [_make_step(1, "tap", "btn_ok")]}
        modified = [_make_test_case("TC-001", steps=[_make_step(1, "tap", "btn_cancel")])]
        llm_callable = AsyncMock(return_value="User prefers cancel")
        rag_pipeline = MagicMock()
        rag_pipeline.write_back = AsyncMock()
        # Simulate existing pattern found with high score
        existing_result = MagicMock()
        existing_result.score = 0.95
        existing_result.metadata = {"pattern_id": "existing-pattern-id"}
        rag_pipeline.query = AsyncMock(return_value=[existing_result])

        pattern_repo = MagicMock()
        pattern_repo.get_by_app_id = AsyncMock(return_value=[])
        # Simulate the existing pattern
        existing_pattern = MagicMock()
        existing_pattern.id = "existing-pattern-id"
        existing_pattern.occurrence_count = 1
        existing_pattern.confidence = 0.80
        pattern_repo.get_by_id = AsyncMock(return_value=existing_pattern)
        pattern_repo.update = AsyncMock(return_value=existing_pattern)

        with patch("testagent.plan.delta_extractor.typer") as mock_typer:
            mock_typer.prompt.return_value = "s"

            await process_deltas_and_confirm(
                modified,
                original,
                "com.test.app",
                "test_plan",
                llm_callable,
                rag_pipeline,
                pattern_repo=pattern_repo,
            )

            # Should NOT write_back since we're just incrementing
            rag_pipeline.write_back.assert_not_called()
            update_call = pattern_repo.update.call_args
            assert update_call[0][0] == "existing-pattern-id"
            assert update_call[0][1]["occurrence_count"] == 2
            assert update_call[0][1]["confidence"] == pytest.approx(0.85)
