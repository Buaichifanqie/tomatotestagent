"""TestAgent Eval — AI Agent 评测子系统。

提供 CLI 命令和 API 接口，支持：
- YAML 定义评测任务套件
- 多轮运行 + pass@k/pass^k 指标
- Code-based + Model-based 评判器
- Markdown + JSON 报告输出
"""

from testagent.eval.models import (
    EvalTask,
    EvalSuite,
    GraderConfig,
    GraderResult,
    ScoringConfig,
    SetupStep,
    SuiteResult,
    TaskResult,
    Transcript,
    TranscriptSummary,
    TrialResult,
)
from testagent.eval.runner import EvalRunner
from testagent.eval.loader import load_suite, discover_suites, suite_names

__all__ = [
    "EvalRunner",
    "EvalTask",
    "EvalSuite",
    "GraderConfig",
    "GraderResult",
    "ScoringConfig",
    "SetupStep",
    "SuiteResult",
    "TaskResult",
    "Transcript",
    "TranscriptSummary",
    "TrialResult",
    "load_suite",
    "discover_suites",
    "suite_names",
]
