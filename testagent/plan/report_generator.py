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
        """Build report, write to {output_dir}/plan-report.md and .html, return md path."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        report = self._build_report(plan_name, test_cases, overall, config, cache_stats)
        md_path = self._output_dir / "plan-report.md"
        md_path.write_text(report, encoding="utf-8")

        # Also generate HTML version
        html_path = self._output_dir / "plan-report.html"
        html_path.write_text(self._md_to_html(report), encoding="utf-8")

        return str(md_path)

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
                    # Path may be relative (from evidence) or absolute (legacy)
                    if ev_path.is_absolute():
                        try:
                            rel = ev_path.relative_to(self._output_dir).as_posix()
                        except ValueError:
                            rel = ev_path.name
                    else:
                        rel = ev_path.as_posix()
                    if ev.type == "recording":
                        lines.append(f"- 🎬 [录屏回放]({rel})")
                    elif ev.type == "screenshot" and Path(self._output_dir, ev.path).exists():
                        lines.append(f"- 🖼️ <img src=\"{rel}\" width=\"360\">")
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
                        if scr_path.is_absolute():
                            try:
                                rel = scr_path.relative_to(self._output_dir).as_posix()
                            except ValueError:
                                rel = scr_path.name
                        else:
                            rel = scr_path.as_posix()
                        lines.append("")
                        lines.append(f'  <img src="{rel}" width="360" alt="失败截图">')
                        lines.append("")
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

            # ── CaseJudgeAgent 裁判结果 ────────────────────────────────
            if tc.execution.judge_reasoning:
                lines.append("**🤖 AI 裁判评估:**")
                lines.append("")
                if tc.execution.failure_category and tc.execution.failure_category != "NONE":
                    lines.append(f"- 失败分类: `{tc.execution.failure_category}`")
                if tc.execution.failure_root_cause:
                    lines.append(f"- 根因分析: {tc.execution.failure_root_cause}")
                if tc.execution.judge_confidence:
                    lines.append(f"- 置信度: {tc.execution.judge_confidence:.2f}")
                lines.append(f"- 推理过程: {tc.execution.judge_reasoning[:500]}")
                if tc.execution.judge_evidence:
                    lines.append("- 证据:")
                    for ev in tc.execution.judge_evidence[:5]:
                        lines.append(f"  - {ev}")
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

    @staticmethod
    def _md_to_html(md: str) -> str:
        """Convert markdown to a self-contained styled HTML page.

        Handles the patterns used in plan-report.md without external deps.
        """
        import html as _html
        import re
        lines = md.split("\n")
        html_lines: list[str] = []
        in_code = False
        code_buf: list[str] = []
        in_table = False
        table_buf: list[str] = []

        def flush_table():
            nonlocal table_buf, in_table
            if not table_buf:
                return
            html_lines.append('<table class="data-table">')
            for i, row in enumerate(table_buf):
                tag = "th" if i == 0 else "td"
                cells = [c.strip() for c in row.split("|")[1:-1]]
                escaped = [_html.escape(c) for c in cells]
                sep = f"</{tag}><{tag}>"
                html_lines.append(f"<tr><{tag}>{sep.join(escaped)}</{tag}></tr>")
            html_lines.append("</table>")
            table_buf = []
            in_table = False

        for line in lines:
            if line.startswith("```"):
                if in_code:
                    html_lines.append(f"<pre><code>{_html.escape(chr(10).join(code_buf))}</code></pre>")
                    code_buf = []
                    in_code = False
                else:
                    flush_table()
                    in_code = True
                continue
            if in_code:
                code_buf.append(line)
                continue

            flush_table()

            if not line.strip():
                html_lines.append("<br>")
                continue

            if line.startswith("|") and line.endswith("|"):
                if "---" in line:
                    continue
                in_table = True
                table_buf.append(line)
                continue

            if line.startswith("# "):
                html_lines.append(f"<h1>{_html.escape(line[2:])}</h1>")
            elif line.startswith("## "):
                html_lines.append(f"<h2>{_html.escape(line[3:])}</h2>")
            elif line.startswith("### "):
                html_lines.append(f"<h3>{_html.escape(line[4:])}</h3>")
            elif line.startswith("#### "):
                html_lines.append(f"<h4>{_html.escape(line[5:])}</h4>")
            else:
                text = _html.escape(line)
                text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
                text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
                text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
                text = re.sub(r'!\[(.*?)\]\((.+?)\)', r'<img src="\2" alt="\1" style="max-width:360px">', text)
                text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
                if text.strip().startswith("- ") or text.strip().startswith("* "):
                    html_lines.append(f"<li>{text.strip()[2:]}</li>")
                else:
                    html_lines.append(f"<p>{text}</p>")

        flush_table()

        body = "\n".join(html_lines)
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TestAgent Report</title>
<style>
  body {{ font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
         max-width: 960px; margin: 0 auto; padding: 20px; background: #f8f9fa; color: #333; }}
  h1, h2, h3 {{ color: #1a1a2e; border-bottom: 2px solid #e0e0e0; padding-bottom: 6px; }}
  table.data-table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  table.data-table th {{ background: #4a6fa5; color: #fff; padding: 8px 12px; text-align: left; }}
  table.data-table td {{ padding: 8px 12px; border: 1px solid #ddd; }}
  table.data-table tr:nth-child(even) {{ background: #f2f2f2; }}
  img {{ max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; margin: 8px 0; }}
  pre {{ background: #1e1e2e; color: #cdd6f4; padding: 14px; border-radius: 6px; overflow-x: auto; }}
  code {{ background: #e8e8e8; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
  pre code {{ background: none; padding: 0; }}
  a {{ color: #4a6fa5; }}
  li {{ margin: 4px 0; }}
  p {{ line-height: 1.7; }}
  br {{ display: block; margin: 4px 0; content: ""; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
