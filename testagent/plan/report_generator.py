from __future__ import annotations

from datetime import datetime
from pathlib import Path

from testagent.plan.coordinate_cache import CacheStats
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
        cache_stats: CacheStats | None = None,
    ) -> str:
        """Build the report, write to {output_dir}/plan-report.md, return path."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        report = self._build_report(plan_name, test_cases, overall, config, cache_stats)
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
        cache_stats: CacheStats | None = None,
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

        # ── 缓存效率统计 ────────────────────────────────────────────────────
        if cache_stats and cache_stats.hits + cache_stats.misses > 0:
            lines.append("## 缓存效率统计")
            lines.append("")
            lines.append("| 指标 | 值 |")
            lines.append("|---|---|")
            lines.append(f"| 缓存命中次数 | {cache_stats.hits} |")
            lines.append(f"| 缓存未命中次数 | {cache_stats.misses} |")
            lines.append(f"| 缓存命中率 | {cache_stats.hit_rate:.1%} |")
            lines.append(f"| 回退重试次数 | {cache_stats.fallbacks} |")
            lines.append("")

        # ── 重试统计 ────────────────────────────────────────────────────────
        retried_tcs = [tc for tc in test_cases if tc.execution.retries > 0]
        if retried_tcs:
            lines.append("## 重试统计")
            lines.append("")
            lines.append(f"共有 **{len(retried_tcs)}** 个用例经过重试：")
            lines.append("")
            lines.append("| ID | 标题 | 重试次数 | 首次失败原因 | 重试结果 |")
            lines.append("|---|---|---|---|---|")
            for tc in retried_tcs:
                first_err = ""
                if tc.execution.previous_attempts:
                    first_err = str(tc.execution.previous_attempts[0].get("error_message", ""))
                    if len(first_err) > 60:
                        first_err = first_err[:57] + "..."
                retry_verdict = tc.execution.verdict.value if tc.execution.verdict else ""
                retry_mark = "✅ 通过" if retry_verdict == "PASS" else "❌ 仍失败"
                lines.append(
                    f"| {tc.id} | {tc.title} | {tc.execution.retries} "
                    f"| {first_err} | {retry_mark} |"
                )
            lines.append("")

        # ── 测试结果汇总 ────────────────────────────────────────────────────
        lines.append("## 测试结果汇总")
        lines.append("")
        lines.append(
            "| ID | 标题 | 优先级 | 核心用例 | 状态 | 判定 | 重试 | 耗时(ms) | 错误信息 |"
        )
        lines.append(
            "|---|---|---|---|---|---|---|---|---|"
        )
        for tc in test_cases:
            core_mark = "✓" if tc.is_core else ""
            verdict_str = tc.execution.verdict.value if tc.execution.verdict else ""
            duration = tc.execution.duration_ms
            error = tc.execution.error_message or ""
            retry_mark = f"🔄 ×{tc.execution.retries}" if tc.execution.retries > 0 else ""
            lines.append(
                f"| {tc.id} | {tc.title} | {tc.priority} "
                f"| {core_mark} | {tc.execution.status.value} "
                f"| {verdict_str} | {retry_mark} | {duration} | {error} |"
            )
        lines.append("")

        # ── 详细执行记录 ────────────────────────────────────────────────────
        lines.append("## 详细执行记录")
        lines.append("")
        for tc in test_cases:
            verdict_emoji = self._VERDICT_EMOJI.get(
                tc.execution.verdict, ""
            ) if tc.execution.verdict else ""
            retry_label = ""
            if tc.execution.retries > 0:
                retry_label = f" 🔄 重试×{tc.execution.retries}"
                if tc.execution.previous_attempts:
                    first_verdict = str(tc.execution.previous_attempts[0].get("verdict", ""))
                    first_err = str(tc.execution.previous_attempts[0].get("error_message", ""))
                    if first_err and len(first_err) > 80:
                        first_err = first_err[:77] + "..."
                    retry_label += f"（首次: {first_verdict}{' — ' + first_err if first_err else ''}）"
            lines.append(f"### {tc.id}: {tc.title} {verdict_emoji}{retry_label}")
            lines.append("")

            # ── Evidence (recording, screenshots) ────────────────────
            if tc.execution.evidence:
                lines.append("**证据:**")
                lines.append("")
                for ev in tc.execution.evidence:
                    ev_path = Path(ev.path)
                    if ev.type == "recording":
                        rel = ev_path.relative_to(self._output_dir)
                        lines.append(
                            f"- 🎬 [录屏回放]({rel.as_posix()})"
                        )
                    elif ev.type == "screenshot" and ev_path.exists():
                        rel = ev_path.relative_to(self._output_dir)
                        lines.append(
                            f"- 🖼️ <img src=\"{rel.as_posix()}\" width=\"360\">"
                        )
                lines.append("")

            steps = tc.execution.steps
            if steps:
                lines.append(
                    "| 步骤 | 操作 | 目标 | 来源 | 结果 | 耗时(ms) | 错误信息 |"
                )
                lines.append(
                    "|---|---|---|---|---|---|---|"
                )
                for s in steps:
                    result_mark = "✅" if s.success else "❌"
                    if s.success and s.warning:
                        result_mark = "⚠️"
                    err = s.error_message or ""
                    dur = s.duration_ms if s.duration_ms is not None else ""
                    source = s.source or ""
                    if source:
                        source_mark = f"🟡 Cache Hit ({source})"
                    else:
                        source_mark = "🟢 LLM 视觉识别"
                    lines.append(
                        f"| {s.step} | {s.action} | {s.target} "
                        f"| {source_mark} | {result_mark} | {dur} | {err} |"
                    )
                    # Assert warning detail
                    if s.warning:
                        lines.append("")
                        lines.append(f"  > ⚠️ **断言警告:** {s.warning}")
                        lines.append("")
                    # Screenshot for failed steps and warning steps
                    if (not s.success or s.warning) and s.screenshot_after:
                        scr_path = Path(s.screenshot_after)
                        try:
                            rel = scr_path.relative_to(self._output_dir)
                            lines.append("")
                            lines.append(f'  <img src="{rel.as_posix()}" width="360" alt="失败截图">')
                            lines.append("")
                        except ValueError:
                            pass
                    # Vision analysis for failed steps and warning steps
                    if (not s.success or s.warning) and s.vision_analysis:
                        lines.append("")
                        lines.append("  > **多模态分析:**")
                        for line in s.vision_analysis.strip().split("\n"):
                            lines.append(f"  > {line}")
                        lines.append("")
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
