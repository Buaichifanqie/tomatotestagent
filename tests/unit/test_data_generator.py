from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.agent.test_data_generator import (
    TestDataGenerator,
    is_pii_field,
    mask_pii_value,
    sanitize_pii_in_text,
    sanitize_record,
)


@pytest.fixture
def mock_llm() -> AsyncMock:
    llm = AsyncMock()
    llm.chat = AsyncMock()
    llm.embed = AsyncMock()
    llm.embed_batch = AsyncMock()
    return llm


@pytest.fixture
def mock_db_server() -> AsyncMock:
    server = AsyncMock()
    server.call_tool = AsyncMock()
    server.list_tools = AsyncMock()
    server.health_check = AsyncMock(return_value=True)
    return server


@pytest.fixture
def generator(
    mock_llm: AsyncMock,
    mock_db_server: AsyncMock,
) -> TestDataGenerator:
    return TestDataGenerator(llm=mock_llm, db_server=mock_db_server)


@pytest.fixture
def user_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "User full name"},
            "email": {"type": "string", "format": "email"},
            "age": {"type": "integer", "minimum": 18, "maximum": 65},
            "role": {"type": "string", "enum": ["admin", "user", "viewer"]},
        },
        "required": ["name", "email", "age"],
    }


def _make_llm_response(data: list[dict[str, Any]]) -> MagicMock:
    return MagicMock(
        content=[
            {
                "type": "text",
                "text": json.dumps(data, ensure_ascii=False),
            }
        ],
        stop_reason="end_turn",
        usage={},
    )


# =============================================================================
# PII sanitization tests
# =============================================================================


class TestMaskPiiValue:
    def test_email_mask(self) -> None:
        assert mask_pii_value("user@example.com", "email") == "u***@example.com"

    def test_phone_mask(self) -> None:
        assert mask_pii_value("13812345678", "phone") == "138****0000"

    def test_id_card_mask(self) -> None:
        assert mask_pii_value("110101199901011234", "id_card") == "110***********1234"

    def test_bank_card_mask(self) -> None:
        assert mask_pii_value("6222021234567890", "bank_card") == "6222****0000"

    def test_ip_mask(self) -> None:
        assert mask_pii_value("192.168.1.1", "ip_address") == "192.168.*.*"

    def test_unknown_type_defaults_to_stars(self) -> None:
        assert mask_pii_value("anything", "unknown_type") == "***"


class TestSanitizePiiInText:
    def test_masks_email_in_text(self) -> None:
        text = "Contact user@example.com for details"
        result = sanitize_pii_in_text(text)
        assert "user@example.com" not in result
        assert "u***@example.com" in result

    def test_masks_phone_in_text(self) -> None:
        text = "Phone: 13812345678"
        result = sanitize_pii_in_text(text)
        assert "13812345678" not in result
        assert "138****0000" in result

    def test_masks_id_card_in_text(self) -> None:
        text = "ID: 110101199901011234"
        result = sanitize_pii_in_text(text)
        assert "110101199901011234" not in result

    def test_no_pii_returns_unchanged(self) -> None:
        text = "No PII here, just normal text"
        assert sanitize_pii_in_text(text) == text

    def test_multiple_pii_types_in_text(self) -> None:
        text = "Email: test@example.com, Phone: 13912345678"
        result = sanitize_pii_in_text(text)
        assert "test@example.com" not in result
        assert "13912345678" not in result


class TestIsPiiField:
    def test_email_field(self) -> None:
        assert is_pii_field("email") is True

    def test_phone_field(self) -> None:
        assert is_pii_field("phone") is True
        assert is_pii_field("mobile") is True

    def test_password_field(self) -> None:
        assert is_pii_field("password") is True

    def test_name_field(self) -> None:
        assert is_pii_field("name") is True
        assert is_pii_field("first_name") is True

    def test_non_pii_field(self) -> None:
        assert is_pii_field("age") is False
        assert is_pii_field("status") is False
        assert is_pii_field("amount") is False

    def test_case_insensitive(self) -> None:
        assert is_pii_field("Email") is True
        assert is_pii_field("PHONE") is True

    def test_hyphenated_field(self) -> None:
        assert is_pii_field("first-name") is True


class TestSanitizeRecord:
    def test_masks_pii_fields(self) -> None:
        record = {
            "name": "Zhang San",
            "email": "zhang@example.com",
            "age": 30,
            "role": "admin",
        }
        result = sanitize_record(record)
        assert result["name"] == "***masked***"
        assert result["email"] == "***masked***"
        assert result["age"] == 30
        assert result["role"] == "admin"

    def test_masks_pii_in_non_pii_string_values(self) -> None:
        record = {
            "description": "Contact user@example.com for help",
            "status": "active",
        }
        result = sanitize_record(record)
        assert "user@example.com" not in result["description"]
        assert result["status"] == "active"

    def test_non_string_pii_field_masked(self) -> None:
        record = {"phone": 13812345678}
        result = sanitize_record(record)
        assert result["phone"] == "***masked***"

    def test_preserves_non_pii_data(self) -> None:
        record = {"age": 25, "city": "Beijing", "active": True}
        result = sanitize_record(record)
        assert result == {"age": 25, "city": "Beijing", "active": True}


# =============================================================================
# TestDataGenerator — generate tests
# =============================================================================


class TestGenerate:
    @pytest.mark.asyncio
    async def test_generate_basic_data(
        self,
        generator: TestDataGenerator,
        mock_llm: AsyncMock,
        user_schema: dict[str, Any],
    ) -> None:
        raw_data = [
            {"name": "Test User", "email": "test@example.com", "age": 25, "role": "user"},
            {"name": "Admin User", "email": "admin@example.com", "age": 30, "role": "admin"},
        ]
        mock_llm.chat.return_value = _make_llm_response(raw_data)

        results = await generator.generate(schema=user_schema, count=2)

        assert len(results) == 2
        assert results[0]["name"] == "***masked***"
        assert results[0]["email"] == "***masked***"
        assert results[0]["age"] == 25
        assert results[1]["role"] == "admin"

    @pytest.mark.asyncio
    async def test_generate_with_constraints(
        self,
        generator: TestDataGenerator,
        mock_llm: AsyncMock,
        user_schema: dict[str, Any],
    ) -> None:
        constraints = {"role": "admin"}
        raw_data = [
            {"name": "Admin", "email": "admin@test.com", "age": 35, "role": "admin"},
        ]
        mock_llm.chat.return_value = _make_llm_response(raw_data)

        results = await generator.generate(schema=user_schema, constraints=constraints, count=1)

        assert len(results) == 1
        assert results[0]["role"] == "admin"

        call_args = mock_llm.chat.call_args
        user_msg = call_args[1]["messages"][0]["content"]
        assert "admin" in user_msg

    @pytest.mark.asyncio
    async def test_generate_zero_count_returns_empty(
        self,
        generator: TestDataGenerator,
        mock_llm: AsyncMock,
        user_schema: dict[str, Any],
    ) -> None:
        results = await generator.generate(schema=user_schema, count=0)

        assert results == []
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_llm_failure_returns_empty(
        self,
        generator: TestDataGenerator,
        mock_llm: AsyncMock,
        user_schema: dict[str, Any],
    ) -> None:
        mock_llm.chat.side_effect = RuntimeError("LLM timeout")

        results = await generator.generate(schema=user_schema, count=5)

        assert results == []

    @pytest.mark.asyncio
    async def test_generate_invalid_json_response_returns_empty(
        self,
        generator: TestDataGenerator,
        mock_llm: AsyncMock,
        user_schema: dict[str, Any],
    ) -> None:
        mock_llm.chat.return_value = MagicMock(
            content=[{"type": "text", "text": "Not a JSON array"}],
            stop_reason="end_turn",
            usage={},
        )

        results = await generator.generate(schema=user_schema, count=2)

        assert results == []

    @pytest.mark.asyncio
    async def test_generate_pii_sanitized_in_output(
        self,
        generator: TestDataGenerator,
        mock_llm: AsyncMock,
    ) -> None:
        schema = {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
            },
        }
        raw_data = [
            {
                "username": "john_doe",
                "email": "john.doe@real-email.com",
                "phone": "13912345678",
            },
        ]
        mock_llm.chat.return_value = _make_llm_response(raw_data)

        results = await generator.generate(schema=schema, count=1)

        assert results[0]["email"] == "***masked***"
        assert results[0]["phone"] == "***masked***"
        assert "john.doe@real-email.com" not in str(results)


# =============================================================================
# TestDataGenerator — generate_from_api_spec tests
# =============================================================================


class TestGenerateFromApiSpec:
    @pytest.mark.asyncio
    async def test_generate_from_post_endpoint(
        self,
        generator: TestDataGenerator,
        mock_llm: AsyncMock,
    ) -> None:
        api_spec: dict[str, Any] = {
            "paths": {
                "/api/v1/users": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "email": {"type": "string"},
                                        },
                                        "required": ["name", "email"],
                                    }
                                }
                            }
                        },
                    }
                }
            }
        }

        raw_data = [{"name": "Test User", "email": "test@example.com"}]
        mock_llm.chat.return_value = _make_llm_response(raw_data)

        result = await generator.generate_from_api_spec(api_spec)

        assert result["method"] == "post"
        assert result["path"] == "/api/v1/users"
        assert "name" in result["data"]

    @pytest.mark.asyncio
    async def test_generate_from_spec_with_ref(
        self,
        generator: TestDataGenerator,
        mock_llm: AsyncMock,
    ) -> None:
        api_spec: dict[str, Any] = {
            "paths": {
                "/api/v1/orders": {
                    "post": {
                        "requestBody": {
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/OrderRequest"}}}
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "OrderRequest": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "quantity": {"type": "integer", "minimum": 1},
                        },
                    }
                }
            },
        }

        raw_data = [{"product_id": "prod-001", "quantity": 2}]
        mock_llm.chat.return_value = _make_llm_response(raw_data)

        result = await generator.generate_from_api_spec(api_spec)

        assert result["method"] == "post"
        assert result["path"] == "/api/v1/orders"
        assert result["data"]["product_id"] == "prod-001"

    @pytest.mark.asyncio
    async def test_empty_paths_returns_empty(
        self,
        generator: TestDataGenerator,
        mock_llm: AsyncMock,
    ) -> None:
        result = await generator.generate_from_api_spec({})

        assert result == {}
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_to_get_when_no_write_method(
        self,
        generator: TestDataGenerator,
        mock_llm: AsyncMock,
    ) -> None:
        api_spec: dict[str, Any] = {
            "paths": {
                "/api/v1/health": {
                    "get": {
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            }
        }

        result = await generator.generate_from_api_spec(api_spec)

        assert result == {}


# =============================================================================
# TestDataGenerator — seed_to_database tests
# =============================================================================


class TestSeedToDatabase:
    @pytest.mark.asyncio
    async def test_seed_success(
        self,
        generator: TestDataGenerator,
        mock_db_server: AsyncMock,
    ) -> None:
        mock_db_server.call_tool.return_value = json.dumps(
            {
                "success": True,
                "inserted_count": 3,
                "table": "users",
            }
        )

        data = [
            {"name": "User 1", "age": 25},
            {"name": "User 2", "age": 30},
            {"name": "User 3", "age": 35},
        ]

        result = await generator.seed_to_database(
            data=data,
            table="users",
            database_url="sqlite+aiosqlite:///test.db",
        )

        assert result["success"] is True
        assert result["inserted_count"] == 3

        mock_db_server.call_tool.assert_called_once_with(
            "db_seed",
            {
                "database_url": "sqlite+aiosqlite:///test.db",
                "table": "users",
                "data": data,
                "truncate_first": False,
            },
        )

    @pytest.mark.asyncio
    async def test_seed_empty_data_returns_failure(
        self,
        generator: TestDataGenerator,
        mock_db_server: AsyncMock,
    ) -> None:
        result = await generator.seed_to_database(
            data=[],
            table="users",
            database_url="sqlite+aiosqlite:///test.db",
        )

        assert result["success"] is False
        assert result["inserted_count"] == 0
        mock_db_server.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_failure_does_not_raise(
        self,
        generator: TestDataGenerator,
        mock_db_server: AsyncMock,
    ) -> None:
        mock_db_server.call_tool.side_effect = RuntimeError("DB connection failed")

        result = await generator.seed_to_database(
            data=[{"name": "User 1"}],
            table="users",
            database_url="sqlite+aiosqlite:///test.db",
        )

        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_seed_with_truncate(
        self,
        generator: TestDataGenerator,
        mock_db_server: AsyncMock,
    ) -> None:
        mock_db_server.call_tool.return_value = json.dumps(
            {
                "success": True,
                "inserted_count": 1,
                "table": "orders",
            }
        )

        await generator.seed_to_database(
            data=[{"product_id": "p1", "quantity": 1}],
            table="orders",
            database_url="sqlite+aiosqlite:///test.db",
            truncate_first=True,
        )

        call_args = mock_db_server.call_tool.call_args
        assert call_args[0][0] == "db_seed"
        assert call_args[0][1]["truncate_first"] is True


# =============================================================================
# TestDataGenerator — _parse_llm_response tests
# =============================================================================


class TestParseLlmResponse:
    def test_valid_json_array(
        self,
        generator: TestDataGenerator,
    ) -> None:
        response = MagicMock(
            content=[
                {
                    "type": "text",
                    "text": '[{"name": "Alice"}, {"name": "Bob"}]',
                }
            ]
        )
        result = generator._parse_llm_response(response)
        assert len(result) == 2
        assert result[0]["name"] == "Alice"

    def test_json_with_markdown_fences(
        self,
        generator: TestDataGenerator,
    ) -> None:
        response = MagicMock(
            content=[
                {
                    "type": "text",
                    "text": '```json\n[{"name": "Alice"}]\n```',
                }
            ]
        )
        result = generator._parse_llm_response(response)
        assert len(result) == 1

    def test_invalid_text_returns_empty(
        self,
        generator: TestDataGenerator,
    ) -> None:
        response = MagicMock(content=[{"type": "text", "text": "No data available"}])
        result = generator._parse_llm_response(response)
        assert result == []

    def test_filters_non_dict_items(
        self,
        generator: TestDataGenerator,
    ) -> None:
        response = MagicMock(
            content=[
                {
                    "type": "text",
                    "text": '[{"name": "Alice"}, "invalid", 42]',
                }
            ]
        )
        result = generator._parse_llm_response(response)
        assert len(result) == 1
        assert result[0]["name"] == "Alice"

    def test_empty_content_blocks(
        self,
        generator: TestDataGenerator,
    ) -> None:
        response = MagicMock(content=[])
        result = generator._parse_llm_response(response)
        assert result == []


# =============================================================================
# TestDataGenerator — _resolve_ref tests
# =============================================================================


class TestResolveRef:
    def test_valid_ref(
        self,
        generator: TestDataGenerator,
    ) -> None:
        api_spec: dict[str, Any] = {
            "components": {"schemas": {"User": {"type": "object", "properties": {"id": {"type": "string"}}}}}
        }
        result = generator._resolve_ref("#/components/schemas/User", api_spec)
        assert result is not None
        assert result["type"] == "object"

    def test_invalid_ref_prefix(
        self,
        generator: TestDataGenerator,
    ) -> None:
        result = generator._resolve_ref("https://example.com/schema", {})
        assert result is None

    def test_missing_path(
        self,
        generator: TestDataGenerator,
    ) -> None:
        result = generator._resolve_ref("#/components/schemas/Missing", {})
        assert result is None

    def test_non_dict_result(
        self,
        generator: TestDataGenerator,
    ) -> None:
        api_spec: dict[str, Any] = {"components": {"schemas": {"Count": 42}}}
        result = generator._resolve_ref("#/components/schemas/Count", api_spec)
        assert result is None
