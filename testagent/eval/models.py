from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ── Configuration Models ─────────────────────────────────────────────────────


@dataclass
class SetupStep:
    """环境准备步骤。"""

    action: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraderConfig:
    """评判器配置。"""

    type: str
    expect: str
    rubric: str
    required: bool = True
    threshold: float = 1.0
    custom_expr: str | None = None


@dataclass
class ScoringConfig:
    """评分规则。"""

    mode: str = "hybrid"
    pass_threshold: float = 0.8
    mandatory: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=lambda: {"state_check": 0.5, "llm_rubric": 0.5})


@dataclass
class MetricConfig:
    """指标跟踪配置。"""

    type: str
    metrics: list[str] = field(default_factory=list)


@dataclass
class ReferenceSolution:
    """参考方案。"""

    expected_outcome: str
    expected_duration: float | None = None


# ── Task & Suite Models ──────────────────────────────────────────────────────


@dataclass
class EvalTask:
    """评测任务。"""

    id: str
    description: str
    instruction: str
    setup: list[SetupStep] = field(default_factory=list)
    app: str = ""
    tags: list[str] = field(default_factory=list)
    trials: int = 3
    graders: list[GraderConfig] = field(default_factory=list)
    scoring: ScoringConfig | None = None
    tracked_metrics: MetricConfig | None = None
    timeout: int = 120
    reference: ReferenceSolution | None = None


@dataclass
class EvalSuite:
    """评测套件。"""

    name: str
    description: str
    version: str = "1.0"
    default_trials: int = 3
    app: str = ""
    tags: list[str] = field(default_factory=list)
    tasks: list[EvalTask] = field(default_factory=list)


# ── Result Models ────────────────────────────────────────────────────────────


@dataclass
class GraderResult:
    """单个评判器结果。"""

    grader_type: str
    score: float = 0.0
    passed: bool = False
    details: str = ""


@dataclass
class TranscriptSummary:
    """轨迹摘要。"""

    n_turns: int = 0
    n_tool_calls: int = 0
    total_tokens: int = 0
    total_duration: float = 0.0
    tool_call_sequence: list[str] = field(default_factory=list)
    key_errors: list[str] = field(default_factory=list)
    final_page: str = ""


@dataclass
class Transcript:
    """完整执行轨迹。"""

    messages: list[dict[str, Any]] = field(default_factory=list)
    summary: TranscriptSummary | None = None


@dataclass
class TrialResult:
    """单次试次。"""

    trial_num: int
    passed: bool = False
    score: float = 0.0
    grader_results: list[GraderResult] = field(default_factory=list)
    transcript: Transcript | None = None
    failure_reason: str = ""
    duration: float = 0.0


@dataclass
class TaskResult:
    """多轮聚合结果。"""

    task_id: str
    trials: list[TrialResult] = field(default_factory=list)

    @property
    def pass_at_1(self) -> bool:
        """第一个试次是否通过。"""
        if not self.trials:
            return False
        return self.trials[0].passed

    @property
    def pass_at_k(self) -> bool:
        """是否有任意一个试次通过。"""
        return any(t.passed for t in self.trials)

    @property
    def pass_k(self) -> bool:
        """是否所有试次都通过。"""
        if not self.trials:
            return False
        return all(t.passed for t in self.trials)

    @property
    def mean_score(self) -> float:
        """所有试次的平均分。"""
        if not self.trials:
            return 0.0
        return sum(t.score for t in self.trials) / len(self.trials)

    @property
    def score_std(self) -> float:
        """所有试次的标准差（总体标准差）。"""
        if len(self.trials) < 2:
            return 0.0
        scores = [t.score for t in self.trials]
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        return math.sqrt(variance)

    @property
    def pass_rate(self) -> float:
        """试次通过率。"""
        if not self.trials:
            return 0.0
        return sum(1 for t in self.trials if t.passed) / len(self.trials)


@dataclass
class SuiteResult:
    """套件级结果。"""

    suite_name: str
    run_id: str
    timestamp: str
    task_results: list[TaskResult] = field(default_factory=list)
    duration: float = 0.0
    model_name: str = ""

    @property
    def overall_pass_rate(self) -> float:
        """整体通过率：至少有一个试次通过的任务比例。"""
        if not self.task_results:
            return 0.0
        return sum(1 for tr in self.task_results if tr.pass_at_k) / len(self.task_results)

    @property
    def pass_at_1_rate(self) -> float:
        """首次试次通过率。"""
        if not self.task_results:
            return 0.0
        return sum(1 for tr in self.task_results if tr.pass_at_1) / len(self.task_results)

    @property
    def pass_k_rate(self) -> float:
        """全部试次通过率。"""
        if not self.task_results:
            return 0.0
        return sum(1 for tr in self.task_results if tr.pass_k) / len(self.task_results)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 可序列化的字典，用于 CI 输出。"""
        return {
            "suite_name": self.suite_name,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "duration": self.duration,
            "model_name": self.model_name,
            "overall_pass_rate": self.overall_pass_rate,
            "pass_at_1_rate": self.pass_at_1_rate,
            "pass_k_rate": self.pass_k_rate,
            "num_tasks": len(self.task_results),
            "task_results": [
                {
                    "task_id": tr.task_id,
                    "pass_at_1": tr.pass_at_1,
                    "pass_at_k": tr.pass_at_k,
                    "pass_k": tr.pass_k,
                    "mean_score": tr.mean_score,
                    "score_std": tr.score_std,
                    "pass_rate": tr.pass_rate,
                    "num_trials": len(tr.trials),
                }
                for tr in self.task_results
            ],
        }
