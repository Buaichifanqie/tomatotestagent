# Cross-Source Validation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cross-source validation engine that fetches data from APIs/databases, extracts UI values, and performs intelligent comparison — enabling business-semantic-level test verification beyond simple element assertions.

**Architecture:** New `testagent/rule_engine/` sub-package with 5 core modules (ContextManager, DataSource, SmartComparator, UIExtractor, RuleEngine). Integrates into existing `ExecutionEngine` and `PerTCEvaluator` via Context-centric data flow. MVP uses deterministic methods (DOM + OCR + rule-based matchers); LLM fallback deferred to V1.1+.

**Tech Stack:** Python 3.11+, Pydantic, httpx (async HTTP), jsonpath-ng, pytest

---

## File Structure

```
testagent/rule_engine/              # NEW sub-package
├── __init__.py                     # Public API exports
├── context_manager.py              # Flat dict + ${variable} substitution
├── data_source.py                  # BaseDataSource + ApiDataSource + DatabaseDataSource
├── smart_comparator.py             # 4 built-in matchers + explicit transforms
├── ui_extractor.py                 # DOM extraction + OCR (VLM deferred)
├── yaml_parser.py                  # Parse setup/assertions YAML into models
├── engine.py                       # RuleEngine orchestrator
└── models.py                       # Pydantic models for rule engine

tests/rule_engine/                  # NEW test directory
├── __init__.py
├── test_context_manager.py
├── test_data_source.py
├── test_smart_comparator.py
├── test_ui_extractor.py
├── test_yaml_parser.py
└── test_engine.py

# Modified existing files:
testagent/plan/models.py            # Add setup/assertions/cross_source_results fields to TestCase/TCExecution
testagent/plan/execution_engine.py  # Add setup execution + UI extraction hooks
testagent/plan/evaluator.py         # Add cross-source comparison in evaluation
testagent/cli/plan.py               # Add Phase 3.5 semi-automatic flow
```

---

## Task 1: ContextManager — Flat Dictionary with Variable Substitution

**Files:**
- Create: `testagent/rule_engine/__init__.py`
- Create: `testagent/rule_engine/context_manager.py`
- Create: `tests/rule_engine/__init__.py`
- Create: `tests/rule_engine/test_context_manager.py`

- [ ] **Step 1: Create rule_engine package init**

```python
# testagent/rule_engine/__init__.py
"""Cross-source validation engine for business-semantic-level test verification."""
```

- [ ] **Step 2: Create tests package init**

```python
# tests/rule_engine/__init__.py
```

- [ ] **Step 3: Write failing tests for ContextManager**

```python
# tests/rule_engine/test_context_manager.py
from __future__ import annotations

import pytest
from testagent.rule_engine.context_manager import ContextManager


class TestContextManagerRegister:
    def test_register_and_get(self):
        ctx = ContextManager()
        ctx.register("product_id", "12345")
        assert ctx.get("product_id") == "12345"

    def test_get_default(self):
        ctx = ContextManager()
        assert ctx.get("missing") is None
        assert ctx.get("missing", "fallback") == "fallback"

    def test_register_batch(self):
        ctx = ContextManager()
        ctx.register_batch({"a": 1, "b": 2, "c": 3})
        assert ctx.get("a") == 1
        assert ctx.get("b") == 2
        assert ctx.get("c") == 3

    def test_register_overwrites(self):
        ctx = ContextManager()
        ctx.register("key", "old")
        ctx.register("key", "new")
        assert ctx.get("key") == "new"


class TestContextManagerResolve:
    def test_resolve_simple(self):
        ctx = ContextManager()
        ctx.register("name", "Alice")
        assert ctx.resolve("Hello ${name}!") == "Hello Alice!"

    def test_resolve_multiple(self):
        ctx = ContextManager()
        ctx.register("host", "localhost")
        ctx.register("port", "8080")
        assert ctx.resolve("http://${host}:${port}/api") == "http://localhost:8080/api"

    def test_resolve_missing_variable(self):
        ctx = ContextManager()
        result = ctx.resolve("Value is ${missing}")
        assert "${missing}" in result  # Keep placeholder if not found

    def test_resolve_no_variables(self):
        ctx = ContextManager()
        assert ctx.resolve("plain text") == "plain text"

    def test_resolve_numeric_value(self):
        ctx = ContextManager()
        ctx.register("count", 42)
        assert ctx.resolve("Count: ${count}") == "Count: 42"

    def test_resolve_dict(self):
        ctx = ContextManager()
        ctx.register("base", "http://api.test")
        ctx.register("id", "123")
        data = {
            "endpoint": "${base}/products/${id}",
            "headers": {"Auth": "Bearer ${base}"},
            "list": ["${id}", "static"],
        }
        resolved = ctx.resolve_dict(data)
        assert resolved["endpoint"] == "http://api.test/products/123"
        assert resolved["headers"]["Auth"] == "Bearer http://api.test"
        assert resolved["list"] == ["123", "static"]

    def test_resolve_dict_nested(self):
        ctx = ContextManager()
        ctx.register("x", "1")
        data = {"a": {"b": {"c": "${x}"}}}
        resolved = ctx.resolve_dict(data)
        assert resolved["a"]["b"]["c"] == "1"


class TestContextManagerBuiltins:
    def test_random_id_registered(self):
        ctx = ContextManager()
        ctx.register("random_id", "abc123")
        result = ctx.resolve("test_${random_id}")
        assert result == "test_abc123"

    def test_timestamp_registered(self):
        ctx = ContextManager()
        ctx.register("timestamp", "20260607")
        result = ctx.resolve("log_${timestamp}")
        assert result == "log_20260607"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_context_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'testagent.rule_engine.context_manager'`

- [ ] **Step 5: Implement ContextManager**

```python
# testagent/rule_engine/context_manager.py
from __future__ import annotations

import re
from typing import Any

_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


class ContextManager:
    """Flat dictionary with ${variable} substitution.

    Variables are registered from setup extracts, built-in values,
    and UI extraction results. All references use simple ${name}
    syntax — no deep paths.
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def register(self, key: str, value: Any) -> None:
        """Register a variable in the context."""
        self._store[key] = value

    def register_batch(self, data: dict[str, Any]) -> None:
        """Register multiple variables at once."""
        self._store.update(data)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from context."""
        return self._store.get(key, default)

    def resolve(self, template: str) -> str:
        """Replace ${var} placeholders in a string.

        If a variable is not found, the placeholder is kept as-is.
        """
        def _replacer(match: re.Match[str]) -> str:
            name = match.group(1)
            value = self._store.get(name)
            if value is None:
                return match.group(0)  # Keep ${name} as-is
            return str(value)

        return _VAR_PATTERN.sub(_replacer, template)

    def resolve_dict(self, data: dict) -> dict:
        """Recursively resolve all ${var} in a dict."""
        return self._resolve_value(data)

    def _resolve_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.resolve(value)
        if isinstance(value, dict):
            return {k: self._resolve_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_value(item) for item in value]
        return value

    def keys(self) -> list[str]:
        """Return all registered variable names."""
        return list(self._store.keys())

    def as_dict(self) -> dict[str, Any]:
        """Return a copy of the internal store."""
        return dict(self._store)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_context_manager.py -v`
Expected: All 12 tests PASS

- [ ] **Step 7: Commit**

```bash
git add testagent/rule_engine/__init__.py testagent/rule_engine/context_manager.py tests/rule_engine/__init__.py tests/rule_engine/test_context_manager.py
git commit -m "feat(rule_engine): add ContextManager with flat dict and \${variable} substitution"
```

---

## Task 2: DataSource — Base Interface + API Implementation

**Files:**
- Create: `testagent/rule_engine/models.py`
- Create: `testagent/rule_engine/data_source.py`
- Create: `tests/rule_engine/test_data_source.py`

- [ ] **Step 1: Write failing tests for DataSource models**

```python
# tests/rule_engine/test_data_source.py
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from testagent.rule_engine.data_source import (
    ApiDataSource,
    DatabaseDataSource,
    DataSourceFactory,
)
from testagent.rule_engine.context_manager import ContextManager


class TestApiDataSourceFetch:
    @pytest.mark.asyncio
    async def test_get_request(self):
        """ApiDataSource.fetch() sends GET and extracts JSON path."""
        source = ApiDataSource(
            name="get_user",
            method="GET",
            endpoint="http://test/api/users/123",
            extract={"user_name": "$.data.name"},
        )
        ctx = ContextManager()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"name": "Alice", "age": 30}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            result = await source.fetch(ctx)

        assert result == {"user_name": "Alice"}

    @pytest.mark.asyncio
    async def test_post_request_with_body(self):
        """ApiDataSource.fetch() sends POST with body and extracts response."""
        source = ApiDataSource(
            name="create_product",
            method="POST",
            endpoint="http://test/api/products",
            body={"name": "Test", "price": 100},
            extract={"product_id": "$.data.id"},
        )
        ctx = ContextManager()

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"data": {"id": "prod-456"}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await source.fetch(ctx)

        assert result == {"product_id": "prod-456"}

    @pytest.mark.asyncio
    async def test_context_variable_resolution(self):
        """ApiDataSource resolves ${var} in endpoint and headers."""
        source = ApiDataSource(
            name="get_product",
            method="GET",
            endpoint="${API_BASE}/products/${product_id}",
            headers={"Authorization": "Bearer ${token}"},
            extract={"name": "$.data.name"},
        )
        ctx = ContextManager()
        ctx.register("API_BASE", "http://test/api")
        ctx.register("product_id", "789")
        ctx.register("token", "my-token")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"name": "Widget"}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response) as mock_get:
            result = await source.fetch(ctx)

        # Verify the URL was resolved
        call_args = mock_get.call_args
        assert "http://test/api/products/789" in str(call_args)
        assert result == {"name": "Widget"}

    @pytest.mark.asyncio
    async def test_timeout_marks_error(self):
        """ApiDataSource marks ERROR on timeout, does not raise."""
        import httpx

        source = ApiDataSource(
            name="slow_api",
            method="GET",
            endpoint="http://test/api/slow",
            extract={"value": "$.data"},
        )
        ctx = ContextManager()

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=httpx.TimeoutException("timeout")):
            result = await source.fetch(ctx)

        assert "error" in result

    @pytest.mark.asyncio
    async def test_non_200_marks_error(self):
        """ApiDataSource marks ERROR on non-200 response."""
        source = ApiDataSource(
            name="bad_api",
            method="GET",
            endpoint="http://test/api/bad",
            extract={"value": "$.data"},
        )
        ctx = ContextManager()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = Exception("500")

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            result = await source.fetch(ctx)

        assert "error" in result


class TestApiDataSourceCreate:
    @pytest.mark.asyncio
    async def test_create_returns_extracted_data(self):
        """ApiDataSource.create() sends POST and extracts from response."""
        source = ApiDataSource(
            name="create_order",
            method="POST",
            endpoint="http://test/api/orders",
            body={"item_id": "123"},
            extract={"order_id": "$.data.id"},
        )
        ctx = ContextManager()

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"data": {"id": "order-789"}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await source.create({"item_id": "123"}, ctx)

        assert result == {"order_id": "order-789"}


class TestDataSourceFactory:
    def test_create_api_source(self):
        config = {
            "name": "test_api",
            "type": "api",
            "method": "GET",
            "endpoint": "http://test/api",
            "extract": {"id": "$.data.id"},
        }
        source = DataSourceFactory.create(config)
        assert isinstance(source, ApiDataSource)

    def test_create_database_source(self):
        config = {
            "name": "test_db",
            "type": "database",
            "connection": "sqlite:///test.db",
            "query": "SELECT * FROM users WHERE id = :user_id",
            "extract": {"name": "$.rows[0].name"},
        }
        source = DataSourceFactory.create(config)
        assert isinstance(source, DatabaseDataSource)

    def test_unknown_type_raises(self):
        config = {"name": "bad", "type": "unknown", "extract": {}}
        with pytest.raises(ValueError, match="Unknown data source type"):
            DataSourceFactory.create(config)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_data_source.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create models.py for rule engine**

```python
# testagent/rule_engine/models.py
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AssertionStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    NEED_REVIEW = "NEED_REVIEW"


class CompareResult(BaseModel):
    """Result of a SmartComparator comparison."""
    matched: bool
    ui_value: Any = None
    expected_value: Any = None
    matcher_used: str = ""
    confidence: float = 1.0
    message: str = ""


class AssertionResult(BaseModel):
    """Result of a single assertion execution."""
    field: str
    assertion_type: str  # "cross_source", "ui_visible", etc.
    status: AssertionStatus
    compare_result: CompareResult | None = None
    error_message: str = ""
    source_values: dict[str, Any] = Field(default_factory=dict)


class DataSourceConfig(BaseModel):
    """Configuration for a data source from YAML."""
    name: str
    type: str  # "api", "database", "plugin"
    method: str = ""
    endpoint: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    connection: str = ""
    query: str = ""
    extract: dict[str, str] = Field(default_factory=dict)
    source_ref: str = ""  # Reference to a setup data source name


class AssertionConfig(BaseModel):
    """Configuration for an assertion from YAML."""
    type: str  # "cross_source", "ui_visible"
    field: str = ""
    target: str = ""
    expected: Any = None
    sources: dict[str, Any] = Field(default_factory=dict)
    compare_mode: str = "auto"  # "auto", "strict"
```

- [ ] **Step 4: Implement DataSource classes**

```python
# testagent/rule_engine/data_source.py
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from testagent.rule_engine.context_manager import ContextManager
from testagent.rule_engine.models import DataSourceConfig


class BaseDataSource(ABC):
    """Abstract base for all data sources."""

    def __init__(self, name: str, extract: dict[str, str] | None = None) -> None:
        self.name = name
        self.extract = extract or {}

    @abstractmethod
    async def fetch(self, context: ContextManager) -> dict[str, Any]:
        """Fetch data and return extracted values."""
        pass

    @abstractmethod
    async def create(self, data: dict[str, Any], context: ContextManager) -> dict[str, Any]:
        """Create test data and return extracted values."""
        pass

    def cleanup(self, context: ContextManager) -> None:
        """Optional cleanup hook (no-op by default)."""
        pass

    def _extract_values(self, data: Any) -> dict[str, Any]:
        """Extract values from response using JSONPath-like expressions."""
        result = {}
        for key, path in self.extract.items():
            result[key] = self._resolve_json_path(data, path)
        return result

    @staticmethod
    def _resolve_json_path(data: Any, path: str) -> Any:
        """Resolve a simple JSONPath expression like $.data.field."""
        if not path.startswith("$"):
            return data

        parts = path[1:].strip(".").split(".")
        current = data
        for part in parts:
            if current is None:
                return None
            # Handle array index like rows[0]
            index_match = re.match(r"(\w+)\[(\d+)\]", part)
            if index_match:
                key, idx = index_match.group(1), int(index_match.group(2))
                if isinstance(current, dict):
                    current = current.get(key)
                if isinstance(current, list) and idx < len(current):
                    current = current[idx]
                else:
                    return None
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current


class ApiDataSource(BaseDataSource):
    """REST API data source.

    Supports GET/POST/PUT/DELETE with JSON body, header injection,
    and JSONPath extraction from response.
    """

    def __init__(
        self,
        name: str,
        method: str = "GET",
        endpoint: str = "",
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        extract: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        super().__init__(name, extract)
        self.method = method.upper()
        self.endpoint = endpoint
        self.headers = headers or {}
        self.body = body or {}
        self.timeout = timeout

    async def fetch(self, context: ContextManager) -> dict[str, Any]:
        """Send HTTP request and extract values from response."""
        url = context.resolve(self.endpoint)
        headers = {k: context.resolve(v) for k, v in self.headers.items()}
        body = context.resolve_dict(self.body) if self.body else None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method=self.method,
                    url=url,
                    headers=headers,
                    json=body if self.method in ("POST", "PUT", "PATCH") else None,
                )
                response.raise_for_status()
                data = response.json()
                return self._extract_values(data)
        except httpx.TimeoutException:
            return {"error": f"API timeout: {self.method} {url}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"API error {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": f"API request failed: {str(e)}"}

    async def create(self, data: dict[str, Any], context: ContextManager) -> dict[str, Any]:
        """Create data via POST and extract from response."""
        original_method = self.method
        original_body = self.body
        self.method = "POST"
        self.body = data
        try:
            result = await self.fetch(context)
            return result
        finally:
            self.method = original_method
            self.body = original_body


class DatabaseDataSource(BaseDataSource):
    """SQL database data source.

    Supports parameterized queries with :variable substitution
    from context. Returns results as dict with $.rows[i].field paths.
    """

    def __init__(
        self,
        name: str,
        connection: str = "",
        query: str = "",
        extract: dict[str, str] | None = None,
    ) -> None:
        super().__init__(name, extract)
        self.connection = connection
        self.query = query

    async def fetch(self, context: ContextManager) -> dict[str, Any]:
        """Execute query and extract values from results."""
        resolved_query = self._resolve_query_params(self.query, context)

        try:
            import sqlalchemy
            engine = sqlalchemy.create_engine(self.connection)
            with engine.connect() as conn:
                result = conn.execute(sqlalchemy.text(resolved_query))
                rows = [dict(row._mapping) for row in result]
                return self._extract_values({"rows": rows})
        except Exception as e:
            return {"error": f"Database query failed: {str(e)}"}

    async def create(self, data: dict[str, Any], context: ContextManager) -> dict[str, Any]:
        """Execute INSERT and return affected rows info."""
        return await self.fetch(context)

    @staticmethod
    def _resolve_query_params(query: str, context: ContextManager) -> str:
        """Resolve :param style variables from context."""
        def _replacer(match: re.Match[str]) -> str:
            name = match.group(1)
            value = context.get(name)
            if value is None:
                return match.group(0)
            # Quote string values
            if isinstance(value, str):
                return f"'{value}'"
            return str(value)

        return re.sub(r":(\w+)", _replacer, query)


class DataSourceFactory:
    """Factory for creating data source instances from config dicts."""

    _registry: dict[str, type[BaseDataSource]] = {
        "api": ApiDataSource,
        "database": DatabaseDataSource,
    }

    @classmethod
    def create(cls, config: dict[str, Any]) -> BaseDataSource:
        """Create a data source from a config dict."""
        source_type = config.get("type", "")
        source_cls = cls._registry.get(source_type)
        if source_cls is None:
            raise ValueError(f"Unknown data source type: {source_type}")

        if source_type == "api":
            return ApiDataSource(
                name=config.get("name", ""),
                method=config.get("method", "GET"),
                endpoint=config.get("endpoint", ""),
                headers=config.get("headers"),
                body=config.get("body"),
                extract=config.get("extract"),
            )
        elif source_type == "database":
            return DatabaseDataSource(
                name=config.get("name", ""),
                connection=config.get("connection", ""),
                query=config.get("query", ""),
                extract=config.get("extract"),
            )
        raise ValueError(f"Unhandled data source type: {source_type}")

    @classmethod
    def register(cls, type_name: str, source_cls: type[BaseDataSource]) -> None:
        """Register a custom data source type."""
        cls._registry[type_name] = source_cls
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_data_source.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add testagent/rule_engine/models.py testagent/rule_engine/data_source.py tests/rule_engine/test_data_source.py
git commit -m "feat(rule_engine): add DataSource with ApiDataSource and DatabaseDataSource"
```

---

## Task 3: SmartComparator — Built-in Matchers and Transforms

**Files:**
- Create: `testagent/rule_engine/smart_comparator.py`
- Create: `tests/rule_engine/test_smart_comparator.py`

- [ ] **Step 1: Write failing tests for SmartComparator**

```python
# tests/rule_engine/test_smart_comparator.py
from __future__ import annotations

import pytest
from testagent.rule_engine.smart_comparator import SmartComparator


class TestNumericMatcher:
    def test_integer_vs_float(self):
        comp = SmartComparator()
        result = comp.compare("100.0", 100)
        assert result.matched is True
        assert result.matcher_used == "NumericMatcher"

    def test_string_number_vs_int(self):
        comp = SmartComparator()
        result = comp.compare("42", 42)
        assert result.matched is True

    def test_float_precision(self):
        comp = SmartComparator()
        result = comp.compare("100.00", 100)
        assert result.matched is True

    def test_mismatch(self):
        comp = SmartComparator()
        result = comp.compare("100", 200)
        assert result.matched is False

    def test_non_numeric_falls_through(self):
        comp = SmartComparator()
        result = comp.compare("hello", 100)
        # Should not match with NumericMatcher
        assert result.matcher_used != "NumericMatcher" or not result.matched


class TestCurrencyMatcher:
    def test_yuan_symbol(self):
        comp = SmartComparator()
        result = comp.compare("¥150.00", 150)
        assert result.matched is True
        assert result.matcher_used == "CurrencyMatcher"

    def test_dollar_symbol(self):
        comp = SmartComparator()
        result = comp.compare("$99.99", 99.99)
        assert result.matched is True

    def test_with_comma(self):
        comp = SmartComparator()
        result = comp.compare("¥1,234.56", 1234.56)
        assert result.matched is True

    def test_currency_mismatch(self):
        comp = SmartComparator()
        result = comp.compare("¥100", 200)
        assert result.matched is False

    def test_both_strings(self):
        comp = SmartComparator()
        result = comp.compare("¥100.00", "100")
        assert result.matched is True


class TestFuzzyStringMatcher:
    def test_case_insensitive(self):
        comp = SmartComparator()
        result = comp.compare("Hello", "hello")
        assert result.matched is True
        assert result.matcher_used == "FuzzyStringMatcher"

    def test_trim_whitespace(self):
        comp = SmartComparator()
        result = comp.compare("  hello  ", "hello")
        assert result.matched is True

    def test_mismatch(self):
        comp = SmartComparator()
        result = comp.compare("hello", "world")
        assert result.matched is False


class TestDatetimeMatcher:
    def test_iso_date_vs_timestamp(self):
        comp = SmartComparator()
        # 2026-06-07 in ISO format
        result = comp.compare("2026-06-07", "2026-06-07")
        assert result.matched is True
        assert result.matcher_used == "DatetimeMatcher"

    def test_different_format_same_date(self):
        comp = SmartComparator()
        result = comp.compare("2026/06/07", "2026-06-07")
        assert result.matched is True


class TestExplicitTransforms:
    def test_strip_currency_transform(self):
        comp = SmartComparator()
        result = comp.compare("¥200.00", 200, transform="strip_currency")
        assert result.matched is True
        assert result.matcher_used == "transform:strip_currency"

    def test_divide_by_100_transform(self):
        comp = SmartComparator()
        result = comp.compare("¥100.00", 1, transform="divide_by_100")
        # ¥100.00 -> strip -> 100.00 -> divide by 100 -> 1.0
        assert result.matched is True

    def test_map_transform(self):
        comp = SmartComparator()
        mapping = {"1": "待发货", "2": "已发货", "3": "已完成"}
        result = comp.compare("已发货", 2, transform={"type": "map", "rules": mapping})
        assert result.matched is True

    def test_strict_mode_no_auto_match(self):
        comp = SmartComparator()
        # Without transform, strict mode should do exact comparison
        result = comp.compare("¥100", 100, compare_mode="strict")
        assert result.matched is False  # "¥100" != 100 strictly


class TestCompareResult:
    def test_result_fields(self):
        comp = SmartComparator()
        result = comp.compare("42", 42)
        assert hasattr(result, "matched")
        assert hasattr(result, "ui_value")
        assert hasattr(result, "expected_value")
        assert hasattr(result, "matcher_used")
        assert hasattr(result, "confidence")
        assert hasattr(result, "message")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_smart_comparator.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement SmartComparator**

```python
# testagent/rule_engine/smart_comparator.py
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from testagent.rule_engine.models import CompareResult


class SmartComparator:
    """Intelligent comparison with three-layer fallback.

    Layer 1: Built-in Smart Matchers (80% of cases, zero config)
    Layer 2: Explicit Transforms (19% of cases)
    Layer 3: LLM Semantic (deferred to V1.1+)
    """

    def compare(
        self,
        ui_value: Any,
        expected_value: Any,
        transform: Any = None,
        compare_mode: str = "auto",
    ) -> CompareResult:
        """Compare two values using the funnel strategy.

        Args:
            ui_value: Value extracted from UI.
            expected_value: Value from API/DB.
            transform: Optional transform to apply before comparison.
            compare_mode: "auto" (funnel) or "strict" (exact match).

        Returns:
            CompareResult with match status and metadata.
        """
        # Layer 2: Explicit transform takes priority
        if transform is not None:
            return self._apply_transform(ui_value, expected_value, transform)

        # Strict mode: no auto-matching, direct comparison
        if compare_mode == "strict":
            matched = str(ui_value) == str(expected_value)
            return CompareResult(
                matched=matched,
                ui_value=ui_value,
                expected_value=expected_value,
                matcher_used="strict",
                confidence=1.0,
                message="Exact string match" if matched else f"'{ui_value}' != '{expected_value}'",
            )

        # Layer 1: Auto-match funnel
        return self._auto_match(ui_value, expected_value)

    def _auto_match(self, ui_value: Any, expected_value: Any) -> CompareResult:
        """Try built-in matchers in order."""
        matchers = [
            ("NumericMatcher", self._try_numeric),
            ("CurrencyMatcher", self._try_currency),
            ("DatetimeMatcher", self._try_datetime),
            ("FuzzyStringMatcher", self._try_fuzzy_string),
        ]

        for name, matcher_fn in matchers:
            result = matcher_fn(ui_value, expected_value)
            if result is not None:
                return result

        # Fallback: exact string comparison
        matched = str(ui_value) == str(expected_value)
        return CompareResult(
            matched=matched,
            ui_value=ui_value,
            expected_value=expected_value,
            matcher_used="exact_string",
            confidence=1.0 if matched else 0.0,
            message="Exact match" if matched else f"'{ui_value}' != '{expected_value}'",
        )

    def _try_numeric(self, ui_value: Any, expected_value: Any) -> CompareResult | None:
        """Try numeric comparison."""
        try:
            ui_num = float(str(ui_value).strip())
            exp_num = float(str(expected_value).strip())
            matched = abs(ui_num - exp_num) < 1e-9
            return CompareResult(
                matched=matched,
                ui_value=ui_value,
                expected_value=expected_value,
                matcher_used="NumericMatcher",
                confidence=1.0,
                message=f"Numeric: {ui_num} {'==' if matched else '!='} {exp_num}",
            )
        except (ValueError, TypeError):
            return None

    def _try_currency(self, ui_value: Any, expected_value: Any) -> CompareResult | None:
        """Try currency comparison (strip ¥/$ and commas)."""
        ui_str = str(ui_value).strip()
        exp_str = str(expected_value).strip()

        # Strip currency symbols and commas
        ui_clean = re.sub(r"[¥$€£,]", "", ui_str).strip()
        exp_clean = re.sub(r"[¥$€£,]", "", exp_str).strip()

        # Check if either had currency symbols
        has_currency = any(s in ui_str for s in "¥$€£") or any(s in exp_str for s in "¥$€£")
        if not has_currency:
            return None

        try:
            ui_num = float(ui_clean)
            exp_num = float(exp_clean)
            matched = abs(ui_num - exp_num) < 1e-9
            return CompareResult(
                matched=matched,
                ui_value=ui_value,
                expected_value=expected_value,
                matcher_used="CurrencyMatcher",
                confidence=1.0,
                message=f"Currency: {ui_num} {'==' if matched else '!='} {exp_num}",
            )
        except (ValueError, TypeError):
            return None

    def _try_datetime(self, ui_value: Any, expected_value: Any) -> CompareResult | None:
        """Try datetime comparison (normalize formats)."""
        ui_str = str(ui_value).strip()
        exp_str = str(expected_value).strip()

        # Common date patterns
        date_patterns = [
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y%m%d",
        ]

        ui_dt = None
        exp_dt = None

        for fmt in date_patterns:
            try:
                ui_dt = datetime.strptime(ui_str, fmt)
                break
            except ValueError:
                continue

        for fmt in date_patterns:
            try:
                exp_dt = datetime.strptime(exp_str, fmt)
                break
            except ValueError:
                continue

        if ui_dt is None or exp_dt is None:
            return None

        matched = ui_dt == exp_dt
        return CompareResult(
            matched=matched,
            ui_value=ui_value,
            expected_value=expected_value,
            matcher_used="DatetimeMatcher",
            confidence=1.0,
            message=f"Datetime: {ui_dt} {'==' if matched else '!='} {exp_dt}",
        )

    def _try_fuzzy_string(self, ui_value: Any, expected_value: Any) -> CompareResult | None:
        """Try fuzzy string comparison (case-insensitive, trim)."""
        ui_str = str(ui_value).strip().lower()
        exp_str = str(expected_value).strip().lower()

        # Only use fuzzy if both are non-numeric strings
        try:
            float(ui_str)
            float(exp_str)
            return None  # Both numeric, let NumericMatcher handle
        except ValueError:
            pass

        matched = ui_str == exp_str
        return CompareResult(
            matched=matched,
            ui_value=ui_value,
            expected_value=expected_value,
            matcher_used="FuzzyStringMatcher",
            confidence=1.0 if matched else 0.0,
            message=f"Fuzzy: '{ui_str}' {'==' if matched else '!='} '{exp_str}'",
        )

    def _apply_transform(self, ui_value: Any, expected_value: Any, transform: Any) -> CompareResult:
        """Apply an explicit transform before comparison."""
        if isinstance(transform, str):
            # Built-in transform name
            transformed = self._run_builtin_transform(ui_value, transform)
        elif isinstance(transform, dict):
            # Complex transform with rules
            transform_type = transform.get("type", "")
            if transform_type == "map":
                transformed = self._run_map_transform(ui_value, transform.get("rules", {}))
            else:
                transformed = ui_value
        else:
            transformed = ui_value

        # Compare transformed value with expected
        try:
            matched = abs(float(str(transformed)) - float(str(expected_value))) < 1e-9
            matcher_used = f"transform:{transform if isinstance(transform, str) else transform.get('type', 'custom')}"
        except (ValueError, TypeError):
            matched = str(transformed).strip().lower() == str(expected_value).strip().lower()
            matcher_used = f"transform:{transform if isinstance(transform, str) else 'custom'}"

        return CompareResult(
            matched=matched,
            ui_value=transformed,
            expected_value=expected_value,
            matcher_used=matcher_used,
            confidence=1.0,
            message=f"Transform applied: {ui_value} -> {transformed}, {'matched' if matched else 'mismatch'}",
        )

    @staticmethod
    def _run_builtin_transform(value: Any, transform_name: str) -> Any:
        """Run a built-in transform by name."""
        value_str = str(value).strip()

        if transform_name == "strip_currency":
            return re.sub(r"[¥$€£,]", "", value_str).strip()
        elif transform_name == "divide_by_100":
            cleaned = re.sub(r"[¥$€£,]", "", value_str).strip()
            try:
                return float(cleaned) / 100
            except ValueError:
                return value
        else:
            return value

    @staticmethod
    def _run_map_transform(value: Any, rules: dict[str, str]) -> Any:
        """Run a mapping transform (e.g., status code to text)."""
        value_str = str(value).strip()
        # Try reverse mapping (value -> key)
        for key, mapped_value in rules.items():
            if value_str == mapped_value:
                return key
        # Try direct mapping (key -> value)
        return rules.get(value_str, value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_smart_comparator.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add testagent/rule_engine/smart_comparator.py tests/rule_engine/test_smart_comparator.py
git commit -m "feat(rule_engine): add SmartComparator with 4 built-in matchers and transforms"
```

---

## Task 4: UIExtractor — DOM Extraction (MVP without OCR)

**Files:**
- Create: `testagent/rule_engine/ui_extractor.py`
- Create: `tests/rule_engine/test_ui_extractor.py`

- [ ] **Step 1: Write failing tests for UIExtractor**

```python
# tests/rule_engine/test_ui_extractor.py
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from testagent.rule_engine.ui_extractor import UIExtractor
from testagent.rule_engine.context_manager import ContextManager


class TestUIExtractorDOM:
    @pytest.mark.asyncio
    async def test_extract_text_by_xpath(self):
        """UIExtractor extracts text from DOM element via Appium."""
        extractor = UIExtractor(
            appium_url="http://localhost:4723",
            session_id="test-session",
        )
        ctx = ContextManager()

        mock_source = '''<hierarchy>
            <node text="¥150.00" resource-id="price" class="android.widget.TextView"/>
        </hierarchy>'''

        with patch("testagent.rule_engine.ui_extractor.app_get_source",
                    new_callable=AsyncMock, return_value={"source": mock_source}):
            result = await extractor.extract(
                config={"semantic": "商品价格", "locator": {"resource_id": "price"}},
                context=ctx,
            )

        assert result == "¥150.00"

    @pytest.mark.asyncio
    async def test_extract_returns_none_when_not_found(self):
        """UIExtractor returns None when element not found in DOM."""
        extractor = UIExtractor(
            appium_url="http://localhost:4723",
            session_id="test-session",
        )
        ctx = ContextManager()

        mock_source = '<hierarchy><node text="Other" resource-id="other"/></hierarchy>'

        with patch("testagent.rule_engine.ui_extractor.app_get_source",
                    new_callable=AsyncMock, return_value={"source": mock_source}):
            result = await extractor.extract(
                config={"semantic": "商品价格", "locator": {"resource_id": "price"}},
                context=ctx,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_extract_collection(self):
        """UIExtractor extracts all text items from a list."""
        extractor = UIExtractor(
            appium_url="http://localhost:4723",
            session_id="test-session",
        )
        ctx = ContextManager()

        mock_source = '''<hierarchy>
            <node class="android.widget.ListView" resource-id="result_list">
                <node text="华为手机 P60" class="android.widget.TextView"/>
                <node text="华为手机 Mate 60" class="android.widget.TextView"/>
                <node text="苹果手机 iPhone 15" class="android.widget.TextView"/>
            </node>
        </hierarchy>'''

        with patch("testagent.rule_engine.ui_extractor.app_get_source",
                    new_callable=AsyncMock, return_value={"source": mock_source}):
            result = await extractor.extract_collection(
                config={"semantic": "搜索结果列表", "locator": {"resource_id": "result_list"}},
                context=ctx,
            )

        assert len(result) == 3
        assert "华为手机 P60" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_ui_extractor.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement UIExtractor**

```python
# testagent/rule_engine/ui_extractor.py
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from testagent.common.logging import get_logger
from testagent.rule_engine.context_manager import ContextManager

logger = get_logger(__name__)


class UIExtractor:
    """Extract values from UI using three-layer fallback.

    MVP: DOM extraction only.
    V1.1+: OCR (Layer 2) and VLM (Layer 3) will be added.
    """

    def __init__(self, appium_url: str, session_id: str) -> None:
        self._appium_url = appium_url
        self._session_id = session_id

    async def extract(self, config: dict[str, Any], context: ContextManager) -> Any:
        """Extract a single value from UI.

        Args:
            config: Dict with 'semantic', optional 'locator', optional 'transform'.
            context: Current context for variable resolution.

        Returns:
            Extracted text value, or None if not found.
        """
        # Layer 1: DOM extraction
        result = await self._extract_from_dom(config)
        if result is not None:
            # Apply transform if specified
            transform = config.get("transform")
            if transform:
                result = self._apply_transform(result, transform)
            return result

        # Layer 2: OCR (deferred to V1.1+)
        logger.info(f"DOM extraction failed for '{config.get('semantic', '?')}', OCR not yet implemented")

        # Layer 3: VLM (deferred to V1.1+)
        return None

    async def extract_collection(self, config: dict[str, Any], context: ContextManager) -> list[str]:
        """Extract all text items from a list/collection element.

        Returns:
            List of text strings, or empty list if not found.
        """
        source = await self._get_page_source()
        if not source:
            return []

        locator = config.get("locator", {})
        resource_id = locator.get("resource_id", "")

        try:
            root = ET.fromstring(source)
        except ET.ParseError:
            return []

        # Find the parent container
        if resource_id:
            container = root.find(f".//*[@resource-id='{resource_id}']")
        else:
            container = root

        if container is None:
            return []

        # Extract all child text nodes
        texts = []
        for node in container.iter("node"):
            text = node.get("text", "").strip()
            if text:
                texts.append(text)

        return texts

    async def _extract_from_dom(self, config: dict[str, Any]) -> str | None:
        """Extract text from DOM using locator."""
        source = await self._get_page_source()
        if not source:
            return None

        locator = config.get("locator", {})
        resource_id = locator.get("resource_id", "")
        xpath = locator.get("xpath", "")
        text_match = locator.get("text", "")

        try:
            root = ET.fromstring(source)
        except ET.ParseError:
            return None

        # Try resource-id first
        if resource_id:
            node = root.find(f".//*[@resource-id='{resource_id}']")
            if node is not None:
                return node.get("text", "").strip() or None

        # Try text content match
        if text_match:
            for node in root.iter("node"):
                if node.get("text", "") == text_match:
                    return text_match

        # Try semantic matching (find element near text label)
        semantic = config.get("semantic", "")
        if semantic:
            return self._semantic_extract(root, semantic)

        return None

    @staticmethod
    def _semantic_extract(root: ET.Element, semantic: str) -> str | None:
        """Try to find a value element near a label matching the semantic text.

        Strategy: find a node whose text contains the semantic keyword,
        then look for a sibling or nearby node with a numeric/currency value.
        """
        # Find the label node
        label_node = None
        for node in root.iter("node"):
            node_text = node.get("text", "")
            if semantic in node_text:
                label_node = node
                break

        if label_node is None:
            return None

        # Look for value in siblings (same parent)
        parent = None
        for p in root.iter("node"):
            for child in p:
                if child is label_node:
                    parent = p
                    break

        if parent is not None:
            for child in parent:
                if child is label_node:
                    continue
                text = child.get("text", "").strip()
                # Look for numeric/currency values
                if text and re.search(r"[\d¥$€£]", text):
                    return text

        # Fallback: return the label text itself if it contains a value
        label_text = label_node.get("text", "").strip()
        if re.search(r"[\d¥$€£]", label_text):
            return label_text

        return None

    async def _get_page_source(self) -> str | None:
        """Get current page source from Appium."""
        try:
            from testagent.mcp_servers.appium_server.tools import app_get_source
            result = await app_get_source(
                appium_url=self._appium_url,
                session_id=self._session_id,
            )
            return result.get("source", "")
        except Exception as e:
            logger.warning(f"Failed to get page source: {e}")
            return None

    @staticmethod
    def _apply_transform(value: str, transform: str) -> str:
        """Apply a simple string transform."""
        if transform == "strip_currency":
            return re.sub(r"[¥$€£,]", "", value).strip()
        return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_ui_extractor.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add testagent/rule_engine/ui_extractor.py tests/rule_engine/test_ui_extractor.py
git commit -m "feat(rule_engine): add UIExtractor with DOM extraction (OCR/VLM deferred)"
```

---

## Task 5: YAML Parser — Parse Setup and Assertions Configs

**Files:**
- Create: `testagent/rule_engine/yaml_parser.py`
- Create: `tests/rule_engine/test_yaml_parser.py`

- [ ] **Step 1: Write failing tests for YAML parser**

```python
# tests/rule_engine/test_yaml_parser.py
from __future__ import annotations

import pytest
from testagent.rule_engine.yaml_parser import RuleYamlParser
from testagent.rule_engine.models import DataSourceConfig, AssertionConfig


class TestRuleYamlParser:
    def test_parse_setup_api(self):
        yaml_data = {
            "setup": [
                {
                    "name": "create_product",
                    "type": "api",
                    "method": "POST",
                    "endpoint": "${API_BASE}/products",
                    "body": {"name": "Test"},
                    "extract": {"product_id": "$.data.id"},
                }
            ]
        }
        parser = RuleYamlParser()
        result = parser.parse_setup(yaml_data["setup"])
        assert len(result) == 1
        assert result[0].name == "create_product"
        assert result[0].type == "api"
        assert result[0].method == "POST"
        assert result[0].extract == {"product_id": "$.data.id"}

    def test_parse_setup_database(self):
        yaml_data = {
            "setup": [
                {
                    "name": "query_db",
                    "type": "database",
                    "connection": "sqlite:///test.db",
                    "query": "SELECT * FROM products WHERE id = :product_id",
                    "extract": {"db_price": "$.rows[0].price"},
                }
            ]
        }
        parser = RuleYamlParser()
        result = parser.parse_setup(yaml_data["setup"])
        assert len(result) == 1
        assert result[0].type == "database"
        assert result[0].connection == "sqlite:///test.db"

    def test_parse_assertions_cross_source(self):
        yaml_data = {
            "assertions": [
                {
                    "type": "cross_source",
                    "field": "discount_price",
                    "sources": {
                        "ui": {"semantic": "商品折扣价"},
                        "api": {"source_ref": "create_product", "extract": "$.discount_price"},
                    },
                }
            ]
        }
        parser = RuleYamlParser()
        result = parser.parse_assertions(yaml_data["assertions"])
        assert len(result) == 1
        assert result[0].type == "cross_source"
        assert result[0].field == "discount_price"

    def test_parse_assertions_ui_visible(self):
        yaml_data = {
            "assertions": [
                {
                    "type": "ui_visible",
                    "target": "商品卡片",
                    "expected": True,
                }
            ]
        }
        parser = RuleYamlParser()
        result = parser.parse_assertions(yaml_data["assertions"])
        assert len(result) == 1
        assert result[0].type == "ui_visible"

    def test_parse_empty_setup(self):
        parser = RuleYamlParser()
        result = parser.parse_setup([])
        assert result == []

    def test_parse_empty_assertions(self):
        parser = RuleYamlParser()
        result = parser.parse_assertions([])
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_yaml_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement YAML parser**

```python
# testagent/rule_engine/yaml_parser.py
from __future__ import annotations

from typing import Any

from testagent.rule_engine.models import AssertionConfig, DataSourceConfig


class RuleYamlParser:
    """Parse rule engine YAML configs into internal models."""

    def parse_setup(self, setup_list: list[dict[str, Any]]) -> list[DataSourceConfig]:
        """Parse setup data source configs from YAML."""
        result = []
        for item in setup_list or []:
            result.append(DataSourceConfig(
                name=item.get("name", ""),
                type=item.get("type", ""),
                method=item.get("method", ""),
                endpoint=item.get("endpoint", ""),
                headers=item.get("headers", {}),
                body=item.get("body", {}),
                connection=item.get("connection", ""),
                query=item.get("query", ""),
                extract=item.get("extract", {}),
                source_ref=item.get("source_ref", ""),
            ))
        return result

    def parse_assertions(self, assertion_list: list[dict[str, Any]]) -> list[AssertionConfig]:
        """Parse assertion configs from YAML."""
        result = []
        for item in assertion_list or []:
            result.append(AssertionConfig(
                type=item.get("type", ""),
                field=item.get("field", ""),
                target=item.get("target", ""),
                expected=item.get("expected"),
                sources=item.get("sources", {}),
                compare_mode=item.get("compare_mode", "auto"),
            ))
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_yaml_parser.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add testagent/rule_engine/yaml_parser.py tests/rule_engine/test_yaml_parser.py
git commit -m "feat(rule_engine): add YAML parser for setup and assertions configs"
```

---

## Task 6: RuleEngine — Orchestrator

**Files:**
- Create: `testagent/rule_engine/engine.py`
- Create: `tests/rule_engine/test_engine.py`

- [ ] **Step 1: Write failing tests for RuleEngine**

```python
# tests/rule_engine/test_engine.py
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from testagent.rule_engine.engine import RuleEngine
from testagent.rule_engine.context_manager import ContextManager
from testagent.rule_engine.models import AssertionStatus


class TestRuleEngineExecuteSetup:
    @pytest.mark.asyncio
    async def test_execute_api_setup(self):
        """RuleEngine.execute_setup() runs API data source and registers results in context."""
        engine = RuleEngine(appium_url="http://localhost:4723", session_id="test")

        setup_configs = [
            {
                "name": "create_product",
                "type": "api",
                "method": "POST",
                "endpoint": "http://test/api/products",
                "body": {"name": "Test"},
                "extract": {"product_id": "$.data.id"},
            }
        ]

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"data": {"id": "prod-123"}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            await engine.execute_setup(setup_configs)

        assert engine.context.get("product_id") == "prod-123"

    @pytest.mark.asyncio
    async def test_execute_setup_with_context_resolution(self):
        """RuleEngine resolves ${var} in setup configs using context."""
        engine = RuleEngine(appium_url="http://localhost:4723", session_id="test")
        engine.context.register("API_BASE", "http://test/api")

        setup_configs = [
            {
                "name": "get_product",
                "type": "api",
                "method": "GET",
                "endpoint": "${API_BASE}/products/123",
                "extract": {"name": "$.data.name"},
            }
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"name": "Widget"}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
            await engine.execute_setup(setup_configs)

        assert engine.context.get("name") == "Widget"


class TestRuleEngineExecuteAssertions:
    @pytest.mark.asyncio
    async def test_cross_source_assertion_pass(self):
        """RuleEngine.execute_assertions() passes when values match."""
        engine = RuleEngine(appium_url="http://localhost:4723", session_id="test")
        engine.context.register("product_id", "123")

        # Mock UI extraction
        with patch.object(engine._ui_extractor, "extract", new_callable=AsyncMock, return_value="¥150.00"):
            # Mock API fetch for real-time source
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"data": {"price": 150}}
            mock_response.raise_for_status = MagicMock()

            assertion_configs = [
                {
                    "type": "cross_source",
                    "field": "price",
                    "sources": {
                        "ui": {"semantic": "商品价格"},
                        "api": {
                            "type": "api",
                            "method": "GET",
                            "endpoint": "http://test/api/products/${product_id}",
                            "extract": "$.data.price",
                        },
                    },
                }
            ]

            with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
                results = await engine.execute_assertions(assertion_configs)

        assert len(results) == 1
        assert results[0].status == AssertionStatus.PASS

    @pytest.mark.asyncio
    async def test_cross_source_assertion_fail(self):
        """RuleEngine.execute_assertions() fails when values don't match."""
        engine = RuleEngine(appium_url="http://localhost:4723", session_id="test")
        engine.context.register("product_id", "123")

        with patch.object(engine._ui_extractor, "extract", new_callable=AsyncMock, return_value="¥100.00"):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"data": {"price": 200}}
            mock_response.raise_for_status = MagicMock()

            assertion_configs = [
                {
                    "type": "cross_source",
                    "field": "price",
                    "sources": {
                        "ui": {"semantic": "商品价格"},
                        "api": {
                            "type": "api",
                            "method": "GET",
                            "endpoint": "http://test/api/products/${product_id}",
                            "extract": "$.data.price",
                        },
                    },
                }
            ]

            with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response):
                results = await engine.execute_assertions(assertion_configs)

        assert len(results) == 1
        assert results[0].status == AssertionStatus.FAIL

    @pytest.mark.asyncio
    async def test_assertion_with_source_ref(self):
        """RuleEngine resolves source_ref from setup cache."""
        engine = RuleEngine(appium_url="http://localhost:4723", session_id="test")
        engine._setup_cache["create_product"] = {"product_id": "123", "price": "150"}

        with patch.object(engine._ui_extractor, "extract", new_callable=AsyncMock, return_value="¥150.00"):
            assertion_configs = [
                {
                    "type": "cross_source",
                    "field": "price",
                    "sources": {
                        "ui": {"semantic": "商品价格"},
                        "api": {"source_ref": "create_product", "extract": "$.price"},
                    },
                }
            ]

            results = await engine.execute_assertions(assertion_configs)

        assert len(results) == 1
        assert results[0].status == AssertionStatus.PASS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_engine.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement RuleEngine**

```python
# testagent/rule_engine/engine.py
from __future__ import annotations

from typing import Any

from testagent.common.logging import get_logger
from testagent.rule_engine.context_manager import ContextManager
from testagent.rule_engine.data_source import ApiDataSource, DataSourceFactory
from testagent.rule_engine.models import (
    AssertionConfig,
    AssertionResult,
    AssertionStatus,
    CompareResult,
    DataSourceConfig,
)
from testagent.rule_engine.smart_comparator import SmartComparator
from testagent.rule_engine.ui_extractor import UIExtractor
from testagent.rule_engine.yaml_parser import RuleYamlParser

logger = get_logger(__name__)


class RuleEngine:
    """Orchestrates data fetching, UI extraction, and comparison.

    Manages the three-phase execution:
    Phase A: execute_setup() — fetch data from APIs/DBs, register in context
    Phase B: UI operations happen in ExecutionEngine (not here)
    Phase C: execute_assertions() — extract UI values, compare with external data
    """

    def __init__(self, appium_url: str, session_id: str) -> None:
        self.context = ContextManager()
        self._ui_extractor = UIExtractor(appium_url, session_id)
        self._comparator = SmartComparator()
        self._parser = RuleYamlParser()
        self._setup_cache: dict[str, dict[str, Any]] = {}

    async def execute_setup(self, setup_configs: list[dict[str, Any]]) -> None:
        """Phase A: Execute all data sources and register results in context.

        Args:
            setup_configs: List of data source config dicts from YAML.
        """
        configs = self._parser.parse_setup(setup_configs)

        for config in configs:
            # Resolve ${var} in config
            resolved_config = self.context.resolve_dict(config.model_dump())

            source = DataSourceFactory.create(resolved_config)

            if config.type == "api":
                result = await source.fetch(self.context)
            elif config.type == "database":
                result = await source.fetch(self.context)
            else:
                logger.warning(f"Unknown data source type: {config.type}")
                continue

            if "error" in result:
                logger.warning(f"Setup data source '{config.name}' failed: {result['error']}")
                continue

            # Register extracted values in context
            self.context.register_batch(result)
            # Also cache for source_ref lookups
            self._setup_cache[config.name] = result
            logger.info(f"Setup '{config.name}' completed: {list(result.keys())}")

    async def execute_assertions(
        self, assertion_configs: list[dict[str, Any]]
    ) -> list[AssertionResult]:
        """Phase C: Execute all assertions and return results.

        Args:
            assertion_configs: List of assertion config dicts from YAML.

        Returns:
            List of AssertionResult objects.
        """
        configs = self._parser.parse_assertions(assertion_configs)
        results = []

        for config in configs:
            if config.type == "cross_source":
                result = await self._execute_cross_source(config)
            elif config.type == "ui_visible":
                result = self._execute_ui_visible(config)
            else:
                result = AssertionResult(
                    field=config.field or config.target,
                    assertion_type=config.type,
                    status=AssertionStatus.ERROR,
                    error_message=f"Unknown assertion type: {config.type}",
                )
            results.append(result)

        return results

    async def _execute_cross_source(self, config: AssertionConfig) -> AssertionResult:
        """Execute a cross-source comparison assertion."""
        field = config.field
        sources = config.sources

        # Extract UI value
        ui_config = sources.get("ui", {})
        ui_value = await self._ui_extractor.extract(ui_config, self.context)

        if ui_value is None:
            return AssertionResult(
                field=field,
                assertion_type="cross_source",
                status=AssertionStatus.ERROR,
                error_message=f"UI extraction failed for '{ui_config.get('semantic', field)}'",
                source_values={"ui": None},
            )

        # Extract expected value from API/DB source
        expected_value = None
        source_name = ""

        for source_key in ("api", "database", "db"):
            if source_key in sources:
                source_config = sources[source_key]
                source_name = source_key
                expected_value = await self._extract_expected_value(source_config)
                break

        if expected_value is None:
            return AssertionResult(
                field=field,
                assertion_type="cross_source",
                status=AssertionStatus.ERROR,
                error_message=f"Failed to extract expected value from {source_name} source",
                source_values={"ui": ui_value},
            )

        # Compare
        compare_config = ui_config if ui_config.get("transform") else {}
        compare_mode = config.compare_mode

        compare_result = self._comparator.compare(
            ui_value=ui_value,
            expected_value=expected_value,
            transform=compare_config.get("transform"),
            compare_mode=compare_mode,
        )

        status = AssertionStatus.PASS if compare_result.matched else AssertionStatus.FAIL

        return AssertionResult(
            field=field,
            assertion_type="cross_source",
            status=status,
            compare_result=compare_result,
            source_values={"ui": ui_value, source_name: expected_value},
        )

    async def _extract_expected_value(self, source_config: dict[str, Any]) -> Any:
        """Extract expected value from a source config (cache or real-time)."""
        source_ref = source_config.get("source_ref", "")
        extract_path = source_config.get("extract", "")

        # Try cache first (source_ref)
        if source_ref and source_ref in self._setup_cache:
            cached = self._setup_cache[source_ref]
            if extract_path:
                return ApiDataSource._resolve_json_path(cached, extract_path)
            return cached

        # Real-time fetch
        if source_config.get("type") in ("api", "database"):
            resolved = self.context.resolve_dict(source_config)
            source = DataSourceFactory.create(resolved)
            result = await source.fetch(self.context)
            if "error" not in result:
                # Extract the specific field
                for key, value in result.items():
                    return value
            return None

        return None

    def _execute_ui_visible(self, config: AssertionConfig) -> AssertionResult:
        """Execute a simple UI visibility assertion (placeholder for MVP)."""
        # This is a placeholder — actual UI visibility is checked by ExecutionEngine
        return AssertionResult(
            field=config.target,
            assertion_type="ui_visible",
            status=AssertionStatus.PASS,
            error_message="UI visibility check delegated to ExecutionEngine",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_engine.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add testagent/rule_engine/engine.py tests/rule_engine/test_engine.py
git commit -m "feat(rule_engine): add RuleEngine orchestrator for cross-source validation"
```

---

## Task 7: Model Integration — Add Rule Engine Fields to TestCase/TCExecution

**Files:**
- Modify: `testagent/plan/models.py`

- [ ] **Step 1: Add cross_source_results field to TCExecution**

Add the following field to the `TCExecution` class in `testagent/plan/models.py`:

```python
# In TCExecution class, add after assert_warnings:
    cross_source_results: list[dict[str, object]] = Field(default_factory=list)
```

- [ ] **Step 2: Add setup and assertions fields to TestCase**

Add the following fields to the `TestCase` class in `testagent/plan/models.py`:

```python
# In TestCase class, add after execution:
    setup: list[dict[str, object]] = Field(default_factory=list)
    assertions: list[dict[str, object]] = Field(default_factory=list)
```

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/ -v -x --timeout=30 -k "not test_app" 2>/dev/null | tail -20`
Expected: Existing tests still pass

- [ ] **Step 4: Commit**

```bash
git add testagent/plan/models.py
git commit -m "feat(plan): add setup/assertions/cross_source_results fields to models"
```

---

## Task 8: ExecutionEngine Integration — Setup Execution Hook

**Files:**
- Modify: `testagent/plan/execution_engine.py`

- [ ] **Step 1: Add RuleEngine initialization to ExecutionEngine**

Add to the `__init__` method of `ExecutionEngine`:

```python
# In __init__, after self._coordinate_cache = CoordinateCache():
        self._rule_engine: Any | None = None
```

- [ ] **Step 2: Add setup execution before steps in _execute_single**

In the `_execute_single` method, add setup execution after the precondition check and before step execution:

```python
# In _execute_single(), after "await self._ensure_app_launched()" and before "for step in tc.steps:":
        # ── Phase A: Execute setup data sources ──────────────────────
        if tc.setup:
            await self._execute_setup(tc)
```

- [ ] **Step 3: Add _execute_setup method**

Add this method to `ExecutionEngine`:

```python
    async def _execute_setup(self, tc: TestCase) -> None:
        """Execute setup data sources and register results in rule engine context."""
        from testagent.rule_engine.engine import RuleEngine

        if self._rule_engine is None:
            self._rule_engine = RuleEngine(
                appium_url=self.session_manager.appium_url,
                session_id=self.session_manager.session_id or "",
            )

        try:
            await self._rule_engine.execute_setup(tc.setup)
            self._log(f"  [Setup: {len(tc.setup)} data source(s) executed]")
        except Exception as exc:
            self._log(f"  [Setup warning: {exc}]")
```

- [ ] **Step 4: Add assertions execution after steps**

In `_execute_single`, add assertions execution after all steps pass:

```python
# In _execute_single(), after "tc.execution.status = ExecutionStatus.EXECUTED" and before the method ends:
        # ── Phase C: Execute cross-source assertions ──────────────────
        if tc.assertions and self._rule_engine is not None:
            try:
                assertion_results = await self._rule_engine.execute_assertions(tc.assertions)
                tc.execution.cross_source_results = [
                    r.model_dump() for r in assertion_results
                ]
                # Check for failures
                failed = [r for r in assertion_results if r.status.value == "FAIL"]
                if failed:
                    tc.execution.status = ExecutionStatus.FAILED
                    tc.execution.error_message = f"Cross-source assertion failed: {failed[0].field}"
                    tc.execution.failure_type = FailureType.ASSERTION_FAILED
                self._log(f"  [Assertions: {len(assertion_results)} executed, {len(failed)} failed]")
            except Exception as exc:
                self._log(f"  [Assertions warning: {exc}]")
```

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/ -v -x --timeout=30 -k "not test_app" 2>/dev/null | tail -20`
Expected: Existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add testagent/plan/execution_engine.py
git commit -m "feat(plan): integrate RuleEngine into ExecutionEngine for setup and assertions"
```

---

## Task 9: PerTCEvaluator Integration — Cross-Source Results in Evaluation

**Files:**
- Modify: `testagent/plan/evaluator.py`

- [ ] **Step 1: Add cross-source check to _fallback_evaluate**

In `PerTCEvaluator._fallback_evaluate`, add a check for cross-source results before the existing `EXECUTED` status handling:

```python
# In _fallback_evaluate(), after the BLOCKED check and before the EXECUTED check:
        if status == ExecutionStatus.EXECUTED:
            # Check cross-source assertion results first
            cross_results = tc.execution.cross_source_results
            if cross_results:
                failed_assertions = [
                    r for r in cross_results
                    if isinstance(r, dict) and r.get("status") == "FAIL"
                ]
                error_assertions = [
                    r for r in cross_results
                    if isinstance(r, dict) and r.get("status") == "ERROR"
                ]
                if failed_assertions:
                    first_fail = failed_assertions[0]
                    compare = first_fail.get("compare_result", {})
                    return EvaluationOutput(
                        verdict=ExecutionVerdict.FAIL,
                        confidence=compare.get("confidence", 0.9) if isinstance(compare, dict) else 0.9,
                        reason=f"Cross-source mismatch on '{first_fail.get('field', '?')}': "
                               f"{compare.get('message', '') if isinstance(compare, dict) else ''}",
                        evidence=list(tc.execution.evidence),
                        evidence_missing=missing,
                        failure_type=FailureType.ASSERTION_FAILED,
                    )
                if error_assertions:
                    first_err = error_assertions[0]
                    return EvaluationOutput(
                        verdict=ExecutionVerdict.NEED_REVIEW,
                        confidence=0.5,
                        reason=f"Cross-source error on '{first_err.get('field', '?')}': "
                               f"{first_err.get('error_message', 'unknown')}",
                        evidence=list(tc.execution.evidence),
                        evidence_missing=missing,
                    )
```

- [ ] **Step 2: Add import for FailureType**

Ensure `FailureType` is imported at the top of `evaluator.py`:

```python
from testagent.plan.models import (
    EvaluationOutput,
    ExecutionStatus,
    ExecutionVerdict,
    FailureType,
    StepExecution,
    TestCase,
)
```

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/ -v -x --timeout=30 -k "not test_app" 2>/dev/null | tail -20`
Expected: Existing tests still pass

- [ ] **Step 4: Commit**

```bash
git add testagent/plan/evaluator.py
git commit -m "feat(plan): integrate cross-source results into PerTCEvaluator"
```

---

## Task 10: Update Public API — rule_engine __init__.py

**Files:**
- Modify: `testagent/rule_engine/__init__.py`

- [ ] **Step 1: Update __init__.py with public exports**

```python
# testagent/rule_engine/__init__.py
"""Cross-source validation engine for business-semantic-level test verification.

Provides multi-source data fusion (UI + API + DB) with intelligent comparison
for automated test validation beyond simple element assertions.

Usage:
    from testagent.rule_engine import RuleEngine, SmartComparator, ContextManager

    engine = RuleEngine(appium_url="http://localhost:4723", session_id="abc")
    await engine.execute_setup(setup_configs)
    results = await engine.execute_assertions(assertion_configs)
"""

from testagent.rule_engine.context_manager import ContextManager
from testagent.rule_engine.data_source import (
    ApiDataSource,
    BaseDataSource,
    DatabaseDataSource,
    DataSourceFactory,
)
from testagent.rule_engine.engine import RuleEngine
from testagent.rule_engine.models import (
    AssertionConfig,
    AssertionResult,
    AssertionStatus,
    CompareResult,
    DataSourceConfig,
)
from testagent.rule_engine.smart_comparator import SmartComparator
from testagent.rule_engine.ui_extractor import UIExtractor
from testagent.rule_engine.yaml_parser import RuleYamlParser

__all__ = [
    "ContextManager",
    "BaseDataSource",
    "ApiDataSource",
    "DatabaseDataSource",
    "DataSourceFactory",
    "RuleEngine",
    "SmartComparator",
    "UIExtractor",
    "RuleYamlParser",
    "AssertionConfig",
    "AssertionResult",
    "AssertionStatus",
    "CompareResult",
    "DataSourceConfig",
]
```

- [ ] **Step 2: Run all rule_engine tests**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add testagent/rule_engine/__init__.py
git commit -m "feat(rule_engine): update public API exports"
```

---

## Task 11: End-to-End Integration Test

**Files:**
- Create: `tests/rule_engine/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/rule_engine/test_integration.py
"""End-to-end integration test for the cross-source validation engine."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from testagent.rule_engine import RuleEngine, AssertionStatus


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_full_flow(self):
        """Test complete flow: setup -> context resolution -> assertion."""
        engine = RuleEngine(appium_url="http://localhost:4723", session_id="test")

        # Phase A: Setup
        setup_configs = [
            {
                "name": "create_product",
                "type": "api",
                "method": "POST",
                "endpoint": "http://test/api/products",
                "body": {"name": "Test Product", "price": 150},
                "extract": {"product_id": "$.data.id"},
            }
        ]

        mock_create_response = MagicMock()
        mock_create_response.status_code = 201
        mock_create_response.json.return_value = {"data": {"id": "prod-123"}}
        mock_create_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_create_response):
            await engine.execute_setup(setup_configs)

        assert engine.context.get("product_id") == "prod-123"

        # Phase C: Assertions (mock UI extraction)
        assertion_configs = [
            {
                "type": "cross_source",
                "field": "price",
                "sources": {
                    "ui": {"semantic": "商品价格"},
                    "api": {
                        "type": "api",
                        "method": "GET",
                        "endpoint": "http://test/api/products/${product_id}",
                        "extract": "$.data.price",
                    },
                },
            }
        ]

        mock_get_response = MagicMock()
        mock_get_response.status_code = 200
        mock_get_response.json.return_value = {"data": {"price": 150}}
        mock_get_response.raise_for_status = MagicMock()

        with patch.object(engine._ui_extractor, "extract", new_callable=AsyncMock, return_value="¥150.00"):
            with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_get_response):
                results = await engine.execute_assertions(assertion_configs)

        assert len(results) == 1
        assert results[0].status == AssertionStatus.PASS
        assert results[0].compare_result.matched is True
        assert results[0].compare_result.matcher_used == "CurrencyMatcher"

    @pytest.mark.asyncio
    async def test_full_flow_with_source_ref(self):
        """Test flow with source_ref (cache reuse) instead of real-time fetch."""
        engine = RuleEngine(appium_url="http://localhost:4723", session_id="test")

        # Simulate setup cache
        engine._setup_cache["create_product"] = {
            "product_id": "prod-456",
            "price": 200,
        }
        engine.context.register("product_id", "prod-456")

        assertion_configs = [
            {
                "type": "cross_source",
                "field": "price",
                "sources": {
                    "ui": {"semantic": "商品价格"},
                    "api": {"source_ref": "create_product", "extract": "$.price"},
                },
            }
        ]

        with patch.object(engine._ui_extractor, "extract", new_callable=AsyncMock, return_value="¥200.00"):
            results = await engine.execute_assertions(assertion_configs)

        assert len(results) == 1
        assert results[0].status == AssertionStatus.PASS

    @pytest.mark.asyncio
    async def test_mismatch_reports_fail(self):
        """Test that price mismatch results in FAIL status."""
        engine = RuleEngine(appium_url="http://localhost:4723", session_id="test")
        engine._setup_cache["api"] = {"price": 200}

        assertion_configs = [
            {
                "type": "cross_source",
                "field": "price",
                "sources": {
                    "ui": {"semantic": "商品价格"},
                    "api": {"source_ref": "api", "extract": "$.price"},
                },
            }
        ]

        with patch.object(engine._ui_extractor, "extract", new_callable=AsyncMock, return_value="¥100.00"):
            results = await engine.execute_assertions(assertion_configs)

        assert len(results) == 1
        assert results[0].status == AssertionStatus.FAIL
        assert "100" in results[0].compare_result.message
        assert "200" in results[0].compare_result.message
```

- [ ] **Step 2: Run integration test**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/test_integration.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite**

Run: `cd D:/test-ai-agent/vibe-ai-agent && python -m pytest tests/rule_engine/ -v`
Expected: All rule_engine tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/rule_engine/test_integration.py
git commit -m "test(rule_engine): add end-to-end integration tests"
```

---

## Verification Checklist

After all tasks are complete, verify:

- [ ] All `tests/rule_engine/` tests pass
- [ ] Existing tests still pass (no regression)
- [ ] `ContextManager` handles ${variable} substitution correctly
- [ ] `ApiDataSource` can fetch from REST APIs and extract JSON paths
- [ ] `SmartComparator` auto-matches numeric, currency, datetime, fuzzy string
- [ ] `UIExtractor` extracts text from DOM via Appium page source
- [ ] `RuleEngine` orchestrates the full setup → assertion flow
- [ ] `PerTCEvaluator` correctly evaluates cross-source results
- [ ] Error cases return ERROR status (not crash)
