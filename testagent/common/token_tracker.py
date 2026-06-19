"""Token usage tracker for LLM, Vision, and Judge API calls.

Tracks token consumption per test case and per category,
provides real-time display and chart generation.

Phases:
- Generation phase: TC generation LLM calls (before execution)
- Execution phase: Per-step LLM + Vision calls (during execution)
- Judge phase: CaseJudgeAgent video analysis (after execution)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testagent.common.logging import get_logger

_logger = get_logger(__name__)


@dataclass
class TokenUsage:
    """Token usage for a single category."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, prompt: int = 0, completion: int = 0, total: int = 0) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        if total:
            self.total_tokens += total
        else:
            self.total_tokens += prompt + completion

    @property
    def display(self) -> str:
        if self.total_tokens == 0:
            return "0"
        if self.prompt_tokens and self.completion_tokens:
            return f"{self.prompt_tokens:,}↑ {self.completion_tokens:,}↓ = {self.total_tokens:,}"
        return f"{self.total_tokens:,}"


@dataclass
class TCTokenUsage:
    """Token usage for a single test case."""
    generation: TokenUsage = field(default_factory=TokenUsage)  # TC generation phase
    llm: TokenUsage = field(default_factory=TokenUsage)         # Step execution LLM
    vision: TokenUsage = field(default_factory=TokenUsage)      # Step execution Vision
    judge: TokenUsage = field(default_factory=TokenUsage)       # CaseJudgeAgent

    @property
    def total(self) -> int:
        return self.generation.total_tokens + self.llm.total_tokens + self.vision.total_tokens + self.judge.total_tokens


class TokenTracker:
    """Global token usage tracker.

    Usage:
        tracker = TokenTracker()

        # TC generation phase
        tracker.start_generation()
        # ... LLM calls for TC generation ...
        tracker.end_generation()

        # TC execution phase
        tracker.set_current_tc("TC-001")
        tracker.record("llm", prompt_tokens=100, completion_tokens=50)
        tracker.record("vision", total_tokens=200)
        tracker.record("judge", prompt_tokens=500, completion_tokens=200)

        # Per-TC summary
        tracker.print_tc_summary()

        # Final chart
        tracker.generate_chart(output_dir)
    """

    # ANSI color codes
    _COLOR_GEN = "\033[32m"      # green
    _COLOR_LLM = "\033[36m"      # cyan
    _COLOR_VISION = "\033[35m"   # magenta
    _COLOR_JUDGE = "\033[33m"    # yellow
    _COLOR_TOTAL = "\033[37m"    # white
    _COLOR_RESET = "\033[0m"

    def __init__(self) -> None:
        self._current_tc_id: str = ""
        self._per_tc: dict[str, TCTokenUsage] = {}
        self._global = TCTokenUsage()
        self._generation_tokens = TokenUsage()  # Accumulated generation tokens

    def start_generation(self) -> None:
        """Mark the start of TC generation phase."""
        self._current_tc_id = "__generation__"

    def end_generation(self) -> None:
        """Mark the end of TC generation phase. Tokens are stored in _generation_tokens."""
        gen_usage = self._per_tc.get("__generation__")
        if gen_usage:
            self._generation_tokens = gen_usage.llm  # Generation uses LLM category
        self._current_tc_id = ""

    def set_current_tc(self, tc_id: str) -> None:
        """Set the current test case context. Execution tokens start from 0."""
        self._current_tc_id = tc_id
        if tc_id not in self._per_tc:
            self._per_tc[tc_id] = TCTokenUsage()

    def record(
        self,
        category: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        """Record token usage for the current test case.

        Args:
            category: "llm", "vision", or "judge"
            prompt_tokens: Input/prompt tokens
            completion_tokens: Output/completion tokens
            total_tokens: Total tokens (if prompt+completion not available)
        """
        tc_usage = self._per_tc.get(self._current_tc_id)
        if tc_usage is None:
            tc_usage = TCTokenUsage()
            self._per_tc[self._current_tc_id] = tc_usage

        target = {
            "llm": tc_usage.llm,
            "vision": tc_usage.vision,
            "judge": tc_usage.judge,
        }.get(category)

        if target:
            target.add(prompt=prompt_tokens, completion=completion_tokens, total=total_tokens)
            # Also add to global
            global_target = {
                "llm": self._global.llm,
                "vision": self._global.vision,
                "judge": self._global.judge,
            }[category]
            global_target.add(prompt=prompt_tokens, completion=completion_tokens, total=total_tokens)

    def print_tc_summary(self) -> None:
        """Print token usage summary for the current test case (execution phase only)."""
        tc_usage = self._per_tc.get(self._current_tc_id)
        if not tc_usage:
            return

        # Only show execution-phase tokens (LLM + Vision + Judge), not generation
        exec_total = tc_usage.llm.total_tokens + tc_usage.vision.total_tokens + tc_usage.judge.total_tokens
        if exec_total == 0:
            return

        C = self._COLOR_LLM
        V = self._COLOR_VISION
        J = self._COLOR_JUDGE
        T = self._COLOR_TOTAL
        R = self._COLOR_RESET

        parts = []
        if tc_usage.llm.total_tokens > 0:
            parts.append(f"{C}LLM:{tc_usage.llm.display}{R}")
        if tc_usage.vision.total_tokens > 0:
            parts.append(f"{V}Vision:{tc_usage.vision.display}{R}")
        if tc_usage.judge.total_tokens > 0:
            parts.append(f"{J}Judge:{tc_usage.judge.display}{R}")

        if parts:
            print(f"  {T}[Tokens] {' | '.join(parts)} | Total:{exec_total:,}{R}")

    def print_global_summary(self) -> None:
        """Print global token usage summary."""
        G = self._COLOR_GEN
        C = self._COLOR_LLM
        V = self._COLOR_VISION
        J = self._COLOR_JUDGE
        T = self._COLOR_TOTAL
        R = self._COLOR_RESET

        gen_total = self._generation_tokens.total_tokens

        print(f"\n{'='*60}")
        print(f"  {T}[Token Usage Summary]{R}")
        print(f"{'='*60}")
        if gen_total > 0:
            print(f"  {G}Generation: {self._generation_tokens.display}{R}")
        if self._global.llm.total_tokens > 0:
            print(f"  {C}LLM:        {self._global.llm.display}{R}")
        if self._global.vision.total_tokens > 0:
            print(f"  {V}Vision:     {self._global.vision.display}{R}")
        if self._global.judge.total_tokens > 0:
            print(f"  {J}Judge:      {self._global.judge.display}{R}")
        total = gen_total + self._global.total
        print(f"  {T}Total:      {total:,}{R}")
        print(f"{'='*60}")

    def generate_chart(self, output_dir: str) -> str | None:
        """Generate an HTML bar chart of token usage per test case.

        Returns the path to the HTML chart, or None if no data.
        No external dependencies required (pure HTML/CSS/JS).
        """
        if not self._per_tc or self._global.total == 0:
            return None

        # Prepare data
        rows = []
        for tc_id, usage in sorted(self._per_tc.items()):
            if tc_id.startswith("__"):
                continue
            if usage.total > 0:
                short_id = tc_id.replace("TC-VIDEO-PLAYBACK-", "TC-")
                rows.append({
                    "id": short_id,
                    "gen": usage.generation.total_tokens,
                    "llm": usage.llm.total_tokens,
                    "vision": usage.vision.total_tokens,
                    "judge": usage.judge.total_tokens,
                    "total": usage.total,
                })

        if not rows:
            return None

        gen_total = self._generation_tokens.total_tokens
        llm_total = self._global.llm.total_tokens
        vision_total = self._global.vision.total_tokens
        judge_total = self._global.judge.total_tokens
        grand_total = gen_total + llm_total + vision_total + judge_total

        # Build HTML
        max_total = max(r["total"] for r in rows)
        rows_html = []
        for r in rows:
            pct_gen = r["gen"] / max_total * 100 if max_total else 0
            pct_llm = r["llm"] / max_total * 100 if max_total else 0
            pct_vision = r["vision"] / max_total * 100 if max_total else 0
            pct_judge = r["judge"] / max_total * 100 if max_total else 0

            bars = ""
            if pct_gen > 0:
                bars += f'<span class="bar gen" style="width:{pct_gen:.1f}%">{r["gen"]:,}</span>'
            if pct_llm > 0:
                bars += f'<span class="bar llm" style="width:{pct_llm:.1f}%">{r["llm"]:,}</span>'
            if pct_vision > 0:
                bars += f'<span class="bar vision" style="width:{pct_vision:.1f}%">{r["vision"]:,}</span>'
            if pct_judge > 0:
                bars += f'<span class="bar judge" style="width:{pct_judge:.1f}%">{r["judge"]:,}</span>'

            rows_html.append(f'<tr><td class="tc-id">{r["id"]}</td><td class="bar-cell">{bars}</td><td class="total">{r["total"]:,}</td></tr>')

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Token Usage Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 40px; background: #f5f5f5; }}
  h1 {{ color: #333; }}
  .summary {{ display: flex; gap: 20px; margin: 20px 0; }}
  .summary-card {{ background: white; padding: 16px 24px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); min-width: 140px; }}
  .summary-card .label {{ font-size: 12px; color: #666; text-transform: uppercase; }}
  .summary-card .value {{ font-size: 24px; font-weight: bold; margin-top: 4px; }}
  .summary-card.gen .value {{ color: #4CAF50; }}
  .summary-card.llm .value {{ color: #00BCD4; }}
  .summary-card.vision .value {{ color: #9C27B0; }}
  .summary-card.judge .value {{ color: #FF9800; }}
  .summary-card.total .value {{ color: #333; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  th {{ background: #f0f0f0; padding: 12px 16px; text-align: left; font-size: 13px; color: #666; }}
  td {{ padding: 10px 16px; border-bottom: 1px solid #eee; }}
  .tc-id {{ font-weight: 600; white-space: nowrap; width: 120px; }}
  .bar-cell {{ width: 100%; }}
  .bar {{ display: inline-block; height: 22px; border-radius: 3px; margin-right: 2px; font-size: 10px; color: white; line-height: 22px; padding: 0 4px; min-width: 2px; }}
  .bar.gen {{ background: #4CAF50; }}
  .bar.llm {{ background: #00BCD4; }}
  .bar.vision {{ background: #9C27B0; }}
  .bar.judge {{ background: #FF9800; }}
  .total {{ text-align: right; font-weight: 600; white-space: nowrap; }}
  .legend {{ display: flex; gap: 16px; margin: 12px 0; font-size: 13px; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .legend-color {{ width: 14px; height: 14px; border-radius: 3px; }}
</style></head><body>
<h1>Token Usage Report</h1>
<div class="summary">
  <div class="summary-card gen"><div class="label">Generation</div><div class="value">{gen_total:,}</div></div>
  <div class="summary-card llm"><div class="label">LLM</div><div class="value">{llm_total:,}</div></div>
  <div class="summary-card vision"><div class="label">Vision</div><div class="value">{vision_total:,}</div></div>
  <div class="summary-card judge"><div class="label">Judge</div><div class="value">{judge_total:,}</div></div>
  <div class="summary-card total"><div class="label">Total</div><div class="value">{grand_total:,}</div></div>
</div>
<div class="legend">
  <div class="legend-item"><div class="legend-color" style="background:#4CAF50"></div>Generation</div>
  <div class="legend-item"><div class="legend-color" style="background:#00BCD4"></div>LLM</div>
  <div class="legend-item"><div class="legend-color" style="background:#9C27B0"></div>Vision</div>
  <div class="legend-item"><div class="legend-color" style="background:#FF9800"></div>Judge</div>
</div>
<table>
<tr><th>Test Case</th><th>Token Usage</th><th style="text-align:right">Total</th></tr>
{"".join(rows_html)}
</table>
</body></html>"""

        chart_path = str(Path(output_dir) / "token_usage_chart.html")
        Path(chart_path).write_text(html, encoding="utf-8")
        _logger.info("Token usage chart saved to %s", chart_path)
        return chart_path

    def to_dict(self) -> dict[str, Any]:
        """Export all token usage data as a dict."""
        result = {}
        for tc_id, usage in self._per_tc.items():
            result[tc_id] = {
                "generation": {"total": usage.generation.total_tokens},
                "llm": {"prompt": usage.llm.prompt_tokens, "completion": usage.llm.completion_tokens, "total": usage.llm.total_tokens},
                "vision": {"prompt": usage.vision.prompt_tokens, "completion": usage.vision.completion_tokens, "total": usage.vision.total_tokens},
                "judge": {"prompt": usage.judge.prompt_tokens, "completion": usage.judge.completion_tokens, "total": usage.judge.total_tokens},
                "total": usage.total,
            }
        return result
