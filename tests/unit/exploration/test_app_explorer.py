"""Tests for AppExplorer core orchestration module."""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from testagent.exploration.app_explorer import AppExplorer, _page_fingerprint
from testagent.exploration.ui_tree_parser import UIElement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_element(text: str = "", content_desc: str = "", bounds: str = "[0,0][100,100]") -> UIElement:
    return UIElement(
        text=text,
        content_desc=content_desc,
        element_type="button",
        bounds=bounds,
        resource_id="",
    )


SIMPLE_XML = """\
<hierarchy>
  <node class="android.widget.TextView" text="首页" content-desc="" resource-id="title"
        bounds="[0,0][200,50]" clickable="true" enabled="true" />
  <node class="android.widget.Button" text="搜索" content-desc="搜索入口" resource-id="btn_search"
        bounds="[200,0][400,50]" clickable="true" enabled="true" />
  <node class="android.widget.EditText" text="" content-desc="" resource-id="input_search"
        bounds="[0,60][400,110]" clickable="false" enabled="true" />
</hierarchy>
"""

# Patch targets — the source module where the functions are defined,
# since app_explorer uses lazy imports inside methods.
_PATCH_LAUNCH = "testagent.mcp_servers.appium_server.tools.app_launch"
_PATCH_SOURCE = "testagent.mcp_servers.appium_server.tools.app_get_source"
_PATCH_TAP = "testagent.mcp_servers.appium_server.tools.app_tap"
_PATCH_EXEC = "testagent.mcp_servers.appium_server.tools.app_exec"

# --- _page_fingerprint tests ---


class TestPageFingerprint:
    def test_same_elements_same_fingerprint(self):
        els = [_make_element("搜索"), _make_element("首页")]
        fp1 = _page_fingerprint(els)
        fp2 = _page_fingerprint(els)
        assert fp1 == fp2
        assert len(fp1) == 12

    def test_different_elements_different_fingerprint(self):
        fp1 = _page_fingerprint([_make_element("搜索")])
        fp2 = _page_fingerprint([_make_element("设置")])
        assert fp1 != fp2

    def test_empty_elements_empty_fingerprint(self):
        assert _page_fingerprint([]) == ""

    def test_order_independent(self):
        els_a = [_make_element("A"), _make_element("B")]
        els_b = [_make_element("B"), _make_element("A")]
        assert _page_fingerprint(els_a) == _page_fingerprint(els_b)


# --- AppExplorer tests ---


class TestAppExplorer:
    # ---- explore() ----

    @pytest.mark.asyncio
    async def test_explore_returns_empty_on_planner_failure(self):
        """Planner.plan() raises -> return empty UIContextMap, session not created."""
        mock_session = MagicMock()
        mock_session.create_session = AsyncMock(return_value="sess-1")

        explorer = AppExplorer(session_manager=mock_session, llm_callable=lambda t: "")

        # Replace the planner with one whose plan() raises
        explorer._planner = MagicMock()
        explorer._planner.plan = AsyncMock(side_effect=RuntimeError("LLM down"))

        result = await explorer.explore("some PRD", "com.example.app")

        assert result.pages == []
        mock_session.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_returns_empty_on_session_failure(self):
        """create_session returns None -> return empty UIContextMap."""

        async def ok_llm(text: str) -> str:
            return "[]"

        mock_session = MagicMock()
        mock_session.create_session = AsyncMock(return_value=None)

        explorer = AppExplorer(session_manager=mock_session, llm_callable=ok_llm)
        result = await explorer.explore("some PRD", "com.example.app")

        assert result.pages == []

    @pytest.mark.asyncio
    @patch(_PATCH_SOURCE, new_callable=AsyncMock)
    @patch(_PATCH_LAUNCH, new_callable=AsyncMock)
    async def test_explore_records_home_page(self, mock_launch, mock_source):
        """Home page is recorded with breadcrumb ['App启动'] after successful launch."""
        mock_launch.return_value = {"result": "ok"}
        mock_source.return_value = {"source": SIMPLE_XML, "format": "xml"}

        async def ok_llm(text: str) -> str:
            return "[]"

        mock_session = MagicMock()
        mock_session.create_session = AsyncMock(return_value="sess-1")
        mock_session.session_id = "sess-1"
        mock_session.close_session = AsyncMock()

        explorer = AppExplorer(session_manager=mock_session, llm_callable=ok_llm)
        result = await explorer.explore("PRD text", "com.example.app")

        assert len(result.pages) == 1
        home = result.pages[0]
        assert home.name == "首页"
        assert home.breadcrumb == ["App启动"]
        assert len(home.elements) > 0

    @pytest.mark.asyncio
    @patch(_PATCH_SOURCE, new_callable=AsyncMock)
    @patch(_PATCH_LAUNCH, new_callable=AsyncMock)
    @patch(_PATCH_TAP, new_callable=AsyncMock)
    async def test_explore_skips_failed_target(self, mock_tap, mock_launch, mock_source):
        """Target whose tap returns error -> exploration_status='failed'."""
        mock_launch.return_value = {"result": "ok"}
        # Return home XML always so fingerprint matches during navigate_to_home
        mock_source.return_value = {"source": SIMPLE_XML, "format": "xml"}
        mock_tap.return_value = {"error": "Element not found"}

        target_json = [
            {
                "target_name": "搜索结果页",
                "keywords": ["搜索"],
                "reach_actions": [
                    {"type": "tap", "target_hint": "不存在的按钮"},
                ],
                "priority": 1,
            }
        ]

        async def ok_llm(text: str) -> str:
            return json.dumps(target_json)

        mock_session = MagicMock()
        mock_session.create_session = AsyncMock(return_value="sess-1")
        mock_session.session_id = "sess-1"
        mock_session.close_session = AsyncMock()

        explorer = AppExplorer(session_manager=mock_session, llm_callable=ok_llm)
        result = await explorer.explore("PRD", "com.example.app")

        # Home + failed target
        assert len(result.pages) == 2
        failed = result.pages[1]
        assert failed.name == "搜索结果页"
        assert failed.exploration_status == "failed"

    # ---- _find_element_by_hint ----

    def test_find_element_by_hint_exact(self):
        explorer = AppExplorer.__new__(AppExplorer)
        elements = [_make_element("搜索"), _make_element("首页")]
        result = explorer._find_element_by_hint(elements, "搜索")
        assert result is not None
        assert result.text == "搜索"

    def test_find_element_by_hint_substring(self):
        explorer = AppExplorer.__new__(AppExplorer)
        elements = [_make_element("搜索按钮"), _make_element("首页")]
        result = explorer._find_element_by_hint(elements, "搜索")
        assert result is not None
        assert result.text == "搜索按钮"

    def test_find_element_by_hint_keyword(self):
        explorer = AppExplorer.__new__(AppExplorer)
        elements = [_make_element("点击这里搜索商品"), _make_element("首页")]
        result = explorer._find_element_by_hint(elements, "搜索商品")
        assert result is not None
        assert result.text == "点击这里搜索商品"

    def test_find_element_by_hint_not_found(self):
        explorer = AppExplorer.__new__(AppExplorer)
        elements = [_make_element("首页"), _make_element("设置")]
        result = explorer._find_element_by_hint(elements, "搜索")
        assert result is None
