"""Tests for the YAML-based evaluation suite and task loader."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from testagent.eval.loader import discover_suites, load_suite
from testagent.eval.models import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    MetricConfig,
    ScoringConfig,
    SetupStep,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_yaml(path: Path, data: dict) -> None:
    """Write *data* as YAML to *path*, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


def _make_suite_dir(
    tmpdir: Path,
    suite_name: str = "test-suite",
    suite_data: dict | None = None,
    tasks: list[dict] | None = None,
) -> Path:
    """Create a temporary suite directory with *suite.yaml* and *tasks*."""
    suite_dir = tmpdir / suite_name
    suite_dir.mkdir(parents=True, exist_ok=True)

    default_suite = {
        "suite": {
            "name": suite_name,
            "description": "A test suite",
            "version": "1.0.0",
            "default_trials": 3,
            "app": "bilibili",
            "tags": ["test"],
        }
    }
    _write_yaml(suite_dir / "suite.yaml", suite_data or default_suite)

    if tasks:
        for i, task_data in enumerate(tasks):
            _write_yaml(suite_dir / f"task_{i}.yaml", {"task": task_data})

    return suite_dir


# ── Tests ────────────────────────────────────────────────────────────────────


class TestLoadSuite:
    """Tests for ``load_suite()``."""

    def test_load_basic_suite(self) -> None:
        """Load a suite with two task definitions and verify all metadata."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            task1 = {
                "id": "test_task_1",
                "description": "First test task",
                "instruction": "Navigate to settings page",
                "trials": 5,
                "timeout": 60,
                "graders": [
                    {"type": "state_check", "expect": {"page": "settings"}},
                ],
                "scoring": {
                    "mode": "hybrid",
                    "pass_threshold": 0.8,
                    "mandatory": ["state_check"],
                },
                "tracked_metrics": [
                    {"type": "transcript", "metrics": ["n_turns"]},
                ],
            }

            task2 = {
                "id": "test_task_2",
                "description": "Second test task",
                "instruction": "Search for a video",
                "timeout": 90,
                "graders": [
                    {"type": "llm_rubric", "rubric": "Search completed"},
                ],
            }

            suite_dir = _make_suite_dir(tmpdir, tasks=[task1, task2])
            suite = load_suite(str(suite_dir))

            # Suite metadata
            assert isinstance(suite, EvalSuite)
            assert suite.name == "test-suite"
            assert suite.description == "A test suite"
            assert suite.version == "1.0.0"
            assert suite.default_trials == 3
            assert suite.app == "bilibili"
            assert suite.tags == ["test"]

            # Exactly two tasks loaded
            assert len(suite.tasks) == 2

            # ── Task 1 ────────────────────────────────────────────────────────
            t1: EvalTask = suite.tasks[0]
            assert t1.id == "test_task_1"
            assert t1.description == "First test task"
            assert t1.instruction == "Navigate to settings page"
            assert t1.trials == 5  # overrides suite default
            assert t1.timeout == 60

            # GraderConfig
            assert len(t1.graders) == 1
            g1: GraderConfig = t1.graders[0]
            assert g1.grader_type == "state_check"
            assert g1.expect == {"page": "settings"}

            # ScoringConfig
            assert isinstance(t1.scoring, ScoringConfig)
            assert t1.scoring.mode == "hybrid"
            assert t1.scoring.pass_threshold == 0.8
            assert t1.scoring.mandatory == ["state_check"]

            # Tracked metrics (list)
            assert len(t1.tracked_metrics) == 1
            tm1: MetricConfig = t1.tracked_metrics[0]
            assert tm1.type == "transcript"
            assert tm1.metrics == ["n_turns"]

            # ── Task 2 ────────────────────────────────────────────────────────
            t2: EvalTask = suite.tasks[1]
            assert t2.id == "test_task_2"
            assert t2.instruction == "Search for a video"
            # Inherits suite default trials
            assert t2.trials == 3
            assert t2.timeout == 90

            assert len(t2.graders) == 1
            g2: GraderConfig = t2.graders[0]
            assert g2.grader_type == "llm_rubric"
            assert g2.rubric == "Search completed"

            # No scoring in YAML → None
            assert t2.scoring is None

            # No tracked_metrics in YAML → empty list
            assert t2.tracked_metrics == []

    def test_load_uses_default_trials(self) -> None:
        """Task without explicit trials inherits suite default_trials."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            suite_data = {
                "suite": {
                    "name": "default-trials-suite",
                    "description": "Suite with custom default trials",
                    "version": "1.0.0",
                    "default_trials": 5,
                }
            }

            task_data = {
                "id": "no_trials_task",
                "description": "Task without trials",
                "instruction": "Do something",
                "graders": [
                    {"type": "state_check", "expect": {"page": "home"}},
                ],
            }

            suite_dir = _make_suite_dir(tmpdir, suite_data=suite_data, tasks=[task_data])
            suite = load_suite(str(suite_dir))

            assert len(suite.tasks) == 1
            task = suite.tasks[0]
            assert task.trials == 5  # inherited from suite default_trials

    def test_task_with_optional_setup(self) -> None:
        """Task with setup steps is parsed correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            task_data = {
                "id": "setup_task",
                "description": "Task with setup",
                "instruction": "Login and navigate",
                "setup": [
                    {"action": "launch_app", "params": {"app": "bilibili"}},
                    {"action": "login", "params": {"username": "test_user"}},
                    {"action": "navigate", "params": {"page": "home"}},
                ],
                "graders": [
                    {"type": "state_check", "expect": {"page": "home"}},
                ],
            }

            suite_dir = _make_suite_dir(tmpdir, tasks=[task_data])
            suite = load_suite(str(suite_dir))

            assert len(suite.tasks) == 1
            task = suite.tasks[0]
            assert len(task.setup) == 3

            s1: SetupStep = task.setup[0]
            assert s1.action == "launch_app"
            assert s1.params == {"app": "bilibili"}

            s2: SetupStep = task.setup[1]
            assert s2.action == "login"
            assert s2.params == {"username": "test_user"}

            s3: SetupStep = task.setup[2]
            assert s3.action == "navigate"
            assert s3.params == {"page": "home"}

    def test_load_error_missing_dir(self) -> None:
        """load_suite raises FileNotFoundError for a nonexistent path."""
        with tempfile.TemporaryDirectory() as tmp:
            nonexistent = Path(tmp) / "does_not_exist"
            import pytest

            with pytest.raises(FileNotFoundError):
                load_suite(str(nonexistent))

    def test_load_error_missing_suite_yaml(self) -> None:
        """load_suite raises ValueError when suite.yaml is missing."""
        with tempfile.TemporaryDirectory() as tmp:
            empty_dir = Path(tmp) / "empty_suite"
            empty_dir.mkdir()
            import pytest

            with pytest.raises(ValueError, match="Missing suite.yaml"):
                load_suite(str(empty_dir))


class TestDiscoverSuites:
    """Tests for ``discover_suites()``."""

    def test_discover_suites(self) -> None:
        """Discover returns all suites in a directory."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            # Suite A
            _make_suite_dir(
                tmpdir,
                suite_name="suite-a",
                suite_data={
                    "suite": {
                        "name": "suite-a",
                        "description": "Suite A",
                        "default_trials": 3,
                    }
                },
                tasks=[
                    {"id": "a1", "description": "A1", "instruction": "Do A1"},
                ],
            )

            # Suite B
            _make_suite_dir(
                tmpdir,
                suite_name="suite-b",
                suite_data={
                    "suite": {
                        "name": "suite-b",
                        "description": "Suite B",
                        "default_trials": 5,
                    }
                },
                tasks=[
                    {"id": "b1", "description": "B1", "instruction": "Do B1"},
                    {"id": "b2", "description": "B2", "instruction": "Do B2"},
                ],
            )

            suites = discover_suites(str(tmpdir))
            assert len(suites) == 2

            names = {s.name for s in suites}
            assert names == {"suite-a", "suite-b"}

            suite_b = next(s for s in suites if s.name == "suite-b")
            assert len(suite_b.tasks) == 2
            assert suite_b.tasks[0].id == "b1"
            assert suite_b.default_trials == 5

    def test_discover_empty_dir(self) -> None:
        """Discover returns empty list for a directory with no suites."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            suites = discover_suites(str(tmpdir))
            assert suites == []

    def test_discover_nonexistent_dir(self) -> None:
        """Discover returns empty list for a nonexistent directory."""
        suites = discover_suites("/nonexistent/path/for/sure")
        assert suites == []
