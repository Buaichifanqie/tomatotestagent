from __future__ import annotations

import json
import re
from typing import Any

from testagent.eval.graders.base import BaseGrader
from testagent.eval.models import EvalTask, GraderResult, Transcript


class LlmRubricGrader(BaseGrader):
    """LLM-as-Judge grader that scores agent execution quality against a rubric.

    Uses an external LLM provider to evaluate how well an agent's execution
    transcript meets the criteria defined in ``config.rubric``.

    Parameters
    ----------
    config:
        Grader configuration with ``rubric`` string.
    llm_provider:
        Any object with an ``async chat(system, messages, temperature, max_tokens)``
        method returning a response with ``.content`` (list of content blocks)
        and ``.usage`` (with ``.input_tokens``, ``.output_tokens``, ``.total_tokens``).
    recorder:
        Optional ``TranscriptRecorder`` for tracking judge token usage.
    """

    def __init__(self, config, llm_provider, recorder=None) -> None:
        super().__init__(config)
        self._llm = llm_provider
        self._recorder = recorder

    async def grade(
        self, transcript: Transcript, task: EvalTask, **kwargs
    ) -> GraderResult:
        """Grade execution quality using LLM-as-Judge.

        Builds a judge prompt with the task description, rubric, transcript
        summary, and a preview of the last 6 messages, then calls the LLM.
        Parses the JSON response into a normalized score.
        """
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(transcript, task)

        # ── Call LLM ──────────────────────────────────────────────────────────
        try:
            response = await self._llm.chat(
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0,
                max_tokens=512,
            )
        except Exception:
            return GraderResult(
                grader_type="llm_rubric",
                score=0.0,
                passed=False,
                details="LLM error",
            )

        # ── Record token usage ────────────────────────────────────────────────
        if self._recorder is not None:
            usage = response.usage
            if isinstance(usage, dict):
                self._recorder.record_usage(
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                )
            else:
                self._recorder.record_usage(
                    input_tokens=getattr(usage, "input_tokens", 0),
                    output_tokens=getattr(usage, "output_tokens", 0),
                    total_tokens=getattr(usage, "total_tokens", 0),
                )

        # ── Parse response ────────────────────────────────────────────────────
        text = self._extract_text(response)
        parsed = self._parse_json(text)

        if parsed is None:
            return GraderResult(
                grader_type="llm_rubric",
                score=0.0,
                passed=False,
                details="parse error",
            )

        raw_score = parsed.get("score", 0)
        passed = parsed.get("passed", False)
        reason = parsed.get("reason", "")

        normalized = max(0.0, min(1.0, raw_score / 5.0))

        return GraderResult(
            grader_type="llm_rubric",
            score=normalized,
            passed=bool(passed),
            details=str(reason) if reason else "",
        )

    # ── Prompt builders ──────────────────────────────────────────────────────

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "你是一个 AI Agent 执行质量评判员。根据 rubric 和执行轨迹评分。\n\n"
            "评分规则：满分5分，4分及以上视为通过。\n"
            "返回 JSON 格式（不要 markdown 代码块）：\n"
            '{"score": <1-5>, "passed": <true/false>, "reason": "<简短原因>"}\n\n'
            '如果无法判断，返回 {"score": 0, "passed": false, "reason": "UNKNOWN: <原因>"}'
        )

    def _build_user_prompt(self, transcript: Transcript, task: EvalTask) -> str:
        lines: list[str] = []
        lines.append(f"## Task\n{task.description}")

        rubric = self.config.rubric
        if rubric:
            lines.append(f"\n## Rubric\n{rubric}")

        summary = transcript.summary
        if summary is not None:
            lines.append("\n## Transcript Summary")
            lines.append(f"- Turns: {summary.n_turns}")
            lines.append(f"- Tool calls: {summary.n_tool_calls}")
            if summary.tool_call_sequence:
                seq = " → ".join(summary.tool_call_sequence)
                lines.append(f"- Tool sequence: {seq}")
            if summary.key_errors:
                lines.append(f"- Errors: {', '.join(summary.key_errors)}")

        messages = transcript.messages or []
        preview = messages[-6:] if messages else []
        if preview:
            lines.append(f"\n## Last {len(preview)} Messages")
            for i, msg in enumerate(preview):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if isinstance(content, (dict, list)):
                    content = json.dumps(content, ensure_ascii=False)
                elif not isinstance(content, str):
                    content = str(content)
                if len(content) > 500:
                    content = content[:500] + "..."
                lines.append(f"[{i}] ({role}) {content[:200]}")

        return "\n".join(lines)

    # ── Response parsing ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Extract text content from an LLM response.

        Handles:
        - ``response.content`` as a list of dicts with ``type``/``text`` keys
        - ``response.content`` as a plain string
        - Any other shape via string coercion
        """
        content = response.content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        return block.get("text", "")
                    if "text" in block:
                        return block["text"]
            return str(content)
        if isinstance(content, str):
            return content
        return str(content)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        """Parse JSON from LLM response text, stripping markdown fences."""
        if not text:
            return None

        text = text.strip()
        # Remove markdown code-block fences (```json ... ```)
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
