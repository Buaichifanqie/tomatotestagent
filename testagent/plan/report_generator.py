from __future__ import annotations

from datetime import datetime
from pathlib import Path

from testagent.plan.models import (
    ExecutionVerdict,
    OverallEvaluation,
    PlanConfig,
    StepExecution,
    TestCase,
)


class ReportGenerator:
    """Produces structured Markdown test reports."""

    # ── verdict → emoji mapping ─────────────────────────────────────────────
    _VERDICT_EMOJI: dict[ExecutionVerdict, str] = {
        ExecutionVerdict.PASS: "✅",
        ExecutionVerdict.FAIL: "❌",
        ExecutionVerdict.BLOCKED: "⛔",
        ExecutionVerdict.NEED_REVIEW: "⚠️",
        ExecutionVerdict.INCONCLUSIVE: "❓",
        ExecutionVerdict.PARTIAL: "⚠️",
    }

    def __init__(self, output_dir: str) -> None:
        self._output_dir = Path(output_dir)

    # ── public API ──────────────────────────────────────────────────────────

    def generate(
        self,
        plan_name: str,
        test_cases: list[TestCase],
        overall: OverallEvaluation,
        config: PlanConfig,
    ) -> str:
        """Build the report, write to {output_dir}/plan-report.md, return path."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        report = self._build_report(plan_name, test_cases, overall, config)
        path = self._output_dir / "plan-report.md"
        path.write_text(report, encoding="utf-8")
        return str(path)

    # ── report assembly ─────────────────────────────────────────────────────

    def _build_report(
        self,
        plan_name: str,
        test_cases: list[TestCase],
        overall: OverallEvaluation,
        config: PlanConfig,
    ) -> str:
        lines: list[str] = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ── header ──────────────────────────────────────────────────────────
        lines.append("# 测试报告")
        lines.append("")
        lines.append(f"**计划名称:** {plan_name}")
        lines.append(f"**生成时间:** {now}")
        lines.append("")

        # ── 总体评估 ────────────────────────────────────────────────────────
        lines.append("## 总体评估")
        lines.append("")
        badge = self._verdict_badge(overall.verdict)
        lines.append(f"**总体判定:** {badge}")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|---|---|")
        lines.append(f"| 通过率 | {overall.pass_rate} |")
        lines.append(f"| 核心用例通过率 | {overall.core_pass_rate} |")
        lines.append(f"| 待审核用例 | {overall.need_review_count} |")
        lines.append(f"| 阻塞用例 | {overall.blocked_count} |")
        lines.append("")
        if overall.summary:
            lines.append(f"**总结:** {overall.summary}")
            lines.append("")

        # ── 测试结果汇总 ────────────────────────────────────────────────────
        lines.append("## 测试结果汇总")
        lines.append("")
        lines.append(
            "| ID | 标题 | 优先级 | 核心用例 | 状态 | 判定 | 耗时(ms) | 错误信息 |"
        )
        lines.append(
            "|---|---|---|---|---|---|---|---|"
        )
        for tc in test_cases:
            core_mark = "✅" if tc.is_core else "❌"
            verdict_str = tc.execution.verdict.value if tc.execution.verdict else ""
            duration = tc.execution.duration_ms
            error = tc.execution.error_message or ""
            lines.append(
                f"| {tc.id} | {tc.title} | {tc.priority} "
                f"| {core_mark} | {tc.execution.status.value} "
                f"| {verdict_str} | {duration} | {error} |"
            )
        lines.append("")

        # ── 详细执行记录 ────────────────────────────────────────────────────
        lines.append("## 详细执行记录")
        lines.append("")
        for tc in test_cases:
            verdict_emoji = self._VERDICT_EMOJI.get(
                tc.execution.verdict, ""
            ) if tc.execution.verdict else ""
            lines.append(f"### {tc.id}: {tc.title} {verdict_emoji}")
            lines.append("")
            steps = tc.execution.steps
            if steps:
                lines.append(
                    "| 步骤 | 操作 | 目标 | 结果 | 耗时(ms) | 错误信息 |"
                )
                lines.append(
                    "|---|---|---|---|---|---|"
                )
                for s in steps:
                    result_mark = "✅" if s.success else "❌"
                    err = s.error_message or ""
                    dur = s.duration_ms if s.duration_ms is not None else ""
                    lines.append(
                        f"| {s.step} | {s.action} | {s.target} "
                        f"| {result_mark} | {dur} | {err} |"
                    )
            else:
                lines.append("无详细步骤记录。")
            lines.append("")

        # ── 需人工复查的用例 ────────────────────────────────────────────────
        lines.append("## 需人工复查的用例")
        lines.append("")
        review_tcs = [
            tc for tc in test_cases
            if tc.execution.verdict == ExecutionVerdict.NEED_REVIEW
        ]
        if review_tcs:
            for tc in review_tcs:
                lines.append(
                    f"- **{tc.id}**: {tc.title} "
                    f"({tc.execution.error_message or '待人工确认'})"
                )
        else:
            lines.append("无")
        lines.append("")

        return "\n".join(lines)

    # ── static helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _verdict_badge(verdict: ExecutionVerdict) -> str:
        mapping: dict[ExecutionVerdict, str] = {
            ExecutionVerdict.PASS: "✅ PASS",
            ExecutionVerdict.FAIL: "❌ FAIL",
            ExecutionVerdict.BLOCKED: "⛔ BLOCKED",
            ExecutionVerdict.NEED_REVIEW: "⚠️ NEED_REVIEW",
            ExecutionVerdict.INCONCLUSIVE: "❓ INCONCLUSIVE",
            ExecutionVerdict.PARTIAL: "⚠️ PARTIAL",
        }
        return mapping[verdict]
