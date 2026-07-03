"""Markdown report generator for evaluation results.

Produces a human-readable Markdown report from a SuiteResult with sections for
overview, per-task details, stability analysis, performance metrics, history
comparison placeholder, a failed-transcript sample, and suggestions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import SuiteResult, TaskResult, TrialResult


class MarkdownReporter:
    """Generates a human-readable Markdown evaluation report."""

    @staticmethod
    def generate(result: SuiteResult) -> str:
        """Generate the full markdown report as a string."""
        lines: list[str] = []
        _header(lines, result)
        _section_overview(lines, result)
        _section_task_details(lines, result)
        _section_stability(lines, result)
        _section_performance(lines, result)
        _section_comparison(lines)
        _section_transcript(lines, result)
        _section_suggestions(lines, result)
        return "\n".join(lines)

    @staticmethod
    def save(result: SuiteResult, output_dir: Path) -> str:
        """Generate and save the report to *output_dir* / report.md."""
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "report.md"
        path.write_text(MarkdownReporter.generate(result), encoding="utf-8")
        return str(path)


# ── Helper utilities ─────────────────────────────────────────────────────────


def _avg(values: list[float]) -> float:
    """Return the average of *values*, or 0.0 if empty."""
    return sum(values) / len(values) if values else 0.0


def _find_first_failed_trial(
    result: SuiteResult,
) -> tuple[TaskResult, TrialResult] | None:
    """Return the first (task, trial) where the trial did not pass."""
    for tr in result.task_results:
        for t in tr.trials:
            if not t.passed:
                return tr, t
    return None


def _format_content_preview(content: Any, max_chars: int = 200) -> str:
    """Truncate message content to a single-line preview."""
    if isinstance(content, str):
        preview = content.replace("\n", " ")[:max_chars]
    elif isinstance(content, list):
        texts = [
            c.get("text", "") if isinstance(c, dict) else ""
            for c in content
        ]
        preview = " ".join(texts).replace("\n", " ")[:max_chars]
    else:
        preview = str(content).replace("\n", " ")[:max_chars]
    return preview


# ── Report sections ──────────────────────────────────────────────────────────


def _header(lines: list[str], result: SuiteResult) -> None:
    lines.append("# 评测报告")
    lines.append("")
    lines.append(f"- **评测套件:** {result.suite_name}")
    lines.append(f"- **运行ID:** {result.run_id}")
    lines.append(f"- **时间戳:** {result.timestamp}")
    lines.append(f"- **总耗时:** {result.duration:.1f}s")
    lines.append(f"- **模型:** {result.model_name}")
    lines.append(f"- **总任务数:** {len(result.task_results)}")
    lines.append("")


def _section_overview(lines: list[str], result: SuiteResult) -> None:
    lines.append("## 1. 总体概览")
    lines.append("")

    # Average task duration across all trials
    durations = [t.duration for tr in result.task_results for t in tr.trials]
    avg_dur = _avg(durations)

    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| pass@1 | {result.pass_at_1_rate:.1%} |")
    lines.append(f"| pass@k（任一通过） | {result.overall_pass_rate:.1%} |")
    lines.append(f"| pass^k（全部通过） | {result.pass_k_rate:.1%} |")
    lines.append(f"| 平均任务耗时 | {avg_dur:.1f}s |")
    lines.append("")


def _section_task_details(lines: list[str], result: SuiteResult) -> None:
    lines.append("## 2. 逐任务详情")
    lines.append("")

    for tr in result.task_results:
        icon = "✅" if tr.pass_at_k else "❌"
        lines.append(f"### {icon} {tr.task_id}")
        lines.append("")

        # ── Trial result table ──
        lines.append("#### 各轮次结果")
        lines.append("| 轮次 | 结果 | 分数 | 失败原因 | 耗时 |")
        lines.append("|------|------|------|----------|------|")
        for trial in tr.trials:
            passed_icon = "✅" if trial.passed else "❌"
            fail_reason = trial.failure_reason if trial.failure_reason else "-"
            lines.append(
                f"| {trial.trial_num} | {passed_icon} | {trial.score:.2f} |"
                f" {fail_reason} | {trial.duration:.1f}s |"
            )
        lines.append("")

        # ── Aggregated metrics ──
        lines.append("#### 聚合指标")
        lines.append("")
        lines.append(f"- **pass@1:** {'✅' if tr.pass_at_1 else '❌'}")
        lines.append(f"- **pass@k:** {'✅' if tr.pass_at_k else '❌'}")
        lines.append(f"- **all_passed:** {'✅' if tr.all_passed else '❌'}")
        lines.append(f"- **平均分:** {tr.mean_score:.2f}")
        lines.append("")

        # ── Grader details from the first trial ──
        first_trial = tr.trials[0] if tr.trials else None
        if first_trial and first_trial.grader_results:
            lines.append("#### 评分详情（首次轮次）")
            for gr in first_trial.grader_results:
                gr_icon = "✅" if gr.passed else "❌"
                lines.append(f"- {gr_icon} **{gr.grader_type}:** score={gr.score:.2f}")
            lines.append("")


def _section_stability(lines: list[str], result: SuiteResult) -> None:
    lines.append("## 3. 稳定性分析")
    lines.append("")
    lines.append("| 任务 | pass@k | all_passed | 稳定性 |")
    lines.append("|------|--------|------------|--------|")

    for tr in result.task_results:
        pass_at_k_icon = "✅" if tr.pass_at_k else "❌"
        all_pass_icon = "✅" if tr.all_passed else "❌"

        if tr.all_passed:
            label = "✅ 稳定"
        elif tr.pass_at_k:
            label = "⚠️ 偶发"
        else:
            label = "❌ 系统性问题"

        lines.append(
            f"| {tr.task_id} | {pass_at_k_icon} | {all_pass_icon} | {label} |"
        )
    lines.append("")


def _section_performance(lines: list[str], result: SuiteResult) -> None:
    lines.append("## 4. 性能指标")
    lines.append("")
    lines.append("| 任务 | 平均 tokens | 平均 turns | 平均 tool_calls | 平均耗时 |")
    lines.append("|------|-------------|------------|-----------------|----------|")

    for tr in result.task_results:
        tokens_list: list[float] = []
        turns_list: list[float] = []
        calls_list: list[float] = []
        dur_list: list[float] = []

        for t in tr.trials:
            dur_list.append(t.duration)
            if t.transcript and t.transcript.summary:
                s = t.transcript.summary
                tokens_list.append(float(s.total_tokens))
                turns_list.append(float(s.n_turns))
                calls_list.append(float(s.n_tool_calls))

        avg_tokens = _avg(tokens_list)
        avg_turns = _avg(turns_list)
        avg_calls = _avg(calls_list)
        avg_dur = _avg(dur_list)

        lines.append(
            f"| {tr.task_id} | {avg_tokens:.0f} | {avg_turns:.1f} |"
            f" {avg_calls:.1f} | {avg_dur:.1f}s |"
        )
    lines.append("")


def _section_comparison(lines: list[str]) -> None:
    lines.append("## 5. 历史对比")
    lines.append("")
    lines.append("> 历史对比功能将在后续版本中启用。")
    lines.append("")


def _section_transcript(lines: list[str], result: SuiteResult) -> None:
    lines.append("## 6. Transcript 示例")
    lines.append("")

    target = _find_first_failed_trial(result)
    if target is None:
        lines.append("> 所有任务均通过，无失败 Transcript。")
        lines.append("")
        return

    tr, trial = target
    if not trial.transcript or not trial.transcript.messages:
        lines.append(
            f"> 任务 `{tr.task_id}` 第 {trial.trial_num} 轮失败，"
            "但无 Transcript 记录。"
        )
        lines.append("")
        return

    messages = trial.transcript.messages[-6:]
    lines.append(
        f"> 以下为任务 `{tr.task_id}` 第 {trial.trial_num} 轮（失败）的"
        f"最后 {len(messages)} 条消息："
    )
    lines.append("")

    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        preview = _format_content_preview(content)
        lines.append(f"- **{role}:** {preview}")
    lines.append("")


def _section_suggestions(lines: list[str], result: SuiteResult) -> None:
    lines.append("## 7. 改进建议")
    lines.append("")

    failed = [tr for tr in result.task_results if not tr.all_passed]
    if not failed:
        lines.append("> 所有任务均稳定通过，无需改进。")
        lines.append("")
        return

    for tr in failed:
        if not tr.pass_at_k:
            lines.append(
                f"- **{tr.task_id}**: 所有轮次均失败，"
                "建议检查任务配置或环境。"
            )
        elif not tr.all_passed:
            lines.append(
                f"- **{tr.task_id}**: 存在偶发失败，"
                "建议增加重试或优化稳定性。"
            )
    lines.append("")
