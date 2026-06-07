from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from testagent.rule_engine.context_manager import ContextManager


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

    @staticmethod
    def resolve_json_path(data: Any, path: str) -> Any:
        """Resolve a simple JSONPath expression like $.data.field."""
        return BaseDataSource._resolve_json_path(data, path)


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
        return await self._do_request(
            method=self.method,
            body=self.body,
            context=context,
        )

    async def create(self, data: dict[str, Any], context: ContextManager) -> dict[str, Any]:
        """Create data via POST and extract from response."""
        return await self._do_request(method="POST", body=data, context=context)

    async def _do_request(
        self,
        method: str,
        body: dict[str, Any] | None,
        context: ContextManager,
    ) -> dict[str, Any]:
        """Send an HTTP request with explicit method and body."""
        url = context.resolve(self.endpoint)
        headers = {k: context.resolve(v) for k, v in self.headers.items()}
        resolved_body = context.resolve_dict(body) if body else None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                kwargs: dict[str, Any] = {"url": url, "headers": headers}
                if method in ("POST", "PUT", "PATCH"):
                    kwargs["json"] = resolved_body

                method_fn = getattr(client, method.lower())
                response = await method_fn(**kwargs)
                response.raise_for_status()
                data = response.json()
                return self._extract_values(data)
        except httpx.TimeoutException:
            return {"error": f"API timeout: {method} {url}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"API error {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": f"API request failed: {str(e)}"}


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
        query_text, params = self._resolve_query_params(self.query, context)

        try:
            import sqlalchemy
            engine = sqlalchemy.create_engine(self.connection)
            with engine.connect() as conn:
                stmt = sqlalchemy.text(query_text).bindparams(**params)
                result = conn.execute(stmt)
                rows = [dict(row._mapping) for row in result]
                return self._extract_values({"rows": rows})
        except Exception as e:
            return {"error": f"Database query failed: {str(e)}"}

    async def create(self, data: dict[str, Any], context: ContextManager) -> dict[str, Any]:
        """Execute INSERT and return affected rows info."""
        return await self.fetch(context)

    @staticmethod
    def _resolve_query_params(
        query: str, context: ContextManager
    ) -> tuple[str, dict[str, Any]]:
        """Resolve :param style variables from context.

        Returns a tuple of (query_text, params_dict) where query_text uses
        __param__ placeholders and params_dict maps those placeholders to
        resolved values.  Safe for use with sqlalchemy.text().bindparams().
        """
        params: dict[str, Any] = {}

        def _replacer(match: re.Match[str]) -> str:
            name = match.group(1)
            value = context.get(name)
            if value is None:
                return match.group(0)
            placeholder = f"__{name}__"
            params[placeholder] = value
            return f":{placeholder}"

        safe_query = re.sub(r":(\w+)", _replacer, query)
        return safe_query, params


class DataSourceFactory:
    """Factory for creating data source instances from config dicts."""

    _registry: dict[str, type[BaseDataSource]] = {
        "api": ApiDataSource,
        "database": DatabaseDataSource,
    }

    # Maps each registered type to the config keys its constructor expects
    # (excluding ``name`` which is always extracted).
    _config_keys: dict[str, dict[str, Any]] = {
        "api": {
            "method": "GET",
            "endpoint": "",
            "headers": None,
            "body": None,
            "extract": None,
        },
        "database": {
            "connection": "",
            "query": "",
            "extract": None,
        },
    }

    @classmethod
    def create(cls, config: dict[str, Any]) -> BaseDataSource:
        """Create a data source from a config dict."""
        source_type = config.get("type", "")
        source_cls = cls._registry.get(source_type)
        if source_cls is None:
            raise ValueError(f"Unknown data source type: {source_type}")

        defaults = cls._config_keys.get(source_type, {})
        kwargs: dict[str, Any] = {"name": config.get("name", "")}
        for key, default in defaults.items():
            kwargs[key] = config.get(key, default)
        return source_cls(**kwargs)

    @classmethod
    def register(cls, type_name: str, source_cls: type[BaseDataSource]) -> None:
        """Register a custom data source type."""
        cls._registry[type_name] = source_cls
