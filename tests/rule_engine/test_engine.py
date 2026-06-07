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
