"""YAML loader for evaluation suites and tasks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from testagent.eval.models import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    MetricConfig,
    ReferenceSolution,
    ScoringConfig,
    SetupStep,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_EVALS_DIR = _PROJECT_ROOT / "evals" / "tasks"


def load_suite(suite_path: str) -> EvalSuite:
    """Load an evaluation suite from a directory path.

    If *suite_path* is not absolute, it is treated as a suite name and resolved
    relative to ``<project_root>/evals/tasks/<name>``.
    """
    path = Path(suite_path)
    if not path.is_absolute():
        path = _DEFAULT_EVALS_DIR / suite_path

    if not path.exists():
        raise FileNotFoundError(f"Suite directory not found: {path}")

    suite_yaml = path / "suite.yaml"
    if not suite_yaml.exists():
        raise ValueError(f"Missing suite.yaml in: {path}")

    with suite_yaml.open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    suite_data = raw.get("suite", {})
    suite = EvalSuite(
        name=suite_data.get("name", path.name),
        description=suite_data.get("description", ""),
        version=suite_data.get("version", "1.0.0"),
        default_trials=suite_data.get("default_trials", 3),
        app=suite_data.get("app"),
        tags=suite_data.get("tags", []),
    )

    # Walk directory for all .yaml task files (excluding suite.yaml).
    task_files = sorted(
        p for p in path.rglob("*.yaml") if p.name != "suite.yaml"
    )
    for task_file in task_files:
        task = _load_task_file(task_file, suite.default_trials)
        suite.tasks.append(task)

    return suite


def _load_task_file(path: Path, default_trials: int) -> EvalTask:
    """Parse a single YAML task definition file."""
    with path.open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    data = raw.get("task", {})

    # ── setup steps ──────────────────────────────────────────────────────────
    setup_raw: list[dict[str, Any]] = data.get("setup", [])
    setup = [
        SetupStep(action=item["action"], params=item.get("params", {}))
        for item in setup_raw
    ]

    # ── grader configs ───────────────────────────────────────────────────────
    graders_raw: list[dict[str, Any]] = data.get("graders", [])
    graders = [
        GraderConfig(
            grader_type=g["type"],
            expect=g.get("expect"),
            rubric=g.get("rubric"),
        )
        for g in graders_raw
    ]

    # ── scoring ──────────────────────────────────────────────────────────────
    scoring_raw: dict[str, Any] | None = data.get("scoring")
    scoring: ScoringConfig | None = None
    if scoring_raw is not None:
        scoring = ScoringConfig(
            mode=scoring_raw.get("mode", "hybrid"),
            pass_threshold=scoring_raw.get("pass_threshold", 0.8),
            mandatory=scoring_raw.get("mandatory", []),
            weights=scoring_raw.get("weights", {}),
        )

    # ── tracked metrics ──────────────────────────────────────────────────────
    tm_list = data.get("tracked_metrics", [])
    tracked_metrics_list = [
        MetricConfig(type=m["type"], metrics=m.get("metrics", []))
        for m in tm_list
    ]

    # ── reference ────────────────────────────────────────────────────────────
    ref_raw: dict[str, Any] | None = data.get("reference")
    reference: ReferenceSolution | None = None
    if ref_raw is not None:
        reference = ReferenceSolution(
            expected_outcome=ref_raw["expected_outcome"],
            expected_duration=ref_raw.get("expected_duration"),
        )

    # Inherit *trials* from suite default when not explicitly set.
    trials = data.get("trials", default_trials)

    return EvalTask(
        id=data["id"],
        description=data.get("description", ""),
        instruction=data.get("instruction", ""),
        setup=setup,
        app=data.get("app"),
        tags=data.get("tags", []),
        trials=trials,
        graders=graders,
        scoring=scoring,
        tracked_metrics=tracked_metrics_list,
        timeout=data.get("timeout", 120),
        reference=reference,
    )


def discover_suites(evals_dir: str | None = None) -> list[EvalSuite]:
    """Scan a directory and load every sub-directory that contains a
    ``suite.yaml``."""
    base = Path(evals_dir) if evals_dir is not None else _DEFAULT_EVALS_DIR
    if not base.exists():
        return []

    suites: list[EvalSuite] = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "suite.yaml").is_file():
            try:
                suites.append(load_suite(str(child)))
            except Exception:
                pass
    return suites


def suite_names(evals_dir: str | None = None) -> list[str]:
    """Return the names of all suites under *evals_dir*."""
    return [s.name for s in discover_suites(evals_dir)]
