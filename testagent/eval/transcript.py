from __future__ import annotations

import time
from dataclasses import dataclass, field

from testagent.eval.models import Transcript


@dataclass
class TranscriptSummary:
    """Transcript summary with extraction logic.

    Extends the models.py data structure with an ``extract`` classmethod
    that derives summary fields from a raw message list.
    """

    n_turns: int = 0
    n_tool_calls: int = 0
    total_tokens: int = 0
    total_duration: float = 0.0
    tool_call_sequence: list[str] = field(default_factory=list)
    key_errors: list[str] = field(default_factory=list)
    final_page: str | None = None

    @classmethod
    def extract(cls, messages: list[dict]) -> TranscriptSummary:
        """Derive a TranscriptSummary from a raw message list.

        Counts assistant messages as turns, counts tool_calls within each
        assistant message, records tool-call names in order, and detects
        error strings in tool result content.
        """
        n_turns = 0
        n_tool_calls = 0
        tool_call_sequence: list[str] = []
        key_errors: list[str] = []

        for msg in messages:
            role = msg.get("role", "")

            if role == "assistant":
                n_turns += 1
                calls = msg.get("tool_calls", [])
                for tc in calls:
                    n_tool_calls += 1
                    name = tc.get("function", {}).get("name", "")
                    if name:
                        tool_call_sequence.append(name)

            elif role == "tool":
                content = msg.get("content", "")
                _detect_errors(content, key_errors)

        return cls(
            n_turns=n_turns,
            n_tool_calls=n_tool_calls,
            tool_call_sequence=tool_call_sequence,
            key_errors=key_errors,
        )


def _detect_errors(content: object, key_errors: list[str]) -> None:
    """Detect error strings in tool result content and append to key_errors.

    Handles content as a plain string, a dict with an ``error`` key, or a
    list of content blocks (OpenAI / Anthropic formats).
    """
    if isinstance(content, str):
        if "error" in content.lower():
            key_errors.append(content[:500])
    elif isinstance(content, dict):
        if "error" in content:
            key_errors.append(str(content["error"])[:500])
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if "error" in item:
                    key_errors.append(str(item["error"])[:500])
                text = item.get("text", "")
                if isinstance(text, str) and "error" in text.lower():
                    key_errors.append(text[:500])
            elif isinstance(item, str):
                if "error" in item.lower():
                    key_errors.append(item[:500])


class TranscriptRecorder:
    """Records execution transcript with timing and token-usage tracking.

    Usage::

        with TranscriptRecorder() as recorder:
            recorder.record_message(msg)
            recorder.record_usage(10, 20, 30)
            # ... agent loop calls recorder.on_round(...)

        summary = recorder.summary
        transcript = recorder.transcript
    """

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self._start_time: float | None = None
        self._end_time: float | None = None
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.total_tokens: int = 0

    # ── Timing ─────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Record the start time."""
        self._start_time = time.time()

    def stop(self) -> None:
        """Record the end time."""
        self._end_time = time.time()

    @property
    def duration(self) -> float:
        """Elapsed time between start and stop.

        If still running (stop not yet called), returns time elapsed so far.
        """
        if self._start_time is None:
            return 0.0
        end = self._end_time if self._end_time is not None else time.time()
        return end - self._start_time

    # ── Recording ──────────────────────────────────────────────────────────────

    def record_message(self, msg: dict) -> None:
        """Append a message to the transcript log."""
        self.messages.append(msg)

    def record_usage(
        self, input_tokens: int, output_tokens: int, total_tokens: int
    ) -> None:
        """Accumulate token usage counters."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += total_tokens

    def on_round(
        self, assistant_msg: dict, tool_results: list[dict]
    ) -> None:
        """Callback hook for an agent loop's progress callback.

        agent_loop passes: {"assistant": real_assistant_msg, "tool_calls": [...]}
        Extract the actual assistant message and record it along with tool results.
        """
        # agent_loop wraps the real message: {"assistant": msg, ...}
        real_msg = assistant_msg.get("assistant", assistant_msg)
        self.record_message(real_msg)
        for result in tool_results:
            self.record_message({"role": "tool", "content": str(result)})

    # ── Output ─────────────────────────────────────────────────────────────────

    @property
    def summary(self) -> TranscriptSummary:
        """TranscriptSummary with message-derived fields merged with
        recorded token and timing data."""
        summary = TranscriptSummary.extract(self.messages)
        summary.total_tokens = self.total_tokens
        summary.total_duration = self.duration
        return summary

    @property
    def transcript(self) -> Transcript:
        """Full Transcript object (messages + summary)."""
        return Transcript(messages=self.messages, summary=self.summary)

    # ── Context manager support ────────────────────────────────────────────────

    def __enter__(self) -> TranscriptRecorder:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
