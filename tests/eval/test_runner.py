"""Tests for EvalRunner — core orchestrator."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from testagent.eval.models import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderResult,
    ScoringConfig,
    TrialResult,
)
from testagent.eval.runner import EvalRunner


# ── Fake agent loops ──────────────────────────────────────────────────────────


async def _fake_agent_loop(**kwargs: Any) -> list[dict[str, Any]]:
    """Fake agent loop that returns immediately with a canned message."""
    return [{"role": "assistant", "content": "Done"}]


async def _fake_slow_agent_loop(**kwargs: Any) -> list[dict[str, Any]]:
    """Fake agent loop that sleeps forever (triggers timeout)."""
    await asyncio.sleep(100)
    return []


# ── Controlled runner (for aggregate tests) ───────────────────────────────────


class _ControlledRunner(EvalRunner):
    """EvalRunner that returns pre-defined TrialResults from _run_trial."""

    def __init__(self, trial_results: list[TrialResult]) -> None:
        super().__init__(llm_provider=None, mcp_tools=[])
        self._trial_results = trial_results
        self._call_idx = 0

    async def _run_trial(
        self, task: EvalTask, trial_num: int
    ) -> TrialResult:
        result = self._trial_results[self._call_idx]
        self._call_idx += 1
        return result


class _AlwaysPassRunner(EvalRunner):
    """EvalRunner whose _run_trial always returns a passing result."""

    async def _run_trial(
        self, task: EvalTask, trial_num: int
    ) -> TrialResult:
        return TrialResult(
            trial_num=trial_num,
            passed=True,
            score=1.0,
        )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestEvalRunner:
    """Tests for EvalRunner."""

    @pytest.mark.asyncio
    async def test_run_trial_timeout(self) -> None:
        """task.timeout=0.01 → catches TimeoutError, returns failed TrialResult."""
        runner = EvalRunner(
            llm_provider=None,
            mcp_tools=[],
            agent_loop_fn=_fake_slow_agent_loop,
        )
        task = EvalTask(
            id="test-timeout",
            description="Timeout test",
            instruction="Do something",
            timeout=1,  # 1 second timeout
            trials=1,
        )

        result = await runner._run_trial(task, trial_num=1)

        assert result.passed is False
        assert result.score == 0.0
        assert result.failure_reason is not None
        assert "Timeout" in result.failure_reason
        assert result.trial_num == 1

    @pytest.mark.asyncio
    async def test_run_trial_timeout_short(self) -> None:
        """Very short timeout (0.01s) triggers TimeoutError correctly."""
        runner = EvalRunner(
            llm_provider=None,
            mcp_tools=[],
            agent_loop_fn=_fake_slow_agent_loop,
        )
        task = EvalTask(
            id="test-timeout-short",
            description="Quick timeout test",
            instruction="Do something",
            timeout=1,
            trials=1,
        )

        result = await runner._run_trial(task, trial_num=1)

        assert result.passed is False
        assert result.score == 0.0
        assert result.failure_reason is not None
        assert "Timeout" in result.failure_reason

    @pytest.mark.asyncio
    async def test_run_trial_success(self) -> None:
        """Valid task with passing grader → passed=True, score >= 0."""
        runner = EvalRunner(
            llm_provider=None,
            mcp_tools=[],
            agent_loop_fn=_fake_agent_loop,
        )
        # Use a state_check grader with no expectations — it auto-passes
        task = EvalTask(
            id="test-success",
            description="Success test",
            instruction="Do something",
            graders=[GraderConfig(grader_type="state_check")],
            trials=1,
            timeout=5,
        )

        result = await runner._run_trial(task, trial_num=1)

        assert result.passed is True
        assert result.score >= 0
        assert result.trial_num == 1
        assert result.grader_results is not None
        assert len(result.grader_results) == 1
        assert result.grader_results[0].passed is True

    @pytest.mark.asyncio
    async def test_run_trial_success_with_scoring(self) -> None:
        """Valid task with custom scoring config → correct score & passed."""
        runner = EvalRunner(
            llm_provider=None,
            mcp_tools=[],
            agent_loop_fn=_fake_agent_loop,
        )
        task = EvalTask(
            id="test-scoring",
            description="Scoring test",
            instruction="Do something",
            graders=[GraderConfig(grader_type="state_check")],
            scoring=ScoringConfig(
                mode="hybrid",
                pass_threshold=0.5,
                mandatory=[],
                weights={"state_check": 1.0},
            ),
            trials=1,
            timeout=5,
        )

        result = await runner._run_trial(task, trial_num=1)

        assert result.passed is True
        assert result.score == 1.0

    @pytest.mark.asyncio
    async def test_run_trial_execution_error(self) -> None:
        """Agent loop raises exception → returns failed TrialResult."""

        async def _broken_agent_loop(**kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("Something went wrong")

        runner = EvalRunner(
            llm_provider=None,
            mcp_tools=[],
            agent_loop_fn=_broken_agent_loop,
        )
        task = EvalTask(
            id="test-error",
            description="Error test",
            instruction="Do something",
            trials=1,
            timeout=5,
        )

        result = await runner._run_trial(task, trial_num=1)

        assert result.passed is False
        assert result.score == 0.0
        assert result.failure_reason is not None
        assert "Execution error" in result.failure_reason

    @pytest.mark.asyncio
    async def test_run_trial_grader_error(self) -> None:
        """Grader raises exception → caught, GraderResult with error details."""

        class _FakeGraderRunner(EvalRunner):
            async def _grade(
                self, transcript: Any, task: EvalTask
            ) -> list[GraderResult]:
                raise ValueError("Grader failure")

        runner = _FakeGraderRunner(
            llm_provider=None,
            mcp_tools=[],
            agent_loop_fn=_fake_agent_loop,
        )
        task = EvalTask(
            id="test-grader-error",
            description="Grader error test",
            instruction="Do something",
            graders=[GraderConfig(grader_type="state_check")],
            trials=1,
            timeout=5,
        )

        result = await runner._run_trial(task, trial_num=1)

        # Grader error should cause a failure
        assert result.passed is False
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_run_task_aggregates_trials(self) -> None:
        """Trials with mixed pass/fail → correct pass_at_k, all_passed, mean_score."""
        # 3 trials: pass, pass, fail
        trial_results = [
            TrialResult(
                trial_num=1, passed=True, score=0.9, duration=1.0
            ),
            TrialResult(
                trial_num=2, passed=True, score=0.8, duration=1.0
            ),
            TrialResult(
                trial_num=3, passed=False, score=0.3, duration=1.0
            ),
        ]
        runner = _ControlledRunner(trial_results)

        task = EvalTask(
            id="test-aggregate",
            description="Aggregate test",
            instruction="Do something",
            trials=3,
            timeout=5,
        )

        result = await runner.run_task(task)

        assert result.task_id == "test-aggregate"
        assert result.pass_at_1 is True  # first trial passed
        assert result.pass_at_k is True  # at least one passed
        assert result.all_passed is False  # not all passed
        assert result.mean_score == pytest.approx(
            (0.9 + 0.8 + 0.3) / 3
        )
        assert len(result.trials) == 3

    @pytest.mark.asyncio
    async def test_run_task_all_fail(self) -> None:
        """All trials fail → pass_at_k=False, all_passed=False."""
        trial_results = [
            TrialResult(trial_num=1, passed=False, score=0.2),
            TrialResult(trial_num=2, passed=False, score=0.1),
        ]
        runner = _ControlledRunner(trial_results)

        task = EvalTask(
            id="test-all-fail",
            description="All fail test",
            instruction="Do something",
            trials=2,
            timeout=5,
        )

        result = await runner.run_task(task)

        assert result.pass_at_k is False
        assert result.all_passed is False

    @pytest.mark.asyncio
    async def test_run_task_all_pass(self) -> None:
        """All trials pass → pass_at_k=True, all_passed=True, mean_score=1.0."""
        trial_results = [
            TrialResult(trial_num=1, passed=True, score=1.0),
            TrialResult(trial_num=2, passed=True, score=1.0),
            TrialResult(trial_num=3, passed=True, score=1.0),
        ]
        runner = _ControlledRunner(trial_results)

        task = EvalTask(
            id="test-all-pass",
            description="All pass test",
            instruction="Do something",
            trials=3,
            timeout=5,
        )

        result = await runner.run_task(task)

        assert result.pass_at_k is True
        assert result.all_passed is True
        assert result.mean_score == 1.0

    @pytest.mark.asyncio
    async def test_run_suite(self) -> None:
        """Suite with 2 passing tasks → overall_pass_rate=1.0, 2 task results."""
        runner = _AlwaysPassRunner(
            llm_provider=None,
            mcp_tools=[],
            system_prompt="Test system prompt",
            model_name="test-model",
        )

        suite = EvalSuite(
            name="test-suite",
            description="A test suite",
            tasks=[
                EvalTask(
                    id="task-1",
                    description="First task",
                    instruction="Do task 1",
                    trials=3,
                    timeout=5,
                ),
                EvalTask(
                    id="task-2",
                    description="Second task",
                    instruction="Do task 2",
                    trials=3,
                    timeout=5,
                ),
            ],
        )

        result = await runner.run_suite(suite)

        assert result.suite_name == "test-suite"
        assert result.run_id.startswith("eval_test-suite_")
        assert result.model_name == "test-model"
        assert result.overall_pass_rate == 1.0
        assert result.pass_at_1_rate == 1.0
        assert result.pass_k_rate == 1.0
        assert len(result.task_results) == 2
        assert result.task_results[0].task_id == "task-1"
        assert result.task_results[1].task_id == "task-2"
        assert result.duration >= 0

    @pytest.mark.asyncio
    async def test_run_suite_mixed_results(self) -> None:
        """Suite with mixed pass/fail → partial pass rates."""
        # Two tasks: one passes (single trial), one fails (single trial)
        suite = EvalSuite(
            name="mixed-suite",
            description="Mixed results suite",
            tasks=[
                EvalTask(
                    id="task-pass",
                    description="Passing task",
                    instruction="Do it",
                    trials=1,
                    timeout=5,
                ),
                EvalTask(
                    id="task-fail",
                    description="Failing task",
                    instruction="Do it",
                    trials=1,
                    timeout=5,
                ),
            ],
        )

        class _MixedRunner(EvalRunner):
            def __init__(self) -> None:
                super().__init__(llm_provider=None, mcp_tools=[])
                self._call_count = 0

            async def _run_trial(
                self, task: EvalTask, trial_num: int
            ) -> TrialResult:
                self._call_count += 1
                if self._call_count == 1:
                    return TrialResult(
                        trial_num=trial_num, passed=True, score=1.0
                    )
                return TrialResult(
                    trial_num=trial_num, passed=False, score=0.0
                )

        runner = _MixedRunner()
        result = await runner.run_suite(suite)

        assert result.overall_pass_rate == 0.5
        assert result.pass_at_1_rate == 0.5
        assert result.pass_k_rate == 0.5  # task-pass has all 1 trial passing, task-fail has 0
        assert len(result.task_results) == 2
        assert result.task_results[0].task_id == "task-pass"
        assert result.task_results[1].task_id == "task-fail"

    @pytest.mark.asyncio
    async def test_run_suite_empty_tasks(self) -> None:
        """Suite with no tasks → all rates are 0.0, no task results."""
        runner = _AlwaysPassRunner(llm_provider=None, mcp_tools=[])
        suite = EvalSuite(
            name="empty-suite",
            description="An empty suite",
            tasks=[],
        )

        result = await runner.run_suite(suite)

        assert result.suite_name == "empty-suite"
        assert result.overall_pass_rate == 0.0
        assert result.pass_at_1_rate == 0.0
        assert result.pass_k_rate == 0.0
        assert len(result.task_results) == 0

    @pytest.mark.asyncio
    async def test_compute_score_binary(self) -> None:
        """binary mode: all grader_results must pass for task to pass."""
        runner = EvalRunner(llm_provider=None, mcp_tools=[])
        task = EvalTask(
            id="binary-test",
            description="Binary scoring test",
            instruction="",
            scoring=ScoringConfig(mode="binary"),
        )

        # All pass → pass
        results_all_pass = [
            GraderResult(grader_type="a", score=1.0, passed=True),
            GraderResult(grader_type="b", score=1.0, passed=True),
        ]
        score, passed = runner._compute_score(results_all_pass, task)
        assert passed is True
        assert score == 1.0

        # One fails → fail
        results_one_fail = [
            GraderResult(grader_type="a", score=1.0, passed=True),
            GraderResult(grader_type="b", score=0.0, passed=False),
        ]
        score, passed = runner._compute_score(results_one_fail, task)
        assert passed is False
        assert score == 0.5

    @pytest.mark.asyncio
    async def test_compute_score_hybrid(self) -> None:
        """hybrid mode: mandatory graders must pass AND score >= threshold."""
        runner = EvalRunner(llm_provider=None, mcp_tools=[])
        task = EvalTask(
            id="hybrid-test",
            description="Hybrid scoring test",
            instruction="",
            scoring=ScoringConfig(
                mode="hybrid",
                pass_threshold=0.6,
                mandatory=["state_check"],
                weights={"state_check": 1.0, "llm_rubric": 0.5},
            ),
        )

        # Mandatory passes, score >= threshold → pass
        results = [
            GraderResult(grader_type="state_check", score=1.0, passed=True),
            GraderResult(grader_type="llm_rubric", score=0.5, passed=False),
        ]
        score, passed = runner._compute_score(results, task)
        # total_weight = 1.0 + 0.5 = 1.5
        # weighted_sum = 1.0*1.0 + 0.5*0.5 = 1.25
        # score = 1.25 / 1.5 ≈ 0.833
        assert score == pytest.approx(1.25 / 1.5)
        assert passed is True

        # Mandatory fails → fail even with high score
        results_mandatory_fail = [
            GraderResult(
                grader_type="state_check", score=0.0, passed=False
            ),
            GraderResult(grader_type="llm_rubric", score=1.0, passed=True),
        ]
        score, passed = runner._compute_score(
            results_mandatory_fail, task
        )
        assert passed is False

        # Score below threshold → fail even with all mandatory passing
        results_low_score = [
            GraderResult(grader_type="state_check", score=1.0, passed=True),
            GraderResult(
                grader_type="llm_rubric", score=0.0, passed=False
            ),
        ]
        scorer2 = ScoringConfig(
            mode="hybrid",
            pass_threshold=0.9,
            mandatory=["state_check"],
            weights={"state_check": 1.0, "llm_rubric": 0.5},
        )
        task2 = EvalTask(
            id="hybrid-low",
            description="",
            instruction="",
            scoring=scorer2,
        )
        score, passed = runner._compute_score(results_low_score, task2)
        # total_weight = 1.0 + 0.5 = 1.5
        # weighted_sum = 1.0*1.0 + 0.0*0.5 = 1.0
        # score = 1.0 / 1.5 ≈ 0.667
        # 0.667 < 0.9 → fail
        assert score == pytest.approx(1.0 / 1.5)
        assert passed is False

    @pytest.mark.asyncio
    async def test_compute_score_continuous(self) -> None:
        """continuous (non-binary, non-hybrid) mode: score >= threshold."""
        runner = EvalRunner(llm_provider=None, mcp_tools=[])
        task = EvalTask(
            id="continuous-test",
            description="Continuous scoring test",
            instruction="",
            scoring=ScoringConfig(
                mode="continuous",
                pass_threshold=0.5,
            ),
        )

        results = [
            GraderResult(grader_type="a", score=0.8, passed=True),
            GraderResult(grader_type="b", score=0.4, passed=False),
        ]
        score, passed = runner._compute_score(results, task)
        # total_weight = 0.5 + 0.5 = 1.0
        # weighted_sum = 0.8*0.5 + 0.4*0.5 = 0.6
        # score = 0.6 / 1.0 = 0.6
        assert score == pytest.approx(0.6)
        assert passed is True  # 0.6 >= 0.5

    @pytest.mark.asyncio
    async def test_compute_score_empty_results(self) -> None:
        """No grader results → score=0.0, passed depends on mode."""
        runner = EvalRunner(llm_provider=None, mcp_tools=[])

        # Binary mode with no results → vacuous True
        task_binary = EvalTask(
            id="empty-binary",
            description="",
            instruction="",
            scoring=ScoringConfig(mode="binary"),
        )
        score, passed = runner._compute_score([], task_binary)
        assert score == 0.0
        assert passed is True  # all([]) is True

        # Hybrid mode with no results → mandatory_passed=True, but score < threshold
        task_hybrid = EvalTask(
            id="empty-hybrid",
            description="",
            instruction="",
            scoring=ScoringConfig(mode="hybrid", pass_threshold=0.8),
        )
        score, passed = runner._compute_score([], task_hybrid)
        assert score == 0.0
        assert passed is False  # 0.0 < 0.8

    @pytest.mark.asyncio
    async def test_default_scoring_when_none(self) -> None:
        """task.scoring=None → defaults to hybrid mode with 0.8 threshold."""
        runner = EvalRunner(llm_provider=None, mcp_tools=[])
        task = EvalTask(
            id="default-scoring",
            description="",
            instruction="",
            scoring=None,
        )

        # Single grader with score=0.5 → below default threshold of 0.8
        results = [
            GraderResult(grader_type="a", score=0.5, passed=False),
        ]
        score, passed = runner._compute_score(results, task)
        # weight = 0.5, weighted_sum = 0.5*0.25, score = 0.5
        # mode="hybrid", mandatory=[], pass_threshold=0.8
        # mandatory_passed = True, passed = 0.5 >= 0.8 = False
        assert score == pytest.approx(0.5)
        assert passed is False

    @pytest.mark.asyncio
    async def test_build_system_prompt(self) -> None:
        """_build_system_prompt includes system prompt, model, and app."""
        runner = EvalRunner(
            llm_provider=None,
            mcp_tools=[],
            system_prompt="You are TestAgent.",
            model_name="gpt-4",
        )
        task = EvalTask(
            id="prompt-test",
            description="",
            instruction="",
            app="bilibili",
        )

        prompt = runner._build_system_prompt(task)

        assert "You are TestAgent." in prompt
        assert "model: gpt-4" in prompt
        assert "app: bilibili" in prompt

    @pytest.mark.asyncio
    async def test_build_system_prompt_empty(self) -> None:
        """_build_system_prompt with no extras returns just the app line."""
        runner = EvalRunner(
            llm_provider=None,
            mcp_tools=[],
            system_prompt="",
            model_name="",
        )
        task = EvalTask(
            id="prompt-empty",
            description="",
            instruction="",
            app="bilibili",
        )

        prompt = runner._build_system_prompt(task)

        assert prompt == "app: bilibili"

    @pytest.mark.asyncio
    async def test_run_trial_records_transcript(self) -> None:
        """Transcript is populated after a successful _run_trial."""
        runner = EvalRunner(
            llm_provider=None,
            mcp_tools=[],
            agent_loop_fn=_fake_agent_loop,
        )
        task = EvalTask(
            id="test-transcript",
            description="Transcript test",
            instruction="Do something",
            graders=[GraderConfig(grader_type="state_check")],
            trials=1,
            timeout=5,
        )

        result = await runner._run_trial(task, trial_num=1)

        assert result.transcript is not None
        assert len(result.transcript.messages) > 0
        assert result.transcript.summary is not None
        assert result.duration > 0
