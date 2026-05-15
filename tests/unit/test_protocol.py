from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from pydantic import ValidationError

from testagent.agent.protocol import (
    ACKNOWLEDGMENT_TIMEOUT_SECONDS,
    SENDER_RECEIVER_PATTERN,
    VALID_MESSAGE_TYPES,
    AckPayload,
    AgentMessage,
    ErrorPayload,
    MessageStore,
    NotificationPayload,
    QueryPayload,
    ResultReportPayload,
    TaskAssignmentPayload,
)


def _valid_message(**overrides: object) -> dict[str, object]:
    return {
        "message_type": "task_assignment",
        "sender": "planner",
        "receiver": "executor_1",
        "session_id": str(uuid.uuid4()),
        "payload": {
            "task_id": "t-001",
            "task_type": "api_test",
            "config": {"url": "/health"},
        },
        **overrides,
    }


class TestMessagePayloads:
    def test_task_assignment_payload_defaults(self) -> None:
        p = TaskAssignmentPayload(task_id="t-001", task_type="api_test")
        assert p.type == "task_assignment"
        assert p.config == {}
        assert p.skill_ref is None

    def test_task_assignment_payload_full(self) -> None:
        p = TaskAssignmentPayload(
            task_id="t-001",
            task_type="web_test",
            config={"url": "https://example.com"},
            skill_ref="web_smoke_test",
        )
        assert p.skill_ref == "web_smoke_test"
        assert p.config["url"] == "https://example.com"

    def test_result_report_payload(self) -> None:
        p = ResultReportPayload(
            task_id="t-001",
            status="passed",
            results={"assertions": {"total": 5, "passed": 5}},
            logs="All checks passed",
            duration_ms=123.45,
        )
        assert p.type == "result_report"
        assert p.status == "passed"
        assert p.duration_ms == 123.45

    def test_result_report_payload_defaults(self) -> None:
        p = ResultReportPayload(task_id="t-001", status="failed")
        assert p.results is None
        assert p.logs is None
        assert p.duration_ms is None

    def test_query_payload(self) -> None:
        p = QueryPayload(query_type="get_defect_history", parameters={"limit": 10})
        assert p.type == "query"
        assert p.parameters["limit"] == 10

    def test_query_payload_defaults(self) -> None:
        p = QueryPayload(query_type="ping")
        assert p.parameters == {}

    def test_notification_payload(self) -> None:
        p = NotificationPayload(
            event="session_completed",
            data={"session_id": "s-001", "status": "passed"},
        )
        assert p.type == "notification"
        assert p.data["status"] == "passed"

    def test_notification_payload_defaults(self) -> None:
        p = NotificationPayload(event="ping")
        assert p.data is None

    def test_ack_payload(self) -> None:
        p = AckPayload(acked_message_id="msg-001")
        assert p.type == "ack"
        assert p.acked_message_id == "msg-001"
        assert p.status == "received"

    def test_ack_payload_custom_status(self) -> None:
        p = AckPayload(acked_message_id="msg-001", status="processed")
        assert p.status == "processed"

    def test_error_payload(self) -> None:
        p = ErrorPayload(
            error_code="TIMEOUT",
            message="Request timed out",
            details={"timeout_seconds": 30},
        )
        assert p.type == "error"
        assert p.details["timeout_seconds"] == 30

    def test_error_payload_defaults(self) -> None:
        p = ErrorPayload(error_code="UNKNOWN", message="Something went wrong")
        assert p.details is None


class TestAgentMessageCreation:
    def test_create_task_assignment_message(self) -> None:
        msg = AgentMessage(
            message_type="task_assignment",
            sender="planner",
            receiver="executor_1",
            session_id="session-001",
            payload=TaskAssignmentPayload(task_id="t-001", task_type="api_test"),
        )
        assert msg.message_type == "task_assignment"
        assert uuid.UUID(msg.message_id, version=4)
        assert msg.sender == "planner"
        assert msg.receiver == "executor_1"
        assert msg.session_id == "session-001"
        assert isinstance(msg.payload, TaskAssignmentPayload)
        assert msg.in_reply_to is None
        assert isinstance(msg.timestamp, datetime)

    def test_create_result_report_message(self) -> None:
        msg = AgentMessage(
            message_type="result_report",
            sender="executor_1",
            receiver="analyzer",
            session_id="session-001",
            payload=ResultReportPayload(task_id="t-001", status="passed"),
        )
        assert msg.message_type == "result_report"
        assert msg.sender == "executor_1"
        assert msg.receiver == "analyzer"

    def test_create_query_message(self) -> None:
        msg = AgentMessage(
            message_type="query",
            sender="analyzer",
            receiver="gateway",
            session_id="session-001",
            payload=QueryPayload(query_type="get_session_status"),
        )
        assert msg.message_type == "query"

    def test_create_notification_message(self) -> None:
        msg = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="session-001",
            payload=NotificationPayload(event="session_started"),
        )
        assert msg.message_type == "notification"

    def test_create_ack_message(self) -> None:
        original_id = str(uuid.uuid4())
        msg = AgentMessage(
            message_type="ack",
            sender="executor_1",
            receiver="planner",
            session_id="session-001",
            payload=AckPayload(acked_message_id=original_id),
        )
        assert msg.message_type == "ack"
        assert msg.in_reply_to == original_id

    def test_create_error_message(self) -> None:
        msg = AgentMessage(
            message_type="error",
            sender="executor_1",
            receiver="gateway",
            session_id="session-001",
            payload=ErrorPayload(error_code="TIMEOUT", message="Request timed out"),
        )
        assert msg.message_type == "error"

    def test_default_message_id_is_uuid4(self) -> None:
        msg1 = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        msg2 = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        assert msg1.message_id != msg2.message_id
        uuid.UUID(msg1.message_id, version=4)
        uuid.UUID(msg2.message_id, version=4)

    def test_explicit_message_id(self) -> None:
        msg = AgentMessage(
            message_id="00000000-0000-0000-0000-000000000001",
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        assert msg.message_id == "00000000-0000-0000-0000-000000000001"

    def test_in_reply_to_explicit(self) -> None:
        original_id = str(uuid.uuid4())
        msg = AgentMessage(
            message_type="result_report",
            sender="executor_1",
            receiver="analyzer",
            session_id="s-001",
            payload=ResultReportPayload(task_id="t-001", status="passed"),
            in_reply_to=original_id,
        )
        assert msg.in_reply_to == original_id

    def test_timestamp_auto_set(self) -> None:
        before = datetime.now(UTC)
        msg = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        after = datetime.now(UTC)
        assert before <= msg.timestamp <= after


class TestSenderReceiverValidation:
    VALID_SENDERS: ClassVar[list[str]] = [
        "planner",
        "executor_1",
        "executor_10",
        "analyzer",
        "gateway",
        "cli",
        "broadcast",
    ]

    @pytest.mark.parametrize("sender", VALID_SENDERS)
    def test_valid_senders(self, sender: str) -> None:
        msg = AgentMessage(
            message_type="notification",
            sender=sender,
            receiver="gateway",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        assert msg.sender == sender

    @pytest.mark.parametrize("receiver", VALID_SENDERS)
    def test_valid_receivers(self, receiver: str) -> None:
        msg = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver=receiver,
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        assert msg.receiver == receiver

    INVALID_SENDERS: ClassVar[list[str]] = [
        "",
        "planner_1",
        "Executor_1",
        "EXECUTOR_1",
        "executor_",
        "executor_abc",
        "planner-1",
        "manager",
        "worker",
        "unknown",
    ]

    @pytest.mark.parametrize("sender", INVALID_SENDERS)
    def test_invalid_senders(self, sender: str) -> None:
        with pytest.raises(ValidationError):
            AgentMessage(
                message_type="notification",
                sender=sender,
                receiver="gateway",
                session_id="s-001",
                payload=NotificationPayload(event="test"),
            )

    @pytest.mark.parametrize("receiver", INVALID_SENDERS)
    def test_invalid_receivers(self, receiver: str) -> None:
        with pytest.raises(ValidationError):
            AgentMessage(
                message_type="notification",
                sender="gateway",
                receiver=receiver,
                session_id="s-001",
                payload=NotificationPayload(event="test"),
            )

    def test_regex_pattern_matches_requirements(self) -> None:
        assert re.match(SENDER_RECEIVER_PATTERN, "planner")
        assert re.match(SENDER_RECEIVER_PATTERN, "executor_1")
        assert re.match(SENDER_RECEIVER_PATTERN, "executor_99")
        assert re.match(SENDER_RECEIVER_PATTERN, "analyzer")
        assert re.match(SENDER_RECEIVER_PATTERN, "gateway")
        assert re.match(SENDER_RECEIVER_PATTERN, "cli")
        assert re.match(SENDER_RECEIVER_PATTERN, "broadcast")
        assert not re.match(SENDER_RECEIVER_PATTERN, "planner_1")
        assert not re.match(SENDER_RECEIVER_PATTERN, "executor_abc")
        assert not re.match(SENDER_RECEIVER_PATTERN, "")


class TestMessageTypeValidation:
    @pytest.mark.parametrize("msg_type", sorted(VALID_MESSAGE_TYPES))
    def test_valid_message_types(self, msg_type: str) -> None:
        payload_map: dict[str, object] = {
            "task_assignment": TaskAssignmentPayload(task_id="t-001", task_type="api_test"),
            "result_report": ResultReportPayload(task_id="t-001", status="passed"),
            "query": QueryPayload(query_type="ping"),
            "notification": NotificationPayload(event="ping"),
            "ack": AckPayload(acked_message_id="msg-001"),
            "error": ErrorPayload(error_code="ERR", message="error"),
        }
        msg = AgentMessage(
            message_type=msg_type,
            sender="planner",
            receiver="executor_1",
            session_id="s-001",
            payload=payload_map[msg_type],
        )
        assert msg.message_type == msg_type

    INVALID_MESSAGE_TYPES: ClassVar[list[str]] = [
        "",
        "assign",
        "result",
        "acknowledgment",
        "task",
        "report",
        "query_message",
        "notify",
        "err",
        "task_assignment ",
        "task-assignment",
    ]

    @pytest.mark.parametrize("msg_type", INVALID_MESSAGE_TYPES)
    def test_invalid_message_types(self, msg_type: str) -> None:
        with pytest.raises(ValidationError):
            AgentMessage(
                message_type=msg_type,
                sender="planner",
                receiver="executor_1",
                session_id="s-001",
                payload=TaskAssignmentPayload(task_id="t-001", task_type="api_test"),
            )

    def test_payload_type_mismatch_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            AgentMessage(
                message_type="task_assignment",
                sender="planner",
                receiver="executor_1",
                session_id="s-001",
                payload=ResultReportPayload(task_id="t-001", status="passed"),
            )

    def test_payload_type_consistency_with_query(self) -> None:
        with pytest.raises(ValidationError):
            AgentMessage(
                message_type="query",
                sender="analyzer",
                receiver="gateway",
                session_id="s-001",
                payload=NotificationPayload(event="test"),
            )


class TestSessionStateTransition:
    def test_task_assignment_valid_in_planning(self) -> None:
        msg = AgentMessage(
            message_type="task_assignment",
            sender="planner",
            receiver="executor_1",
            session_id="s-001",
            payload=TaskAssignmentPayload(task_id="t-001", task_type="api_test"),
        )
        assert msg.is_valid_transition("planning") is True

    def test_task_assignment_invalid_in_executing(self) -> None:
        msg = AgentMessage(
            message_type="task_assignment",
            sender="planner",
            receiver="executor_1",
            session_id="s-001",
            payload=TaskAssignmentPayload(task_id="t-001", task_type="api_test"),
        )
        assert msg.is_valid_transition("executing") is False

    def test_task_assignment_invalid_in_analyzing(self) -> None:
        msg = AgentMessage(
            message_type="task_assignment",
            sender="planner",
            receiver="executor_1",
            session_id="s-001",
            payload=TaskAssignmentPayload(task_id="t-001", task_type="api_test"),
        )
        assert msg.is_valid_transition("analyzing") is False

    def test_result_report_valid_in_executing(self) -> None:
        msg = AgentMessage(
            message_type="result_report",
            sender="executor_1",
            receiver="analyzer",
            session_id="s-001",
            payload=ResultReportPayload(task_id="t-001", status="passed"),
        )
        assert msg.is_valid_transition("executing") is True

    def test_result_report_invalid_in_planning(self) -> None:
        msg = AgentMessage(
            message_type="result_report",
            sender="executor_1",
            receiver="analyzer",
            session_id="s-001",
            payload=ResultReportPayload(task_id="t-001", status="passed"),
        )
        assert msg.is_valid_transition("planning") is False

    def test_notification_valid_in_analyzing(self) -> None:
        msg = AgentMessage(
            message_type="notification",
            sender="analyzer",
            receiver="gateway",
            session_id="s-001",
            payload=NotificationPayload(event="analysis_complete"),
        )
        assert msg.is_valid_transition("analyzing") is True

    def test_notification_invalid_in_executing(self) -> None:
        msg = AgentMessage(
            message_type="notification",
            sender="analyzer",
            receiver="gateway",
            session_id="s-001",
            payload=NotificationPayload(event="analysis_complete"),
        )
        assert msg.is_valid_transition("executing") is False

    def test_query_valid_in_analyzing(self) -> None:
        msg = AgentMessage(
            message_type="query",
            sender="analyzer",
            receiver="gateway",
            session_id="s-001",
            payload=QueryPayload(query_type="get_defect_history"),
        )
        assert msg.is_valid_transition("analyzing") is True

    def test_query_invalid_in_planning(self) -> None:
        msg = AgentMessage(
            message_type="query",
            sender="analyzer",
            receiver="gateway",
            session_id="s-001",
            payload=QueryPayload(query_type="get_defect_history"),
        )
        assert msg.is_valid_transition("planning") is False

    def test_ack_always_valid(self) -> None:
        msg = AgentMessage(
            message_type="ack",
            sender="executor_1",
            receiver="planner",
            session_id="s-001",
            payload=AckPayload(acked_message_id="msg-001"),
        )
        for state in ("planning", "executing", "analyzing", "completed", "failed"):
            assert msg.is_valid_transition(state) is True, f"ack should be valid in {state}"

    def test_error_always_valid(self) -> None:
        msg = AgentMessage(
            message_type="error",
            sender="executor_1",
            receiver="gateway",
            session_id="s-001",
            payload=ErrorPayload(error_code="ERR", message="error"),
        )
        for state in ("planning", "executing", "analyzing", "completed", "failed"):
            assert msg.is_valid_transition(state) is True, f"error should be valid in {state}"

    def test_invalid_state_returns_false(self) -> None:
        msg = AgentMessage(
            message_type="notification",
            sender="analyzer",
            receiver="gateway",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        assert msg.is_valid_transition("unknown_state") is False

    def test_no_messages_valid_in_terminal_states(self) -> None:
        notification = AgentMessage(
            message_type="notification",
            sender="analyzer",
            receiver="gateway",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        task_assignment = AgentMessage(
            message_type="task_assignment",
            sender="planner",
            receiver="executor_1",
            session_id="s-001",
            payload=TaskAssignmentPayload(task_id="t-001", task_type="api_test"),
        )
        result_report = AgentMessage(
            message_type="result_report",
            sender="executor_1",
            receiver="analyzer",
            session_id="s-001",
            payload=ResultReportPayload(task_id="t-001", status="passed"),
        )
        query = AgentMessage(
            message_type="query",
            sender="analyzer",
            receiver="gateway",
            session_id="s-001",
            payload=QueryPayload(query_type="ping"),
        )
        for msg in (notification, task_assignment, result_report, query):
            assert msg.is_valid_transition("completed") is False
            assert msg.is_valid_transition("failed") is False


class TestMessageStore:
    @pytest.fixture()
    async def store(self) -> MessageStore:
        s = MessageStore(":memory:")
        yield s
        await s.close()

    async def test_append_and_is_duplicate(self, store: MessageStore) -> None:
        msg = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        assert await store.is_duplicate(msg.message_id) is False
        await store.append(msg)
        assert await store.is_duplicate(msg.message_id) is True

    async def test_append_same_message_id_twice(self, store: MessageStore) -> None:
        msg = AgentMessage(
            message_id="fixed-id-0000-0000-0000-000000000001",
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        await store.append(msg)
        await store.append(msg)
        assert await store.is_duplicate(msg.message_id) is True

    async def test_ack_and_duplicate(self, store: MessageStore) -> None:
        msg = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        await store.append(msg)
        await store.ack(msg.message_id)
        assert await store.is_duplicate(msg.message_id) is True

    async def test_get_unacked_returns_timed_out_messages(self, store: MessageStore) -> None:
        old_ts = (datetime.now(UTC) - timedelta(seconds=ACKNOWLEDGMENT_TIMEOUT_SECONDS + 10)).isoformat()
        msg = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
            timestamp=datetime.fromisoformat(old_ts),
        )
        await store.append(msg)
        unacked = await store.get_unacked(receiver="planner", timeout_seconds=ACKNOWLEDGMENT_TIMEOUT_SECONDS)
        assert any(m.message_id == msg.message_id for m in unacked)

    async def test_get_unacked_excludes_recent_messages(self, store: MessageStore) -> None:
        msg = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
        )
        await store.append(msg)
        unacked = await store.get_unacked(receiver="planner", timeout_seconds=ACKNOWLEDGMENT_TIMEOUT_SECONDS)
        assert all(m.message_id != msg.message_id for m in unacked)

    async def test_get_unacked_excludes_acked_messages(self, store: MessageStore) -> None:
        old_ts = (datetime.now(UTC) - timedelta(seconds=ACKNOWLEDGMENT_TIMEOUT_SECONDS + 10)).isoformat()
        msg = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
            timestamp=datetime.fromisoformat(old_ts),
        )
        await store.append(msg)
        await store.ack(msg.message_id)
        unacked = await store.get_unacked(receiver="planner", timeout_seconds=ACKNOWLEDGMENT_TIMEOUT_SECONDS)
        assert all(m.message_id != msg.message_id for m in unacked)

    async def test_get_unacked_filters_by_receiver(self, store: MessageStore) -> None:
        old_ts = (datetime.now(UTC) - timedelta(seconds=ACKNOWLEDGMENT_TIMEOUT_SECONDS + 10)).isoformat()
        msg_planner = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver="planner",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
            timestamp=datetime.fromisoformat(old_ts),
        )
        msg_analyzer = AgentMessage(
            message_type="notification",
            sender="gateway",
            receiver="analyzer",
            session_id="s-001",
            payload=NotificationPayload(event="test"),
            timestamp=datetime.fromisoformat(old_ts),
        )
        await store.append(msg_planner)
        await store.append(msg_analyzer)

        planner_unacked = await store.get_unacked(receiver="planner", timeout_seconds=1)
        analyzer_unacked = await store.get_unacked(receiver="analyzer", timeout_seconds=1)

        assert any(m.message_id == msg_planner.message_id for m in planner_unacked)
        assert all(m.message_id != msg_analyzer.message_id for m in planner_unacked)
        assert any(m.message_id == msg_analyzer.message_id for m in analyzer_unacked)
        assert all(m.message_id != msg_planner.message_id for m in analyzer_unacked)

    async def test_round_trip_all_payload_types(self, store: MessageStore) -> None:
        payloads = [
            TaskAssignmentPayload(task_id="t-001", task_type="api_test"),
            ResultReportPayload(task_id="t-001", status="passed"),
            QueryPayload(query_type="ping"),
            NotificationPayload(event="test"),
            AckPayload(acked_message_id="msg-001"),
            ErrorPayload(error_code="ERR", message="error"),
        ]
        types = [
            "task_assignment",
            "result_report",
            "query",
            "notification",
            "ack",
            "error",
        ]
        for msg_type, payload in zip(types, payloads, strict=True):
            msg = AgentMessage(
                message_type=msg_type,
                sender="gateway",
                receiver="planner",
                session_id="s-001",
                payload=payload,
            )
            await store.append(msg)
            assert await store.is_duplicate(msg.message_id) is True

    async def test_concurrent_appends(self, store: MessageStore) -> None:
        import asyncio

        async def append_msg(i: int) -> str:
            msg = AgentMessage(
                message_type="notification",
                sender="gateway",
                receiver="planner",
                session_id="s-001",
                payload=NotificationPayload(event=f"concurrent-{i}"),
            )
            await store.append(msg)
            return msg.message_id

        ids = await asyncio.gather(*[append_msg(i) for i in range(10)])
        assert len(set(ids)) == 10
        for mid in ids:
            assert await store.is_duplicate(mid) is True


class TestAckTimeoutConstants:
    def test_acknowledgment_timeout_seconds(self) -> None:
        assert ACKNOWLEDGMENT_TIMEOUT_SECONDS == 30
