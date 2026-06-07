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
                kwargs: dict[str, Any] = {"url": url, "headers": headers}
                if self.method in ("POST", "PUT", "PATCH"):
                    kwargs["json"] = body

                method_fn = getattr(client, self.method.lower())
                response = await method_fn(**kwargs)
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
