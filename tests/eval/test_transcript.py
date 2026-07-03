from __future__ import annotations

import time

from testagent.eval.models import Transcript
from testagent.eval.transcript import TranscriptRecorder, TranscriptSummary


# ── TranscriptSummary.extract ──────────────────────────────────────────────────


class TestTranscriptSummaryExtract:
    """Test TranscriptSummary.extract classmethod."""

    def test_summary_empty_messages(self) -> None:
        """Empty message list yields all-zeros / empty collections."""
        summary = TranscriptSummary.extract([])
        assert summary.n_turns == 0
        assert summary.n_tool_calls == 0
        assert summary.total_tokens == 0
        assert summary.total_duration == 0.0
        assert summary.tool_call_sequence == []
        assert summary.key_errors == []
        assert summary.final_page is None

    def test_summary_basic(self) -> None:
        """Two assistant rounds (one with tool_calls, one without)."""
        messages = [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "Let me search.",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "search", "arguments": "{}"}},
                    {"id": "c2", "function": {"name": "read_page", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_use_id": "c1", "content": "results"},
            {"role": "tool", "tool_use_id": "c2", "content": "page content"},
            {"role": "assistant", "content": "Here is what I found."},
        ]
        summary = TranscriptSummary.extract(messages)
        assert summary.n_turns == 2
        assert summary.n_tool_calls == 2
        assert summary.tool_call_sequence == ["search", "read_page"]
        assert summary.key_errors == []

    def test_summary_no_tool_calls(self) -> None:
        """Assistant messages without tool_calls do not inflate n_tool_calls."""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "what's up"},
            {"role": "assistant", "content": "All good."},
        ]
        summary = TranscriptSummary.extract(messages)
        assert summary.n_turns == 2
        assert summary.n_tool_calls == 0
        assert summary.tool_call_sequence == []

    def test_summary_detects_errors_in_text(self) -> None:
        """Tool result content containing 'error' in plain text."""
        messages = [
            {"role": "user", "content": "do it"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "click", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_use_id": "c1", "content": "Error: element not found"},
        ]
        summary = TranscriptSummary.extract(messages)
        assert len(summary.key_errors) >= 1
        assert "error" in summary.key_errors[0].lower()

    def test_summary_detects_errors_in_json(self) -> None:
        """Tool result content with an 'error' key in a dict."""
        messages = [
            {"role": "user", "content": "do it"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "fetch", "arguments": "{}"}},
                ],
            },
            {
                "role": "tool",
                "tool_use_id": "c1",
                "content": {"error": "API timeout after 30s"},
            },
        ]
        summary = TranscriptSummary.extract(messages)
        assert len(summary.key_errors) >= 1
        assert "API timeout" in summary.key_errors[0]

    def test_summary_multiple_errors(self) -> None:
        """Multiple tool errors are all collected."""
        messages = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "step_1", "arguments": "{}"}},
                    {"id": "c2", "function": {"name": "step_2", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_use_id": "c1", "content": "first error happened"},
            {"role": "tool", "tool_use_id": "c2", "content": {"error": "second failure"}},
        ]
        summary = TranscriptSummary.extract(messages)
        assert len(summary.key_errors) == 2


# ── TranscriptRecorder ────────────────────────────────────────────────────────


class TestTranscriptRecorder:
    """Test TranscriptRecorder class."""

    def test_recorder_context_manager(self) -> None:
        """Context-manager usage: start, record, stop => duration > 0."""
        with TranscriptRecorder() as recorder:
            recorder.record_message({"role": "user", "content": "hi"})
            time.sleep(0.01)

        assert recorder.duration > 0
        assert len(recorder.messages) == 1

    def test_recorder_manual_start_stop(self) -> None:
        """Manual start/stop also works."""
        recorder = TranscriptRecorder()
        recorder.start()
        time.sleep(0.01)
        recorder.stop()
        assert recorder.duration > 0

    def test_recorder_duration_without_start(self) -> None:
        """Duration is 0 when start was never called."""
        recorder = TranscriptRecorder()
        assert recorder.duration == 0.0

    def test_recorder_usage_tracking(self) -> None:
        """Calling record_usage twice accumulates totals."""
        recorder = TranscriptRecorder()
        recorder.record_usage(100, 50, 150)
        recorder.record_usage(200, 100, 300)
        assert recorder.input_tokens == 300
        assert recorder.output_tokens == 150
        assert recorder.total_tokens == 450

    def test_recorder_on_round(self) -> None:
        """on_round records the assistant message and all tool results."""
        recorder = TranscriptRecorder()
        assistant_msg = {
            "role": "assistant",
            "content": "ok",
            "tool_calls": [],
        }
        tool_results = [
            {"role": "tool", "tool_use_id": "c1", "content": "result_a"},
            {"role": "tool", "tool_use_id": "c2", "content": "result_b"},
        ]
        recorder.on_round(assistant_msg, tool_results)
        assert len(recorder.messages) == 3

    def test_recorder_summary_merges_tokens_and_timing(self) -> None:
        """Summary property merges extracted data with recorded tokens/timing."""
        messages = [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "Let me check.",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "search", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_use_id": "c1", "content": "results"},
        ]
        recorder = TranscriptRecorder()
        for msg in messages:
            recorder.record_message(msg)
        recorder.total_tokens = 500
        # Simulate a known time window
        recorder._start_time = 100.0
        recorder._end_time = 105.0

        summary = recorder.summary
        assert summary.n_turns == 1
        assert summary.n_tool_calls == 1
        assert summary.total_tokens == 500
        assert summary.total_duration == 5.0

    def test_recorder_transcript_property(self) -> None:
        """Transcript property returns a models.Transcript with correct data."""
        recorder = TranscriptRecorder()
        recorder.record_message({"role": "user", "content": "hello"})
        t = recorder.transcript
        assert isinstance(t, Transcript)
        assert len(t.messages) == 1
        assert t.summary is not None
        assert t.summary.n_turns == 0  # no assistant messages

    def test_recorder_multiple_on_round_calls(self) -> None:
        """Multiple on_round calls accumulate messages correctly."""
        recorder = TranscriptRecorder()
        recorder.on_round(
            {"role": "assistant", "content": "a1", "tool_calls": [{"id": "c1", "function": {"name": "search", "arguments": "{}"}}]},
            [{"role": "tool", "tool_use_id": "c1", "content": "res1"}],
        )
        recorder.on_round(
            {"role": "assistant", "content": "a2", "tool_calls": []},
            [],
        )
        assert len(recorder.messages) == 3
        summary = recorder.summary
        assert summary.n_turns == 2
        assert summary.n_tool_calls == 1
