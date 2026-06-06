from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.plan.models import TestCase, TestStep
from testagent.plan.test_case_generator import (
    TC_GENERATION_SYSTEM_PROMPT,
    TestCaseGenerator,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _valid_tc_dict() -> dict:
    return {
        "id": "TC-001",
        "title": "Login with valid credentials",
        "priority": "P1",
        "is_core": True,
        "requirement_ids": ["REQ-001"],
        "steps": [
            {
                "step": 1,
                "action": "click",
                "target": "login_button",
                "value": "",
            },
            {
                "step": 2,
                "action": "input",
                "target": "username_field",
                "value": "testuser",
            },
        ],
    }


def _valid_tc_json() -> str:
    import json

    return json.dumps([_valid_tc_dict()])


# ── tests ────────────────────────────────────────────────────────────────────


class TestInit:
    def test_generator_init(self):
        """Can create a TestCaseGenerator."""
        gen = TestCaseGenerator()
        assert gen._llm_provider is None

        dummy = MagicMock()
        gen2 = TestCaseGenerator(llm_provider=dummy)
        assert gen2._llm_provider is dummy


class TestGenerate:
    def test_generate_from_text(self):
        """When llm_provider is set, _call_llm is used and result is parsed."""
        dummy_provider = MagicMock()
        gen = TestCaseGenerator(llm_provider=dummy_provider)
        sample_json = _valid_tc_json()

        with patch.object(gen, "_call_llm", new_callable=AsyncMock, return_value=sample_json) as mock_call:
            result = asyncio.run(
                gen.generate("some PRD text", plan_name="Test Plan")
            )

        mock_call.assert_called_once_with("some PRD text")
        assert len(result) == 1
        tc = result[0]
        assert isinstance(tc, TestCase)
        assert tc.id == "TC-001"
        assert tc.title == "Login with valid credentials"
        assert tc.priority == "P1"
        assert tc.is_core is True
        assert tc.requirement_ids == ["REQ-001"]
        assert len(tc.steps) == 2
        assert all(isinstance(s, TestStep) for s in tc.steps)

    def test_generate_no_provider_parses_directly(self):
        """When llm_provider is None, prd_text is parsed directly as JSON."""
        gen = TestCaseGenerator()
        result = asyncio.run(
            gen.generate(_valid_tc_json())
        )
        assert len(result) == 1
        assert result[0].id == "TC-001"


class TestParseResponse:
    def test_parse_response_valid_json(self):
        """A raw JSON list is parsed into a list of TestCase objects."""
        gen = TestCaseGenerator()
        raw = _valid_tc_json()
        result = gen._parse_response(raw)
        assert len(result) == 1
        assert isinstance(result[0], TestCase)
        assert result[0].id == "TC-001"

    def test_parse_response_invalid(self):
        """Invalid JSON returns an empty list."""
        gen = TestCaseGenerator()
        result = gen._parse_response("not valid json at all")
        assert result == []


class TestExtractJson:
    def test_extract_json_markdown_fenced(self):
        """Extracts JSON array from ```json ... ``` markdown blocks."""
        gen = TestCaseGenerator()
        raw = "Some text\n```json\n[{\"id\": \"TC-1\", \"title\": \"Test\"}]\n```\nmore"
        result = gen._extract_json(raw)
        import json

        assert json.loads(result) == [{"id": "TC-1", "title": "Test"}]

    def test_extract_json_bare(self):
        """Extracts from a raw JSON string (no markdown fences)."""
        gen = TestCaseGenerator()
        raw = '[{"id": "TC-1", "title": "Test"}]'
        result = gen._extract_json(raw)
        import json

        assert json.loads(result) == [{"id": "TC-1", "title": "Test"}]

    def test_extract_json_no_json(self):
        """Returns empty string for text with no JSON content."""
        gen = TestCaseGenerator()
        result = gen._extract_json("Just some random text without JSON.")
        assert result == ""


class TestDictToTc:
    def test_dict_to_tc(self):
        """A full dict is converted to a TestCase with nested TestSteps."""
        gen = TestCaseGenerator()
        data = _valid_tc_dict()
        tc = gen._dict_to_tc(data)
        assert isinstance(tc, TestCase)
        assert tc.id == "TC-001"
        assert tc.title == "Login with valid credentials"
        assert tc.priority == "P1"
        assert tc.is_core is True
        assert tc.requirement_ids == ["REQ-001"]
        assert len(tc.steps) == 2
        step1 = tc.steps[0]
        assert isinstance(step1, TestStep)
        assert step1.step == 1
        assert step1.action == "click"
        assert step1.target == "login_button"
        step2 = tc.steps[1]
        assert step2.step == 2
        assert step2.action == "input"
        assert step2.target == "username_field"
        assert step2.value == "testuser"

    def test_dict_to_tc_minimal(self):
        """A minimal dict uses defaults for optional fields."""
        gen = TestCaseGenerator()
        data = {"id": "TC-099", "title": "Minimal"}
        tc = gen._dict_to_tc(data)
        assert isinstance(tc, TestCase)
        assert tc.id == "TC-099"
        assert tc.title == "Minimal"
        assert tc.priority == "P1"
        assert tc.is_core is False
        assert tc.requirement_ids == []
        assert tc.steps == []


class TestSystemPrompt:
    def test_system_prompt_defined(self):
        """TC_GENERATION_SYSTEM_PROMPT is a non-empty string."""
        assert isinstance(TC_GENERATION_SYSTEM_PROMPT, str)
        assert len(TC_GENERATION_SYSTEM_PROMPT) > 0
