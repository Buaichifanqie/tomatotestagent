"""CaseJudgeAgent — semantic-level test case evaluation.

Uses multimodal LLM to evaluate test case execution results
by analyzing screenshots, execution logs, and case context.
"""

from testagent.judge.case_judge_agent import (
    CaseJudgeAgent,
    CaseJudgeResult,
    should_invoke_judge,
)

__all__ = [
    "CaseJudgeAgent",
    "CaseJudgeResult",
    "should_invoke_judge",
]
