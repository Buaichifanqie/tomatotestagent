from __future__ import annotations

from typing import Any

from testagent.eval.models import EvalTask, GraderResult, Transcript
from testagent.eval.graders.base import BaseGrader


class StateCheckGrader(BaseGrader):
    """Grader that checks UI state after execution.

    Compares the final page and visible elements against expectations
    defined in ``config.expect``.
    """

    async def grade(
        self, transcript: Transcript, task: EvalTask, **kwargs
    ) -> GraderResult:
        """Grade based on expected state after task execution.

        Expect keys
        -----------
        - ``current_page`` (str): expected final page identifier.
        - ``elements_present`` (list[str]): element names that must appear
          in tool result text.
        """
        expect = self.config.expect
        if not expect:
            return GraderResult(
                grader_type="state_check", score=1.0, passed=True,
                details="No expectations configured — auto pass.",
            )

        # ── Check current_page ──────────────────────────────────────────────
        expected_page = expect.get("current_page")
        if expected_page:
            actual_page = (
                transcript.summary.final_page if transcript.summary else ""
            )
            if actual_page != expected_page:
                return GraderResult(
                    grader_type="state_check",
                    score=0.0,
                    passed=False,
                    details=(
                        f"Expected current_page='{expected_page}', "
                        f"got '{actual_page}'"
                    ),
                )

        # ── Check elements_present ──────────────────────────────────────────
        expected_elements: list[str] = expect.get("elements_present", [])
        if expected_elements:
            missing = self._find_elements_in_transcript(
                transcript.messages, expected_elements
            )
            if missing:
                return GraderResult(
                    grader_type="state_check",
                    score=0.0,
                    passed=False,
                    details=f"Missing expected elements in transcript: {missing}",
                )

        return GraderResult(
            grader_type="state_check",
            score=1.0,
            passed=True,
            details="All expectations met.",
        )

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _find_elements_in_transcript(
        messages: list[dict[str, Any]], expected: list[str]
    ) -> list[str]:
        """Return a list of expected element names *not* found in tool results.

        Performs a case-insensitive substring search over the text content
        of every ``role == "tool"`` message.
        """
        if not messages or not expected:
            return list(expected)

        # Collect all tool-result text content
        tool_texts: list[str] = []
        for msg in messages:
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                tool_texts.append(content)
            elif isinstance(content, dict):
                # Flatten dict values into text
                tool_texts.append(" ".join(str(v) for v in content.values()))
            elif isinstance(content, list):
                tool_texts.extend(str(item) for item in content)

        joined = " ".join(tool_texts).lower()

        missing: list[str] = []
        for name in expected:
            if name.lower() not in joined:
                missing.append(name)
        return missing
