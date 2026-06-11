from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from testagent.common.logging import get_logger
from testagent.db_ops.errors import SQLGenerationError
from testagent.db_ops.models import SqlOperation, SqlOperationType
from testagent.llm.base import ILLMProvider

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


class SQLGenerator:
    """Generates SQL operations using an LLM based on schema context and intent."""

    def __init__(self, llm: ILLMProvider) -> None:
        self._llm = llm
        self._system_prompt = _load_prompt("sql_generation.txt")

    async def generate(
        self,
        intent: str,
        schema_context: str,
        test_context: str = "",
        iteration: int = 1,
        previous_results: str = "",
    ) -> SqlOperation:
        """Generate a single SQL operation from natural language intent."""
        user_message = self._system_prompt.format(
            schema_context=schema_context,
            test_context=test_context,
            intent=intent,
            iteration=iteration,
            previous_results=previous_results or "无（首次执行）",
        )

        try:
            response = await self._llm.chat(
                system="你是一名数据库测试工程师，负责生成安全的 SQL 语句。",
                messages=[{"role": "user", "content": user_message}],
                max_tokens=1024,
                temperature=0.1,
            )
        except Exception as exc:
            raise SQLGenerationError(
                f"LLM call failed: {exc}",
                code="LLM_CALL_FAILED",
            ) from exc

        content = response.content
        if not content:
            raise SQLGenerationError(
                "LLM returned empty response",
                code="EMPTY_LLM_RESPONSE",
            )

        text = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
        return self._parse_response(text)

    def _parse_response(self, text: str) -> SqlOperation:
        """Parse LLM JSON response into a SqlOperation."""
        # Strip markdown code fences if present
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json or ```) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SQLGenerationError(
                f"LLM returned invalid JSON: {exc}",
                code="INVALID_JSON",
                details={"raw_text": text[:500]},
            ) from exc

        sql = data.get("sql", "").strip()
        op_type = data.get("type", "").upper()
        params = data.get("params", {})
        description = data.get("description", "")

        if not sql:
            raise SQLGenerationError(
                "LLM returned empty SQL",
                code="EMPTY_SQL",
            )

        try:
            op_enum = SqlOperationType(op_type)
        except ValueError:
            raise SQLGenerationError(
                f"Invalid operation type: {op_type}",
                code="INVALID_OP_TYPE",
                details={"type": op_type},
            )

        return SqlOperation(
            type=op_enum,
            sql=sql,
            params=params,
            description=description,
        )
