from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from testagent.mcp_servers.vision_server.volcano_client import VolcanoVisionClient
from testagent.mcp_servers.vision_server.server import VisionMCPServer
from testagent.mcp_servers.vision_server.tools import (
    _parse_found_status,
    _parse_percentage_coordinates,
    _parse_suggestion,
    vision_describe_screen,
    vision_find_element,
)


DW, DH = 1080, 2400  # default test device dimensions


class TestCoordinateParsing:
    def test_parse_center_point_percent(self) -> None:
        text = "目标位于屏幕中央，坐标 (50%, 50%)"
        result = _parse_percentage_coordinates(text, DW, DH)
        assert result.get("center") == {"x": 540, "y": 1200}
        assert result.get("center_pct") == {"x": 50.0, "y": 50.0}

    def test_parse_center_point_chinese_percent(self) -> None:
        text = "目标位于（30%， 40%）"
        result = _parse_percentage_coordinates(text, DW, DH)
        assert result.get("center") == {"x": 324, "y": 960}
        assert result.get("center_pct") == {"x": 30.0, "y": 40.0}

    def test_parse_bounding_box_percent(self) -> None:
        text = "元素边界框 [10%, 20%, 30%, 40%]"
        result = _parse_percentage_coordinates(text, DW, DH)
        assert result.get("bounds") == {"x1": 108, "y1": 480, "x2": 324, "y2": 960}
        assert result.get("bounds_pct") == {"x1": 10.0, "y1": 20.0, "x2": 30.0, "y2": 40.0}

    def test_parse_bounding_box_chinese_comma(self) -> None:
        text = "元素边界框[10%，20%，30%，40%]"
        result = _parse_percentage_coordinates(text, DW, DH)
        assert result.get("bounds") == {"x1": 108, "y1": 480, "x2": 324, "y2": 960}

    def test_parse_center_and_bounds(self) -> None:
        text = "中心点 (50%, 50%)，边界框 [10%, 20%, 30%, 40%]"
        result = _parse_percentage_coordinates(text, DW, DH)
        assert result.get("center") == {"x": 540, "y": 1200}
        assert result.get("bounds") == {"x1": 108, "y1": 480, "x2": 324, "y2": 960}

    def test_no_coordinates(self) -> None:
        text = "屏幕上没有找到目标元素"
        result = _parse_percentage_coordinates(text, DW, DH)
        assert result == {}


class TestFoundStatusParsing:
    def test_found_true_explicit(self) -> None:
        assert _parse_found_status("found: true，目标在屏幕中央") is True

    def test_found_false_explicit(self) -> None:
        assert _parse_found_status("found: false，目标不在当前屏幕") is False

    def test_found_with_coordinates(self) -> None:
        assert _parse_found_status("目标位于(50%, 50%)") is True

    def test_found_without_coordinates(self) -> None:
        assert _parse_found_status("屏幕上没有找到该元素") is False


class TestSuggestionParsing:
    def test_swipe_left_chinese(self) -> None:
        assert _parse_suggestion("建议向左滑动") == "swipe_left"

    def test_swipe_right_chinese(self) -> None:
        assert _parse_suggestion("请向右滑动") == "swipe_right"

    def test_swipe_up(self) -> None:
        assert _parse_suggestion("向上滑动") == "swipe_up"

    def test_swipe_down(self) -> None:
        assert _parse_suggestion("向下滑动") == "swipe_down"

    def test_swipe_left_direction(self) -> None:
        assert _parse_suggestion("建议向左划") == "swipe_left"

    def test_swipe_right_direction(self) -> None:
        assert _parse_suggestion("请向右划") == "swipe_right"

    def test_no_suggestion(self) -> None:
        assert _parse_suggestion("目标在当前屏幕中") is None


class TestVisionMCPServer:
    def test_server_name(self) -> None:
        server = VisionMCPServer(api_key="test-key")
        assert server.server_name == "vision_server"

    @pytest.mark.asyncio
    async def test_list_tools(self) -> None:
        server = VisionMCPServer(api_key="test-key")
        tools = await server.list_tools()
        tool_names = [t["name"] for t in tools]
        assert "vision_find_element" in tool_names
        assert "vision_describe_screen" in tool_names
        assert len(tools) == 2

    @pytest.mark.asyncio
    async def test_call_tool_unknown(self) -> None:
        server = VisionMCPServer(api_key="test-key")
        result = await server.call_tool("unknown_tool", {})
        result_str = result if isinstance(result, str) else ""
        assert "Unknown tool" in result_str

    @pytest.mark.asyncio
    async def test_call_tool_vision_find_element_no_key(self) -> None:
        server = VisionMCPServer(api_key="")
        result = await server.call_tool("vision_find_element", {"image": "abc", "target": "test"})
        result_dict = json.loads(result) if isinstance(result, str) else {}
        assert result_dict.get("found") is False
        assert "error" in result_dict

    @pytest.mark.asyncio
    async def test_health_check_no_key(self) -> None:
        server = VisionMCPServer(api_key="")
        result = await server.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_with_key(self) -> None:
        server = VisionMCPServer(api_key="test-key")
        result = await server.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_list_resources(self) -> None:
        server = VisionMCPServer(api_key="test-key")
        resources = await server.list_resources()
        assert len(resources) == 1
        assert resources[0]["uri"] == "vision://status"

    def test_from_settings(self) -> None:
        mock_settings = MagicMock()
        mock_settings.vision_api_key.get_secret_value.return_value = "key-from-settings"
        mock_settings.vision_api_url = "https://test.api.com"
        mock_settings.vision_model = "test-model"
        mock_settings.vision_timeout = 60
        mock_settings.vision_max_retries = 5

        server = VisionMCPServer.from_settings(mock_settings)
        assert server._vision_client._api_key == "key-from-settings"
        assert server._vision_client._api_url == "https://test.api.com"
        assert server._vision_client._model == "test-model"

    def test_from_settings_default(self) -> None:
        with patch("testagent.config.settings.get_settings") as mock_get:
            mock_settings = MagicMock()
            mock_settings.vision_api_key.get_secret_value.return_value = "default-key"
            mock_settings.vision_api_url = "https://ark.cn-beijing.volces.com/api/v3"
            mock_settings.vision_model = "doubao-seed-2-0-lite-260428"
            mock_settings.vision_timeout = 60
            mock_settings.vision_max_retries = 3
            mock_get.return_value = mock_settings

            server = VisionMCPServer.from_settings()
            assert server._vision_client._api_key == "default-key"


class TestVolcanoVisionClient:
    def test_init(self) -> None:
        client = VolcanoVisionClient(api_key="test-key")
        assert client.is_configured is True
        assert client._model == "doubao-seed-2-0-lite-260428"
        assert client._timeout == 60
        assert client._max_retries == 2

    def test_is_configured_false(self) -> None:
        client = VolcanoVisionClient(api_key="")
        assert client.is_configured is False

    @pytest.mark.asyncio
    async def test_analyze_no_key(self) -> None:
        client = VolcanoVisionClient(api_key="")
        result = await client.analyze("base64image", "找到目标")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_analyze_success(self) -> None:
        client = VolcanoVisionClient(api_key="test-key", max_retries=1)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "找到目标，坐标 (100, 200)"}, "finish_reason": "stop"}]
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await client.analyze("base64image", "找到美团app")
            assert "error" not in result
            assert "找到目标" in result["content"]

    @pytest.mark.asyncio
    async def test_analyze_retry_on_500(self) -> None:
        client = VolcanoVisionClient(api_key="test-key", max_retries=2)
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 500
        mock_response_fail.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=mock_response_fail
        )

        mock_response_ok = MagicMock()
        mock_response_ok.status_code = 200
        mock_response_ok.json.return_value = {
            "choices": [{"message": {"content": "找到目标"}, "finish_reason": "stop"}]
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [mock_response_fail, mock_response_ok]
            result = await client.analyze("base64", "prompt")
            assert "error" not in result
            assert "找到目标" in result["content"]
            assert mock_post.call_count == 2

    @pytest.mark.asyncio
    async def test_analyze_no_retry_on_400(self) -> None:
        """4xx errors should not be retried."""
        client = VolcanoVisionClient(api_key="test-key", max_retries=3)
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=mock_response
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await client.analyze("base64", "prompt")
            assert "error" in result
            # Should only try once
            assert mock_client.post.call_count == 1


class TestVisionTools:
    @pytest.mark.asyncio
    async def test_vision_find_element_no_client(self) -> None:
        result = await vision_find_element("image_data", "test target")
        assert result.get("found") is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_vision_find_element_success(self) -> None:
        mock_client = AsyncMock(spec=VolcanoVisionClient)
        mock_client.analyze.return_value = {
            "content": "found: true，目标在屏幕中央，坐标 (50%, 50%)，边界框 [10%, 20%, 30%, 40%]",
            "finish_reason": "stop",
        }

        result = await vision_find_element(
            "image_data", "美团 app", vision_client=mock_client,
            device_width=1080, device_height=2400,
        )
        assert result["found"] is True
        assert result["center"] == {"x": 540, "y": 1200}
        assert result["center_pct"] == {"x": 50.0, "y": 50.0}
        assert result["bounds"] == {"x1": 108, "y1": 480, "x2": 324, "y2": 960}
        assert result["bounds_pct"] == {"x1": 10.0, "y1": 20.0, "x2": 30.0, "y2": 40.0}

    @pytest.mark.asyncio
    async def test_vision_find_element_not_found_with_suggestion(self) -> None:
        mock_client = AsyncMock(spec=VolcanoVisionClient)
        mock_client.analyze.return_value = {
            "content": "found: false，目标不在当前屏幕，建议向左滑动",
            "finish_reason": "stop",
        }

        result = await vision_find_element("image_data", "快手 app", vision_client=mock_client)
        assert result["found"] is False
        assert result["suggestion"] == "swipe_left"

    @pytest.mark.asyncio
    async def test_vision_find_element_with_context(self) -> None:
        mock_client = AsyncMock(spec=VolcanoVisionClient)
        mock_client.analyze.return_value = {
            "content": "found: false，当前屏幕是第二页，建议向右滑回第一页",
            "finish_reason": "stop",
        }

        result = await vision_find_element(
            "image_data", "微信", context="上一页没有找到微信", vision_client=mock_client
        )
        assert result["found"] is False
        assert mock_client.analyze.call_args[0][1].startswith("之前的屏幕分析：上一页没有找到微信")

    @pytest.mark.asyncio
    async def test_vision_describe_screen_no_client(self) -> None:
        result = await vision_describe_screen("image_data")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_vision_describe_screen_success(self) -> None:
        mock_client = AsyncMock(spec=VolcanoVisionClient)
        mock_client.analyze.return_value = {
            "content": "当前屏幕是 Android 主桌面，包含以下应用图标：时钟、设置、相机、电话、短信。底部有导航栏。",
            "finish_reason": "stop",
        }

        result = await vision_describe_screen("image_data", vision_client=mock_client)
        assert "error" not in result
        assert "description" in result
        assert "Android" in result["description"]
