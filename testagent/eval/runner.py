"""EvalRunner — core orchestrator for the eval subsystem.

Coordinates task execution, grading, and score aggregation across
multi-trial evaluation suites.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable

from testagent.eval.models import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderResult,
    ScoringConfig,
    SuiteResult,
    TaskResult,
    TrialResult,
)
from testagent.eval.transcript import TranscriptRecorder

logger = logging.getLogger(__name__)


async def _default_agent_loop(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str,
    llm_provider: Any,
    dispatch_fn: Callable | None = None,
    max_rounds: int = 50,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Default agent loop implementation (delegates to testagent.agent.loop)."""
    from testagent.agent.loop import agent_loop

    return await agent_loop(
        messages=messages,
        tools=tools,
        system=system,
        llm_provider=llm_provider,
        dispatch_fn=dispatch_fn,
        max_rounds=max_rounds,
        **kwargs,
    )


class EvalRunner:
    """Core orchestrator for evaluation suites.

    Executes tasks (with multi-trial support), grades transcripts, and
    aggregates scores into ``TrialResult`` / ``TaskResult`` / ``SuiteResult``.
    """

    def __init__(
        self,
        llm_provider: Any,
        mcp_tools: list[dict[str, Any]],
        dispatch_fn: Callable | None = None,
        agent_loop_fn: Callable | None = None,
        system_prompt: str = "",
        model_name: str = "",
    ) -> None:
        self._llm = llm_provider
        self._mcp_tools = mcp_tools
        self._dispatch = dispatch_fn or self._default_dispatch
        self._agent_loop_fn = agent_loop_fn or _default_agent_loop
        self._system_prompt = system_prompt
        self._model_name = model_name

    async def _default_dispatch(self, tool_name: str, args: dict) -> dict:
        """Default dispatch — logs calls when no dispatch_fn is configured."""
        return {"result": f"Called {tool_name} with {args}", "note": "No dispatch configured"}

    # ── Public API ──────────────────────────────────────────────────────────

    async def run_suite(self, suite: EvalSuite) -> SuiteResult:
        """Execute all tasks in a suite sequentially.

        Parameters
        ----------
        suite:
            The evaluation suite whose tasks will be run.

        Returns
        -------
        SuiteResult
            Aggregated results for the entire suite.
        """
        run_id = (
            f"eval_{suite.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        timestamp = datetime.now().isoformat()
        start = datetime.now()

        task_results: list[TaskResult] = []
        for task in suite.tasks:
            result = await self.run_task(task)
            task_results.append(result)

        duration = (datetime.now() - start).total_seconds()
        return SuiteResult(
            suite_name=suite.name,
            run_id=run_id,
            timestamp=timestamp,
            task_results=task_results,
            duration=duration,
            model_name=self._model_name,
        )

    async def run_task(self, task: EvalTask) -> TaskResult:
        """Run N trials for a task and aggregate results.

        Parameters
        ----------
        task:
            The evaluation task to execute.

        Returns
        -------
        TaskResult
            Aggregated result across all trials.
        """
        trials: list[TrialResult] = []
        for trial_num in range(1, task.trials + 1):
            result = await self._run_trial(task, trial_num)
            trials.append(result)
        return TaskResult(task_id=task.id, trials=trials)

    # ── Internal: Trial Execution ──────────────────────────────────────────

    async def _run_trial(
        self, task: EvalTask, trial_num: int
    ) -> TrialResult:
        """Execute a single trial of a task."""
        recorder = TranscriptRecorder()
        recorder.start()

        try:
            # Phase 1: Setup
            await self._execute_setup(task.setup)

            # Phase 2: Agent execution with hard timeout
            system = self._build_system_prompt(task)
            messages = await asyncio.wait_for(
                self._agent_loop_fn(
                    messages=[
                        {"role": "user", "content": task.instruction}
                    ],
                    tools=self._mcp_tools,
                    system=system,
                    llm_provider=self._llm,
                    dispatch_fn=self._dispatch,
                    progress_callback=recorder.on_round,
                ),
                timeout=task.timeout,
            )
            for msg in messages:
                recorder.record_message(msg)

        except asyncio.TimeoutError:
            recorder.stop()
            return TrialResult(
                trial_num=trial_num,
                passed=False,
                score=0.0,
                failure_reason=f"Timeout after {task.timeout}s",
                duration=recorder.duration,
                transcript=recorder.transcript,
            )
        except Exception as exc:
            recorder.stop()
            return TrialResult(
                trial_num=trial_num,
                passed=False,
                score=0.0,
                failure_reason=f"Execution error: {exc}",
                duration=recorder.duration,
                transcript=recorder.transcript,
            )

        recorder.stop()

        # Phase 3: Grade
        try:
            grader_results = await self._grade(recorder.transcript, task)
        except Exception as exc:
            return TrialResult(
                trial_num=trial_num,
                passed=False,
                score=0.0,
                failure_reason=f"Grading error: {exc}",
                duration=recorder.duration,
                transcript=recorder.transcript,
            )

        # Phase 4: Aggregate score
        score, passed = self._compute_score(grader_results, task)

        failure_reason: str | None = None
        if not passed:
            failing = [r for r in grader_results if not r.passed]
            if failing:
                failure_reason = (
                    f"{failing[0].grader_type}: {failing[0].details[:120]}"
                )

        return TrialResult(
            trial_num=trial_num,
            passed=passed,
            score=score,
            grader_results=grader_results,
            transcript=recorder.transcript,
            failure_reason=failure_reason,
            duration=recorder.duration,
        )

    # ── Internal: Setup ────────────────────────────────────────────────────

    async def _execute_setup(self, setup_steps: list[Any]) -> None:
        """Execute setup steps (stub for MVP — logs only)."""
        for step in setup_steps:
            logger.info("Setup step: %s %s", step.action, step.params)

    # ── Internal: System Prompt ────────────────────────────────────────────

    def _build_system_prompt(self, task: EvalTask) -> str:
        """Build system prompt with model name and app info.

        Combines the base system prompt (if provided) with the model
        name and the target app for the task.
        """
        parts: list[str] = []
        if self._system_prompt:
            parts.append(self._system_prompt)
        if self._model_name:
            parts.append(f"model: {self._model_name}")
        if task.app:
            parts.append(f"app: {task.app}")
        return "\n".join(parts)

    # ── Internal: Grading ──────────────────────────────────────────────────

    async def _grade(
        self, transcript: Any, task: EvalTask
    ) -> list[GraderResult]:
        """Run all graders for a task against its transcript."""
        results: list[GraderResult] = []
        for config in task.graders:
            grader = self._instantiate_grader(config)
            if grader is None:
                continue
            try:
                result = await grader.grade(transcript, task)
                results.append(result)
            except Exception as exc:
                results.append(
                    GraderResult(
                        grader_type=config.grader_type,
                        score=0.0,
                        passed=False,
                        details=f"Grader error: {exc}",
                    )
                )
        return results

    def _instantiate_grader(
        self, config: GraderConfig
    ) -> Any | None:
        """Instantiate a grader based on its config type."""
        if config.grader_type == "state_check":
            from testagent.eval.graders.state_check import StateCheckGrader

            return StateCheckGrader(config)
        elif config.grader_type == "llm_rubric":
            from testagent.eval.graders.llm_rubric import LlmRubricGrader

            return LlmRubricGrader(config, self._llm)
        else:
            logger.warning("Unknown grader type: %s", config.grader_type)
            return None

    # ── Internal: Scoring ──────────────────────────────────────────────────

    def _compute_score(
        self,
        grader_results: list[GraderResult],
        task: EvalTask,
    ) -> tuple[float, bool]:
        """Compute weighted score and determine pass/fail.

        Returns
        -------
        tuple[float, bool]
            (normalized score 0-1, whether the task passed).
        """
        scoring = task.scoring or ScoringConfig()
        weights = scoring.weights or {}

        total_weight = sum(
            weights.get(r.grader_type, 0.5) for r in grader_results
        )
        weighted_sum = sum(
            r.score * weights.get(r.grader_type, 0.5)
            for r in grader_results
        )
        score = weighted_sum / total_weight if total_weight > 0 else 0.0

        if scoring.mode == "binary":
            passed = all(r.passed for r in grader_results)
        elif scoring.mode == "hybrid":
            mandatory_passed = all(
                r.passed
                for r in grader_results
                if r.grader_type in scoring.mandatory
            )
            passed = mandatory_passed and score >= scoring.pass_threshold
        else:
            passed = score >= scoring.pass_threshold

        return score, passed
