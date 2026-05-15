from __future__ import annotations

import json
import logging
from typing import ClassVar

import pytest

from testagent.common.errors import (
    AgentContextOverflowError,
    AgentError,
    AgentTimeoutError,
    ConfigError,
    DatabaseError,
    HarnessError,
    LLMError,
    LLMRateLimitError,
    LLMTokenLimitError,
    MCPConnectionError,
    MCPError,
    MCPServerUnavailableError,
    MCPToolError,
    RAGDegradedError,
    RAGError,
    RAGIngestionError,
    RAGSearchError,
    SandboxError,
    SandboxResourceError,
    SandboxTimeoutError,
    SkillDegradedError,
    SkillError,
    SkillParseError,
    SkillValidationError,
    TestAgentError,
)
from testagent.common.logging import (
    StructuredFormatter,
    get_logger,
    mask_api_key,
    mask_pii,
)


class TestExceptionHierarchy:
    _ALL_EXCEPTION_CLASSES: ClassVar[list[type[TestAgentError]]] = [
        ConfigError,
        DatabaseError,
        AgentError,
        AgentTimeoutError,
        AgentContextOverflowError,
        MCPError,
        MCPConnectionError,
        MCPToolError,
        MCPServerUnavailableError,
        RAGError,
        RAGIngestionError,
        RAGSearchError,
        RAGDegradedError,
        HarnessError,
        SandboxError,
        SandboxTimeoutError,
        SandboxResourceError,
        SkillError,
        SkillParseError,
        SkillValidationError,
        SkillDegradedError,
        LLMError,
        LLMRateLimitError,
        LLMTokenLimitError,
    ]

    @pytest.mark.parametrize("exc_cls", _ALL_EXCEPTION_CLASSES, ids=lambda c: c.__name__)
    def test_all_exceptions_inherit_from_base(self, exc_cls: type[TestAgentError]) -> None:
        assert issubclass(exc_cls, TestAgentError)

    def test_agent_subhierarchy(self) -> None:
        assert issubclass(AgentTimeoutError, AgentError)
        assert issubclass(AgentContextOverflowError, AgentError)
        assert issubclass(AgentTimeoutError, TestAgentError)
        assert issubclass(AgentContextOverflowError, TestAgentError)

    def test_mcp_subhierarchy(self) -> None:
        assert issubclass(MCPConnectionError, MCPError)
        assert issubclass(MCPToolError, MCPError)
        assert issubclass(MCPServerUnavailableError, MCPError)
        assert issubclass(MCPConnectionError, TestAgentError)

    def test_rag_subhierarchy(self) -> None:
        assert issubclass(RAGIngestionError, RAGError)
        assert issubclass(RAGSearchError, RAGError)
        assert issubclass(RAGDegradedError, RAGError)
        assert issubclass(RAGIngestionError, TestAgentError)

    def test_harness_subhierarchy(self) -> None:
        assert issubclass(SandboxError, HarnessError)
        assert issubclass(SandboxTimeoutError, SandboxError)
        assert issubclass(SandboxResourceError, SandboxError)
        assert issubclass(SandboxTimeoutError, TestAgentError)

    def test_skill_subhierarchy(self) -> None:
        assert issubclass(SkillParseError, SkillError)
        assert issubclass(SkillValidationError, SkillError)
        assert issubclass(SkillDegradedError, SkillError)
        assert issubclass(SkillParseError, TestAgentError)

    def test_llm_subhierarchy(self) -> None:
        assert issubclass(LLMRateLimitError, LLMError)
        assert issubclass(LLMTokenLimitError, LLMError)
        assert issubclass(LLMRateLimitError, TestAgentError)


class TestExceptionAttributes:
    def test_base_error_default_code(self) -> None:
        err = TestAgentError("something went wrong")
        assert err.code == "UNKNOWN"
        assert err.message == "something went wrong"
        assert err.details == {}

    def test_base_error_custom_code(self) -> None:
        err = TestAgentError("fail", code="E001")
        assert err.code == "E001"

    def test_base_error_with_details(self) -> None:
        details = {"key": "value", "count": 42}
        err = TestAgentError("fail", code="E002", details=details)
        assert err.details == details

    def test_base_error_details_defaults_to_empty(self) -> None:
        err = TestAgentError("fail")
        assert err.details == {}

    def test_str_without_details(self) -> None:
        err = TestAgentError("oops", code="E001")
        assert str(err) == "[E001] oops"

    def test_str_with_details(self) -> None:
        err = TestAgentError("oops", code="E001", details={"k": "v"})
        assert str(err) == "[E001] oops details={'k': 'v'}"

    def test_repr(self) -> None:
        err = TestAgentError("msg", code="C01", details={"a": 1})
        r = repr(err)
        assert "TestAgentError" in r
        assert "msg" in r
        assert "C01" in r

    def test_subclass_preserves_attributes(self) -> None:
        err = RAGDegradedError("vector down", code="RAG_DEGRADED", details={"fallback": "bm25"})
        assert err.message == "vector down"
        assert err.code == "RAG_DEGRADED"
        assert err.details == {"fallback": "bm25"}
        assert isinstance(err, RAGError)
        assert isinstance(err, TestAgentError)

    def test_exception_can_be_caught_as_base(self) -> None:
        with pytest.raises(TestAgentError):
            raise LLMRateLimitError("rate limited", code="LLM_429")

    def test_exception_can_be_caught_as_parent(self) -> None:
        with pytest.raises(AgentError):
            raise AgentTimeoutError("timeout", code="AGENT_TIMEOUT")


class TestMaskPII:
    def test_mask_phone_number(self) -> None:
        result = mask_pii("user phone is 13812345678")
        assert "13812345678" not in result
        assert "[手机号已脱敏]" in result

    def test_mask_id_card(self) -> None:
        result = mask_pii("id: 110101199003076534")
        assert "110101199003076534" not in result
        assert "[身份证已脱敏]" in result

    def test_mask_email(self) -> None:
        result = mask_pii("contact: user@example.com")
        assert "user@example.com" not in result
        assert "[邮箱已脱敏]" in result

    def test_mask_multiple_pii(self) -> None:
        text = "phone 13900001111 email test@test.com"
        result = mask_pii(text)
        assert "13900001111" not in result
        assert "test@test.com" not in result
        assert "[手机号已脱敏]" in result
        assert "[邮箱已脱敏]" in result

    def test_no_pii_unchanged(self) -> None:
        text = "hello world no pii here"
        assert mask_pii(text) == text

    def test_mask_id_card_with_x(self) -> None:
        result = mask_pii("id: 11010119900307653X")
        assert "11010119900307653X" not in result
        assert "[身份证已脱敏]" in result


class TestMaskAPIKey:
    def test_mask_sk_prefix(self) -> None:
        result = mask_api_key("key: sk-abcdefghijklmnop")
        assert "sk-abcdefghijklmnop" not in result
        assert "sk-abcd***" in result

    def test_mask_sk_live_prefix(self) -> None:
        result = mask_api_key("key: sk_live_abcdefghijklmnop")
        assert "sk_live_abcdefghijklmnop" not in result
        assert "sk_live_abcd***" in result

    def test_mask_key_prefix(self) -> None:
        result = mask_api_key("key-1234567890abcdef")
        assert "key-1234567890abcdef" not in result
        assert "key-1234***" in result

    def test_mask_token_assignment(self) -> None:
        result = mask_api_key("token=abcdef1234567890")
        assert "token=abcdef1234567890" not in result
        assert "token=abcd***" in result

    def test_short_key_not_matched(self) -> None:
        result = mask_api_key("sk-1234")
        assert result == "sk-1234"

    def test_min_length_key_masked(self) -> None:
        result = mask_api_key("sk-12345678")
        assert "sk-12345678" not in result
        assert "sk-1234***" in result

    def test_no_key_unchanged(self) -> None:
        text = "normal log message without keys"
        assert mask_api_key(text) == text


class TestStructuredFormatter:
    def test_format_basic_record(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test.logger"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed

    def test_format_record_with_pii(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="user 13812345678 called API with sk-test123456789",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        msg = parsed["message"]
        assert "13812345678" not in msg
        assert "sk-test123456789" not in msg
        assert "[手机号已脱敏]" in msg

    def test_format_record_with_exception(self) -> None:
        formatter = StructuredFormatter()
        try:
            raise ValueError("inner error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="something failed",
            args=None,
            exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "inner error" in parsed["exception"]

    def test_format_record_with_extra_data(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="with extra",
            args=None,
            exc_info=None,
        )
        record.extra_data = {"session_id": "abc123", "env": "staging"}  # type: ignore[attr-defined]
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "extra" in parsed
        assert parsed["extra"]["session_id"] == "abc123"
        assert parsed["extra"]["env"] == "staging"

    def test_format_record_extra_data_with_pii(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="with extra pii",
            args=None,
            exc_info=None,
        )
        record.extra_data = {"user_info": "phone 13900001111"}  # type: ignore[attr-defined]
        output = formatter.format(record)
        parsed = json.loads(output)
        extra_msg = parsed["extra"]["user_info"]
        assert "13900001111" not in extra_msg
        assert "[手机号已脱敏]" in extra_msg

    def test_output_is_valid_json(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="test.py",
            lineno=1,
            msg="json test 中文内容",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "中文内容" in parsed["message"]


class TestGetLogger:
    def test_returns_logger(self) -> None:
        logger = get_logger("test.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"

    def test_logger_has_handler(self) -> None:
        logger = get_logger("test.handler_check")
        assert len(logger.handlers) >= 1

    def test_logger_handler_uses_structured_formatter(self) -> None:
        logger = get_logger("test.formatter_check")
        handler = logger.handlers[0]
        assert isinstance(handler.formatter, StructuredFormatter)

    def test_logger_idempotent(self) -> None:
        name = "test.idempotent_check"
        logger1 = get_logger(name)
        handler_count_before = len(logger1.handlers)
        logger2 = get_logger(name)
        assert logger1 is logger2
        assert len(logger2.handlers) == handler_count_before
