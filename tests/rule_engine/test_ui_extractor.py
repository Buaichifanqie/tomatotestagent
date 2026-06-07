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
