from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from testagent.common.errors import TestAgentError
from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from testagent.llm.base import ILLMProvider
    from testagent.mcp_servers.database_server.server import DatabaseMCPServer

logger = get_logger(__name__)

PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("phone", re.compile(r"\b1[3-9]\d{9}\b")),
    ("id_card", re.compile(r"\b\d{17}[\dXx]\b")),
    ("bank_card", re.compile(r"\b\d{16,19}\b")),
    ("ip_address", re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")),
]

PII_FIELD_KEYWORDS: frozenset[str] = frozenset(
    {
        "email",
        "mail",
        "phone",
        "mobile",
        "cell",
        "id_card",
        "idcard",
        "identity",
        "ssn",
        "bank_card",
        "bankcard",
        "credit_card",
        "creditcard",
        "password",
        "passwd",
        "pwd",
        "secret",
        "address",
        "addr",
        "name",
        "fullname",
        "first_name",
        "last_name",
        "birth",
        "birthday",
        "birthdate",
    }
)

MASK_MAP: dict[str, str] = {
    "email": "u***@example.com",
    "phone": "138****0000",
    "id_card": "110***********1234",
    "bank_card": "6222****0000",
    "ip_address": "192.168.*.*",
}


class TestDataGeneratorError(TestAgentError):
    pass


def mask_pii_value(text: str, pii_type: str) -> str:
    return MASK_MAP.get(pii_type, "***")


def _pii_replacer(pii_type: str) -> Callable[[re.Match[str]], str]:
    def _replace(_m: re.Match[str]) -> str:
        return mask_pii_value("", pii_type)

    return _replace


def sanitize_pii_in_text(text: str) -> str:
    result = text
    for pii_type, pattern in PII_PATTERNS:
        result = pattern.sub(_pii_replacer(pii_type), result)
    return result


def is_pii_field(field_name: str) -> bool:
    normalized = field_name.lower().replace("-", "_")
    return normalized in PII_FIELD_KEYWORDS


def sanitize_record(record: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in record.items():
        if is_pii_field(key):
            if isinstance(value, str):
                sanitized[key] = "***masked***"
            else:
                sanitized[key] = "***masked***"
        elif isinstance(value, str):
            sanitized[key] = sanitize_pii_in_text(value)
        else:
            sanitized[key] = value
    return sanitized


class TestDataGenerator:
    __test__ = False

    def __init__(
        self,
        llm: ILLMProvider,
        db_server: DatabaseMCPServer,
    ) -> None:
        self._llm = llm
        self._db_server = db_server

    async def generate(
        self,
        schema: dict[str, Any],
        constraints: dict[str, Any] | None = None,
        count: int = 10,
    ) -> list[dict[str, Any]]:
        if count <= 0:
            return []

        prompt = self._build_generation_prompt(schema, constraints, count)

        try:
            response = await self._llm.chat(
                system=self._system_prompt(),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0.7,
            )

            raw_data = self._parse_llm_response(response)

            sanitized = [sanitize_record(record) for record in raw_data]

            logger.info(
                "Generated %d test data records (%d after sanitization)",
                len(raw_data),
                len(sanitized),
                extra={
                    "extra_data": {
                        "requested_count": count,
                        "generated_count": len(raw_data),
                        "sanitized_count": len(sanitized),
                    }
                },
            )

            return sanitized

        except Exception as exc:
            logger.warning(
                "Test data generation failed",
                extra={"extra_data": {"error": str(exc), "count": count}},
            )
            return []

    async def generate_from_api_spec(self, api_spec: dict[str, Any]) -> dict[str, Any]:
        paths = api_spec.get("paths", {})
        if not paths:
            return {}

        endpoint = self._pick_primary_endpoint(paths)
        if endpoint is None:
            return {}

        method, path, operation = endpoint

        request_body = operation.get("requestBody", {})
        schema_ref = self._resolve_request_schema(request_body, api_spec)
        if not schema_ref:
            return {}

        constraints = self._extract_constraints_from_operation(operation)

        data_list = await self.generate(schema=schema_ref, constraints=constraints, count=1)
        if not data_list:
            return {}

        return {
            "method": method,
            "path": path,
            "data": data_list[0],
        }

    async def seed_to_database(
        self,
        data: list[dict[str, Any]],
        table: str,
        database_url: str,
        truncate_first: bool = False,
    ) -> dict[str, Any]:
        if not data:
            return {"success": False, "inserted_count": 0, "table": table}

        try:
            result = await self._db_server.call_tool(
                "db_seed",
                {
                    "database_url": database_url,
                    "table": table,
                    "data": data,
                    "truncate_first": truncate_first,
                },
            )

            if isinstance(result, str):
                try:
                    parsed: dict[str, Any] = json.loads(result)
                except json.JSONDecodeError:
                    parsed = {"raw": result}
            elif isinstance(result, dict):
                parsed = result
            else:
                parsed = {"raw": str(result)}

            logger.info(
                "Seeded test data to database",
                extra={
                    "extra_data": {
                        "table": table,
                        "row_count": len(data),
                        "result": parsed,
                    }
                },
            )

            return parsed

        except Exception as exc:
            logger.warning(
                "Failed to seed test data to database",
                extra={
                    "extra_data": {
                        "table": table,
                        "row_count": len(data),
                        "error": str(exc),
                    }
                },
            )
            return {"success": False, "error": str(exc), "table": table}

    def _system_prompt(self) -> str:
        return """You are a test data generation expert. Generate realistic, business-compliant
test data based on the provided schema and constraints.

Rules:
1. Generate data that conforms exactly to the field types and constraints specified.
2. Use realistic values that match business semantics (e.g., valid order statuses, real-looking names).
3. Never include real PII data - use fictional values only.
4. For enum fields, only use values from the allowed list.
5. For numeric fields, respect min/max bounds.
6. For date fields, use ISO 8601 format (YYYY-MM-DD).
7. Return a JSON array of objects, one per row.

Respond ONLY with a JSON array. No explanation, no markdown fences."""

    def _build_generation_prompt(
        self,
        schema: dict[str, Any],
        constraints: dict[str, Any] | None,
        count: int,
    ) -> str:
        prompt_parts: list[str] = [
            f"Generate {count} test data records with the following schema:",
            "",
            "Schema:",
            json.dumps(schema, ensure_ascii=False, indent=2),
        ]

        if constraints:
            prompt_parts.extend(
                [
                    "",
                    "Additional constraints:",
                    json.dumps(constraints, ensure_ascii=False, indent=2),
                ]
            )

        prompt_parts.extend(
            [
                "",
                f"Return a JSON array of exactly {count} objects.",
            ]
        )

        return "\n".join(prompt_parts)

    def _parse_llm_response(self, response: Any) -> list[dict[str, Any]]:
        for block in response.content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text", ""))
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return [item for item in parsed if isinstance(item, dict)]
                except (json.JSONDecodeError, ValueError):
                    json_match = re.search(r"\[[\s\S]*\]", text)
                    if json_match:
                        try:
                            parsed = json.loads(json_match.group())
                            if isinstance(parsed, list):
                                return [item for item in parsed if isinstance(item, dict)]
                        except (json.JSONDecodeError, ValueError):
                            pass

                    logger.warning(
                        "Failed to parse LLM response as JSON array",
                        extra={"extra_data": {"text": text[:200]}},
                    )

        return []

    def _pick_primary_endpoint(self, paths: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
        for path, methods in paths.items():
            for method, operation in methods.items():
                method_lower = method.lower()
                if method_lower in ("post", "put", "patch"):
                    return method_lower, path, operation

        for path, methods in paths.items():
            for method, operation in methods.items():
                return method.lower(), path, operation

        return None

    def _resolve_request_schema(
        self,
        request_body: dict[str, Any],
        api_spec: dict[str, Any],
    ) -> dict[str, Any] | None:
        content = request_body.get("content", {})
        for _media_type, media_spec in content.items():
            raw_schema: Any = media_spec.get("schema", {})
            schema = raw_schema if isinstance(raw_schema, dict) else {}
            ref = schema.get("$ref", "")
            if ref:
                return self._resolve_ref(ref, api_spec)
            if schema:
                return schema

        return None

    def _resolve_ref(
        self,
        ref: str,
        api_spec: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not ref.startswith("#/"):
            return None

        parts = ref[2:].split("/")
        current: Any = api_spec
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

        return current if isinstance(current, dict) else None

    def _extract_constraints_from_operation(
        self,
        operation: dict[str, Any],
    ) -> dict[str, Any]:
        constraints: dict[str, Any] = {}

        params = operation.get("parameters", [])
        required_params = [p.get("name") for p in params if p.get("required")]
        if required_params:
            constraints["required_params"] = required_params

        return constraints
