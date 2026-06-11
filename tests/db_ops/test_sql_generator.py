"""Tests for testagent.db_ops.sql_generator — SQLGenerator."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.db_ops.errors import SQLGenerationError
from testagent.db_ops.models import SqlOperationType
from testagent.db_ops.sql_generator import SQLGenerator


def _make_mock_llm(response_text: str) -> AsyncMock:
    """Create a mock LLM provider that returns the given text."""
    llm = AsyncMock()
    response = MagicMock()
    response.content = [{"text": response_text}]
    llm.chat.return_value = response
    return llm


def _make_prompt_patch():
    """Patch _load_prompt so tests don't need the actual prompt files."""
    return patch(
        "testagent.db_ops.sql_generator._load_prompt",
        return_value="Schema: {schema_context}\nIntent: {intent}\nTest: {test_context}\nIteration: {iteration}\nPrev: {previous_results}",
    )


class TestSQLGeneratorParseResponse:
    """Test the _parse_response method which converts LLM JSON to SqlOperation."""

    def test_parse_valid_select(self):
        llm = _make_mock_llm("")
        with _make_prompt_patch():
            gen = SQLGenerator(llm)

        json_str = json.dumps({
            "type": "SELECT",
            "sql": "SELECT * FROM users WHERE id = :id",
            "params": {"id": 1},
            "description": "Get user by ID",
        })
        op = gen._parse_response(json_str)
        assert op.type == SqlOperationType.SELECT
        assert "SELECT * FROM users" in op.sql
        assert op.params == {"id": 1}
        assert op.description == "Get user by ID"

    def test_parse_valid_insert(self):
        llm = _make_mock_llm("")
        with _make_prompt_patch():
            gen = SQLGenerator(llm)

        json_str = json.dumps({
            "type": "INSERT",
            "sql": "INSERT INTO users (name) VALUES (:name)",
            "params": {"name": "test"},
        })
        op = gen._parse_response(json_str)
        assert op.type == SqlOperationType.INSERT

    def test_parse_with_markdown_fence(self):
        llm = _make_mock_llm("")
        with _make_prompt_patch():
            gen = SQLGenerator(llm)

        fenced = '```json\n{"type": "SELECT", "sql": "SELECT 1"}\n```'
        op = gen._parse_response(fenced)
        assert op.type == SqlOperationType.SELECT
        assert op.sql == "SELECT 1"

    def test_parse_with_plain_fence(self):
        llm = _make_mock_llm("")
        with _make_prompt_patch():
            gen = SQLGenerator(llm)

        fenced = '```\n{"type": "UPDATE", "sql": "UPDATE t SET x=1"}\n```'
        op = gen._parse_response(fenced)
        assert op.type == SqlOperationType.UPDATE

    def test_parse_invalid_json_raises(self):
        llm = _make_mock_llm("")
        with _make_prompt_patch():
            gen = SQLGenerator(llm)

        with pytest.raises(SQLGenerationError) as exc_info:
            gen._parse_response("not json at all")
        assert "INVALID_JSON" in str(exc_info.value)

    def test_parse_empty_sql_raises(self):
        llm = _make_mock_llm("")
        with _make_prompt_patch():
            gen = SQLGenerator(llm)

        json_str = json.dumps({"type": "SELECT", "sql": ""})
        with pytest.raises(SQLGenerationError) as exc_info:
            gen._parse_response(json_str)
        assert "EMPTY_SQL" in str(exc_info.value)

    def test_parse_invalid_op_type_raises(self):
        llm = _make_mock_llm("")
        with _make_prompt_patch():
            gen = SQLGenerator(llm)

        json_str = json.dumps({"type": "DELETE", "sql": "DELETE FROM t"})
        with pytest.raises(SQLGenerationError) as exc_info:
            gen._parse_response(json_str)
        assert "INVALID_OP_TYPE" in str(exc_info.value)

    def test_parse_case_insensitive_type(self):
        llm = _make_mock_llm("")
        with _make_prompt_patch():
            gen = SQLGenerator(llm)

        json_str = json.dumps({"type": "select", "sql": "SELECT 1"})
        op = gen._parse_response(json_str)
        assert op.type == SqlOperationType.SELECT


class TestSQLGeneratorGenerate:
    """Test the async generate method."""

    @pytest.mark.asyncio
    async def test_generate_success(self):
        response_json = json.dumps({
            "type": "SELECT",
            "sql": "SELECT * FROM users",
            "params": {},
            "description": "List users",
        })
        llm = _make_mock_llm(response_json)
        with _make_prompt_patch():
            gen = SQLGenerator(llm)
            op = await gen.generate(
                intent="list all users",
                schema_context="CREATE TABLE users (id INT, name VARCHAR);",
            )

        assert op.type == SqlOperationType.SELECT
        assert "users" in op.sql
        llm.chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_llm_failure_raises(self):
        llm = AsyncMock()
        llm.chat.side_effect = RuntimeError("API error")
        with _make_prompt_patch():
            gen = SQLGenerator(llm)
            with pytest.raises(SQLGenerationError) as exc_info:
                await gen.generate(intent="test", schema_context="test")
        assert "LLM_CALL_FAILED" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_empty_response_raises(self):
        llm = AsyncMock()
        response = MagicMock()
        response.content = []
        llm.chat.return_value = response
        with _make_prompt_patch():
            gen = SQLGenerator(llm)
            with pytest.raises(SQLGenerationError) as exc_info:
                await gen.generate(intent="test", schema_context="test")
        assert "EMPTY_LLM_RESPONSE" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_passes_iteration_and_previous_results(self):
        response_json = json.dumps({"type": "SELECT", "sql": "SELECT 1"})
        llm = _make_mock_llm(response_json)
        with _make_prompt_patch():
            gen = SQLGenerator(llm)
            await gen.generate(
                intent="test",
                schema_context="schema",
                test_context="ctx",
                iteration=3,
                previous_results="prev failure",
            )

        # Verify the LLM was called with formatted prompt
        call_args = llm.chat.call_args
        messages = call_args.kwargs.get("messages", call_args[1].get("messages", []))
        user_msg = messages[0]["content"]
        assert "3" in user_msg  # iteration
        assert "prev failure" in user_msg

    @pytest.mark.asyncio
    async def test_generate_default_previous_results(self):
        response_json = json.dumps({"type": "SELECT", "sql": "SELECT 1"})
        llm = _make_mock_llm(response_json)
        with _make_prompt_patch():
            gen = SQLGenerator(llm)
            await gen.generate(intent="test", schema_context="schema")

        call_args = llm.chat.call_args
        messages = call_args.kwargs.get("messages", call_args[1].get("messages", []))
        user_msg = messages[0]["content"]
        # When no previous_results, the template should show the default
        assert "首次执行" in user_msg or "test" in user_msg
