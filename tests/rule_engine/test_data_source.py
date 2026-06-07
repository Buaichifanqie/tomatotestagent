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
