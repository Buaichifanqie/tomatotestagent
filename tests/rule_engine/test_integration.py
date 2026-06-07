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
