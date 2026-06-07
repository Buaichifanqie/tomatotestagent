from __future__ import annotations

import re
from typing import Any

_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


class ContextManager:
    """Flat dictionary with ${variable} substitution.

    Variables are registered from setup extracts, built-in values,
    and UI extraction results. All references use simple ${name}
    syntax -- no deep paths.
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
