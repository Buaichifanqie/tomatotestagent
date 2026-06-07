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
