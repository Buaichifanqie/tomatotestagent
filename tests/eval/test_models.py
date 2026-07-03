from __future__ import annotations

import math

from testagent.eval.models import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderResult,
    MetricConfig,
    ReferenceSolution,
    ScoringConfig,
    SetupStep,
    SuiteResult,
    TaskResult,
    Transcript,
    TranscriptSummary,
    TrialResult,
)


class TestTaskResult:
    """Test multi-trial aggregation properties on TaskResult."""

    def test_pass_at_1_first_passed(self) -> None:
        trials = [
            TrialResult(trial_num=1, passed=True, score=0.9),
            TrialResult(trial_num=2, passed=False, score=0.4),
            TrialResult(trial_num=3, passed=True, score=0.8),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.pass_at_1 is True

    def test_pass_at_1_first_failed(self) -> None:
        trials = [
            TrialResult(trial_num=1, passed=False, score=0.4),
            TrialResult(trial_num=2, passed=True, score=0.9),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.pass_at_1 is False

    def test_pass_at_1_empty_trials(self) -> None:
        result = TaskResult(task_id="T1", trials=[])
        assert result.pass_at_1 is False

    def test_pass_at_k_any_passed(self) -> None:
        trials = [
            TrialResult(trial_num=1, passed=False, score=0.4),
            TrialResult(trial_num=2, passed=False, score=0.5),
            TrialResult(trial_num=3, passed=True, score=0.9),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.pass_at_k is True

    def test_pass_at_k_none_passed(self) -> None:
        trials = [
            TrialResult(trial_num=1, passed=False, score=0.4),
            TrialResult(trial_num=2, passed=False, score=0.5),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.pass_at_k is False

    def test_pass_at_k_empty_trials(self) -> None:
        result = TaskResult(task_id="T1", trials=[])
        assert result.pass_at_k is False

    def test_all_passed_all_passed(self) -> None:
        trials = [
            TrialResult(trial_num=1, passed=True, score=0.9),
            TrialResult(trial_num=2, passed=True, score=0.8),
            TrialResult(trial_num=3, passed=True, score=0.95),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.all_passed is True

    def test_all_passed_one_failed(self) -> None:
        trials = [
            TrialResult(trial_num=1, passed=True, score=0.9),
            TrialResult(trial_num=2, passed=False, score=0.4),
            TrialResult(trial_num=3, passed=True, score=0.8),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.all_passed is False

    def test_all_passed_empty_trials(self) -> None:
        result = TaskResult(task_id="T1", trials=[])
        assert result.all_passed is False

    def test_pass_rate_all_passed(self) -> None:
        trials = [
            TrialResult(trial_num=1, passed=True),
            TrialResult(trial_num=2, passed=True),
            TrialResult(trial_num=3, passed=True),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.pass_rate == 1.0

    def test_pass_rate_half_passed(self) -> None:
        trials = [
            TrialResult(trial_num=1, passed=True),
            TrialResult(trial_num=2, passed=False),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.pass_rate == 0.5

    def test_pass_rate_none_passed(self) -> None:
        trials = [
            TrialResult(trial_num=1, passed=False),
            TrialResult(trial_num=2, passed=False),
            TrialResult(trial_num=3, passed=False),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.pass_rate == 0.0

    def test_pass_rate_empty_trials(self) -> None:
        result = TaskResult(task_id="T1", trials=[])
        assert result.pass_rate == 0.0

    def test_mean_score_multiple(self) -> None:
        trials = [
            TrialResult(trial_num=1, score=0.8),
            TrialResult(trial_num=2, score=0.9),
            TrialResult(trial_num=3, score=0.7),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        assert abs(result.mean_score - 0.8) < 1e-10

    def test_mean_score_single(self) -> None:
        trials = [TrialResult(trial_num=1, score=0.95)]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.mean_score == 0.95

    def test_mean_score_empty_trials(self) -> None:
        result = TaskResult(task_id="T1", trials=[])
        assert result.mean_score == 0.0

    def test_score_std_multiple(self) -> None:
        trials = [
            TrialResult(trial_num=1, score=0.8),
            TrialResult(trial_num=2, score=0.9),
            TrialResult(trial_num=3, score=0.7),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        # population std: mean=0.8, squared_diffs=[0.0, 0.01, 0.01], variance=0.02/3≈0.00667
        expected = math.sqrt((0.0 + 0.01 + 0.01) / 3)
        assert abs(result.score_std - expected) < 1e-10

    def test_score_std_single(self) -> None:
        trials = [TrialResult(trial_num=1, score=0.9)]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.score_std == 0.0

    def test_score_std_empty_trials(self) -> None:
        result = TaskResult(task_id="T1", trials=[])
        assert result.score_std == 0.0

    def test_score_std_identical_scores(self) -> None:
        trials = [
            TrialResult(trial_num=1, score=0.85),
            TrialResult(trial_num=2, score=0.85),
            TrialResult(trial_num=3, score=0.85),
        ]
        result = TaskResult(task_id="T1", trials=trials)
        assert result.score_std == 0.0


class TestSuiteResult:
    """Test suite-level aggregation properties on SuiteResult."""

    def test_overall_pass_rate(self) -> None:
        tasks = [
            TaskResult(
                task_id="T1",
                trials=[TrialResult(trial_num=1, passed=True)],
            ),
            TaskResult(
                task_id="T2",
                trials=[TrialResult(trial_num=1, passed=False)],
            ),
            TaskResult(
                task_id="T3",
                trials=[TrialResult(trial_num=1, passed=True)],
            ),
        ]
        suite = SuiteResult(
            suite_name="test",
            run_id="run-001",
            timestamp="2024-01-01T00:00:00",
            task_results=tasks,
        )
        assert suite.overall_pass_rate == 2 / 3

    def test_overall_pass_rate_empty(self) -> None:
        suite = SuiteResult(
            suite_name="test",
            run_id="run-001",
            timestamp="2024-01-01T00:00:00",
            task_results=[],
        )
        assert suite.overall_pass_rate == 0.0

    def test_pass_at_1_rate(self) -> None:
        tasks = [
            TaskResult(
                task_id="T1",
                trials=[TrialResult(trial_num=1, passed=True), TrialResult(trial_num=2, passed=False)],
            ),
            TaskResult(
                task_id="T2",
                trials=[TrialResult(trial_num=1, passed=False), TrialResult(trial_num=2, passed=True)],
            ),
        ]
        suite = SuiteResult(
            suite_name="test",
            run_id="run-001",
            timestamp="2024-01-01T00:00:00",
            task_results=tasks,
        )
        assert suite.pass_at_1_rate == 0.5

    def test_pass_at_1_rate_empty(self) -> None:
        suite = SuiteResult(
            suite_name="test",
            run_id="run-001",
            timestamp="2024-01-01T00:00:00",
            task_results=[],
        )
        assert suite.pass_at_1_rate == 0.0

    def test_pass_k_rate(self) -> None:
        tasks = [
            TaskResult(
                task_id="T1",
                trials=[TrialResult(trial_num=1, passed=True), TrialResult(trial_num=2, passed=True)],
            ),
            TaskResult(
                task_id="T2",
                trials=[TrialResult(trial_num=1, passed=True), TrialResult(trial_num=2, passed=False)],
            ),
        ]
        suite = SuiteResult(
            suite_name="test",
            run_id="run-001",
            timestamp="2024-01-01T00:00:00",
            task_results=tasks,
        )
        assert suite.pass_k_rate == 0.5

    def test_pass_k_rate_empty(self) -> None:
        suite = SuiteResult(
            suite_name="test",
            run_id="run-001",
            timestamp="2024-01-01T00:00:00",
            task_results=[],
        )
        assert suite.pass_k_rate == 0.0


class TestSuiteResultToDict:
    """Test SuiteResult.to_dict() serialization."""

    def test_to_dict_basic(self) -> None:
        tasks = [
            TaskResult(
                task_id="T1",
                trials=[TrialResult(trial_num=1, passed=True, score=0.9)],
            ),
        ]
        suite = SuiteResult(
            suite_name="test-suite",
            run_id="run-001",
            timestamp="2024-06-01T12:00:00",
            task_results=tasks,
            duration=42.5,
            model_name="gpt-4",
        )
        d = suite.to_dict()
        assert d["suite_name"] == "test-suite"
        assert d["run_id"] == "run-001"
        assert d["timestamp"] == "2024-06-01T12:00:00"
        assert d["duration"] == 42.5
        assert d["model_name"] == "gpt-4"
        assert d["overall_pass_rate"] == 1.0
        assert d["pass_at_1_rate"] == 1.0
        assert d["pass_k_rate"] == 1.0
        assert d["num_tasks"] == 1
        assert len(d["task_results"]) == 1
        tr_dict = d["task_results"][0]
        assert tr_dict["task_id"] == "T1"
        assert tr_dict["pass_at_1"] is True
        assert tr_dict["pass_at_k"] is True
        assert tr_dict["all_passed"] is True
        assert tr_dict["mean_score"] == 0.9
        assert tr_dict["score_std"] == 0.0
        assert tr_dict["pass_rate"] == 1.0
        assert tr_dict["num_trials"] == 1

    def test_to_dict_empty(self) -> None:
        suite = SuiteResult(
            suite_name="empty",
            run_id="run-000",
            timestamp="2024-01-01T00:00:00",
        )
        d = suite.to_dict()
        assert d["suite_name"] == "empty"
        assert d["num_tasks"] == 0
        assert d["task_results"] == []
        assert d["overall_pass_rate"] == 0.0
        assert d["pass_at_1_rate"] == 0.0
        assert d["pass_k_rate"] == 0.0


class TestEvalTask:
    """Test EvalTask creation with various grader configurations."""

    def test_basic_task(self) -> None:
        task = EvalTask(
            id="eval-task-001",
            description="Test basic navigation",
            instruction="Navigate to the settings page",
            app="bilibili",
            tags=["smoke", "navigation"],
            trials=5,
        )
        assert task.id == "eval-task-001"
        assert task.app == "bilibili"
        assert task.trials == 5
        assert len(task.setup) == 0
        assert task.scoring is None
        assert task.reference is None

    def test_with_grader_configs(self) -> None:
        graders = [
            GraderConfig(grader_type="state_check", expect={"page": "settings"}, rubric="Settings page visible"),
            GraderConfig(
                grader_type="llm_rubric",
                rubric="Settings loaded successfully",
            ),
        ]
        task = EvalTask(
            id="eval-task-002",
            description="Settings page test",
            instruction="Open settings and verify",
            graders=graders,
            scoring=ScoringConfig(pass_threshold=0.8),
        )
        assert len(task.graders) == 2
        assert task.graders[0].grader_type == "state_check"
        assert task.graders[0].expect == {"page": "settings"}
        assert task.graders[1].grader_type == "llm_rubric"
        assert task.graders[1].rubric == "Settings loaded successfully"
        assert task.scoring is not None
        assert task.scoring.pass_threshold == 0.8

    def test_with_reference(self) -> None:
        task = EvalTask(
            id="eval-task-003",
            description="Task with reference",
            instruction="Do something",
            reference=ReferenceSolution(expected_outcome="Success", expected_duration=30.0),
        )
        assert task.reference is not None
        assert task.reference.expected_outcome == "Success"
        assert task.reference.expected_duration == 30.0

    def test_with_setup_steps(self) -> None:
        setup = [
            SetupStep(action="launch_app", params={"app": "bilibili"}),
            SetupStep(action="login", params={"username": "test"}),
        ]
        task = EvalTask(
            id="eval-task-004",
            description="Login test",
            instruction="Login and verify",
            setup=setup,
        )
        assert len(task.setup) == 2
        assert task.setup[0].action == "launch_app"
        assert task.setup[0].params["app"] == "bilibili"

    def test_with_tracked_metrics(self) -> None:
        task = EvalTask(
            id="eval-task-005",
            description="Performance test",
            instruction="Measure response time",
            tracked_metrics=[MetricConfig(type="latency", metrics=["p50", "p95", "p99"])],
        )
        assert task.tracked_metrics is not None
        assert len(task.tracked_metrics) == 1
        assert task.tracked_metrics[0].type == "latency"
        assert "p95" in task.tracked_metrics[0].metrics

    def test_task_default_trials(self) -> None:
        task = EvalTask(id="eval-task-006", description="Default trials", instruction="Just do it")
        assert task.trials == 3

    def test_task_with_timeout(self) -> None:
        task = EvalTask(
            id="eval-task-007",
            description="Timeout test",
            instruction="Run with timeout",
            timeout=60000,
        )
        assert task.timeout == 60000


class TestEvalSuite:
    """Test EvalSuite creation."""

    def test_basic_suite(self) -> None:
        suite = EvalSuite(
            name="bilibili-smoke",
            description="Bilibili smoke tests",
            app="bilibili",
            tags=["smoke"],
        )
        assert suite.name == "bilibili-smoke"
        assert suite.version == "1.0"
        assert suite.default_trials == 3
        assert suite.tasks == []

    def test_suite_with_tasks(self) -> None:
        tasks = [
            EvalTask(id="T1", description="Task 1", instruction="Do 1"),
            EvalTask(id="T2", description="Task 2", instruction="Do 2"),
        ]
        suite = EvalSuite(
            name="regression",
            description="Regression suite",
            version="2.0",
            default_trials=5,
            tasks=tasks,
        )
        assert len(suite.tasks) == 2
        assert suite.version == "2.0"
        assert suite.default_trials == 5


class TestTrialResult:
    """Test TrialResult creation."""

    def test_basic_trial(self) -> None:
        trial = TrialResult(trial_num=1, passed=True, score=0.95)
        assert trial.trial_num == 1
        assert trial.passed is True
        assert trial.score == 0.95
        assert trial.failure_reason == ""
        assert trial.duration == 0.0
        assert trial.transcript is None

    def test_trial_with_grader_results(self) -> None:
        graders = [
            GraderResult(grader_type="state_check", score=1.0, passed=True, details="OK"),
            GraderResult(grader_type="llm_rubric", score=0.8, passed=True, details="Good"),
        ]
        trial = TrialResult(trial_num=1, grader_results=graders)
        assert len(trial.grader_results) == 2
        assert trial.grader_results[0].grader_type == "state_check"
        assert trial.grader_results[1].score == 0.8

    def test_trial_with_transcript(self) -> None:
        summary = TranscriptSummary(n_turns=5, total_tokens=1000)
        transcript = Transcript(
            messages=[{"role": "user", "content": "hi"}],
            summary=summary,
        )
        trial = TrialResult(trial_num=1, transcript=transcript)
        assert trial.transcript is not None
        assert trial.transcript.summary is not None
        assert trial.transcript.summary.n_turns == 5
        assert len(trial.transcript.messages) == 1


class TestTranscriptSummary:
    """Test TranscriptSummary creation."""

    def test_basic_summary(self) -> None:
        summary = TranscriptSummary(
            n_turns=10,
            n_tool_calls=25,
            total_tokens=5000,
            total_duration=120.5,
            tool_call_sequence=["search", "click", "assert"],
            key_errors=["TimeoutError"],
            final_page="settings",
        )
        assert summary.n_turns == 10
        assert summary.n_tool_calls == 25
        assert summary.total_tokens == 5000
        assert summary.total_duration == 120.5
        assert len(summary.tool_call_sequence) == 3
        assert "TimeoutError" in summary.key_errors
        assert summary.final_page == "settings"

    def test_empty_summary(self) -> None:
        summary = TranscriptSummary()
        assert summary.n_turns == 0
        assert summary.n_tool_calls == 0
        assert summary.tool_call_sequence == []


class TestSetupStep:
    """Test SetupStep creation."""

    def test_basic_step(self) -> None:
        step = SetupStep(action="launch_app", params={"app": "bilibili"})
        assert step.action == "launch_app"
        assert step.params["app"] == "bilibili"

    def test_default_params(self) -> None:
        step = SetupStep(action="click")
        assert step.params == {}


class TestGraderConfig:
    """Test GraderConfig creation."""

    def test_defaults(self) -> None:
        cfg = GraderConfig(grader_type="state_check")
        assert cfg.grader_type == "state_check"
        assert cfg.expect is None
        assert cfg.rubric is None

    def test_with_expect_and_rubric(self) -> None:
        cfg = GraderConfig(
            grader_type="state_check",
            expect={"page": "home"},
            rubric="Home page visible",
        )
        assert cfg.expect == {"page": "home"}
        assert cfg.rubric == "Home page visible"


class TestScoringConfig:
    """Test ScoringConfig creation."""

    def test_defaults(self) -> None:
        cfg = ScoringConfig()
        assert cfg.mode == "hybrid"
        assert cfg.pass_threshold == 0.8
        assert cfg.mandatory == []
        assert cfg.weights == {}

    def test_custom(self) -> None:
        cfg = ScoringConfig(
            mode="weighted",
            pass_threshold=0.8,
            mandatory=["state_check"],
            weights={"state_check": 0.6, "llm_rubric": 0.4},
        )
        assert cfg.mode == "weighted"
        assert cfg.pass_threshold == 0.8
        assert "state_check" in cfg.mandatory
        assert cfg.weights["llm_rubric"] == 0.4


class TestMetricConfig:
    """Test MetricConfig creation."""

    def test_defaults(self) -> None:
        mc = MetricConfig(type="transcript")
        assert mc.type == "transcript"
        assert mc.metrics == []


class TestReferenceSolution:
    """Test ReferenceSolution creation."""

    def test_default_duration(self) -> None:
        ref = ReferenceSolution(expected_outcome="Success")
        assert ref.expected_duration is None

    def test_with_duration(self) -> None:
        ref = ReferenceSolution(expected_outcome="Success", expected_duration=30.0)
        assert ref.expected_duration == 30.0
