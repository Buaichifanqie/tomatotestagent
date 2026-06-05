"""End-to-end integration tests for the AppExplorer pipeline.

Tests the full flow: XML -> parse -> build context map -> serialize -> inject into prompt,
cache roundtrip, and planner-to-explorer flow.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from testagent.exploration.app_explorer import AppExplorer
from testagent.exploration.exploration_planner import ExplorationPlanner
from testagent.exploration.map_cache import MapCache
from testagent.exploration.ui_context_map import ElementInfo, PageInfo, UIContextMap
from testagent.exploration.ui_tree_parser import parse_ui_tree


# ---------------------------------------------------------------------------
# Fixture XML
# ---------------------------------------------------------------------------

FIXTURE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="tv.danmaku.bili" content-desc="" checkable="false" checked="false"
        clickable="false" enabled="true" focusable="false" focused="false"
        scrollable="false" long-clickable="false" password="false" selected="false"
        bounds="[0,0][1080,2340]">
    <node index="0" text="首页" resource-id="tv.danmaku.bili:id/tab_title"
          class="android.widget.TextView" content-desc="首页Tab"
          clickable="true" enabled="true" bounds="[0,2196][270,2340]">
    </node>
    <node index="1" text="热门" resource-id="tv.danmaku.bili:id/tab_title"
          class="android.widget.TextView" content-desc=""
          clickable="true" enabled="true" bounds="[270,2196][540,2340]">
    </node>
    <node index="2" text="" resource-id="tv.danmaku.bili:id/search_bar"
          class="android.widget.EditText" content-desc="搜索"
          clickable="true" enabled="true" bounds="[540,100][1020,180]">
    </node>
  </node>
</hierarchy>
"""

# Patch targets used by AppExplorer
_PATCH_LAUNCH = "testagent.mcp_servers.appium_server.tools.app_launch"
_PATCH_SOURCE = "testagent.mcp_servers.appium_server.tools.app_get_source"
_PATCH_TAP = "testagent.mcp_servers.appium_server.tools.app_tap"
_PATCH_EXEC = "testagent.mcp_servers.appium_server.tools.app_exec"


# ---------------------------------------------------------------------------
# Test 1: Full pipeline parse -> build -> serialize -> inject
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """test_full_pipeline_parse_build_serialize_inject"""

    def test_full_pipeline_parse_build_serialize_inject(self):
        # 1. Parse fixture XML — verify 3 elements extracted
        elements = parse_ui_tree(FIXTURE_XML)
        assert len(elements) == 3, f"Expected 3 elements, got {len(elements)}"

        # Verify element details
        texts = [el.display_text for el in elements]
        assert "首页" in texts
        assert "热门" in texts
        assert "搜索" in texts  # from content-desc on the EditText

        # 2. Build a UIContextMap with one page using parsed elements
        element_infos = [ElementInfo.from_ui_element(el) for el in elements]
        page = PageInfo(
            name="首页",
            elements=element_infos,
            breadcrumb=["App启动"],
            description="B站首页",
        )
        context_map = UIContextMap()
        context_map.add_page(page)

        assert len(context_map.pages) == 1
        assert context_map.element_count == 3

        # 3. Serialize to dict and restore — verify roundtrip
        d = context_map.to_dict()
        restored = UIContextMap.from_dict(d)

        assert len(restored.pages) == 1
        assert restored.pages[0].name == "首页"
        assert restored.pages[0].description == "B站首页"
        assert restored.pages[0].breadcrumb == ["App启动"]
        assert len(restored.pages[0].elements) == 3

        for orig, rest in zip(element_infos, restored.pages[0].elements):
            assert orig.text == rest.text
            assert orig.element_type == rest.element_type
            assert orig.center_x == rest.center_x
            assert orig.center_y == rest.center_y
            assert orig.resource_id == rest.resource_id

        # 4. Generate prompt string — verify it contains element names and coordinates
        ctx_str = context_map.to_context_string()
        assert "首页" in ctx_str
        assert "热门" in ctx_str
        assert "搜索" in ctx_str
        # Verify coordinates are present (center of [0,2196][270,2340] = 135, 2268)
        assert "135" in ctx_str
        assert "2268" in ctx_str

        # 5. Append to a PRD-like string — verify both PRD and UI context are present
        prd_text = "## 产品需求\n测试B站首页功能。"
        full_prompt = f"{prd_text}\n\n---\n\n{ctx_str}"
        assert "产品需求" in full_prompt
        assert "测试B站首页功能" in full_prompt
        assert "首页" in full_prompt
        assert "热门" in full_prompt


# ---------------------------------------------------------------------------
# Test 2: Cache roundtrip
# ---------------------------------------------------------------------------


class TestCacheRoundtrip:
    """test_cache_roundtrip"""

    def test_cache_roundtrip(self, tmp_path):
        # 1. Build a UIContextMap from parsed XML
        elements = parse_ui_tree(FIXTURE_XML)
        element_infos = [ElementInfo.from_ui_element(el) for el in elements]
        page = PageInfo(
            name="首页",
            elements=element_infos,
            breadcrumb=["App启动"],
        )
        context_map = UIContextMap()
        context_map.add_page(page)

        cache = MapCache(cache_dir=str(tmp_path))
        app_pkg = "tv.danmaku.bili"
        app_ver = "7.0.0"

        # 2. Save with MapCache.save()
        cache.save(app_pkg, app_ver, context_map)

        # 3. Load with MapCache.load() — verify content matches
        loaded = cache.load(app_pkg, app_ver)
        assert loaded is not None
        assert len(loaded.pages) == 1
        assert loaded.pages[0].name == "首页"
        assert len(loaded.pages[0].elements) == 3
        assert loaded.element_count == 3

        # Verify element details survive roundtrip
        loaded_texts = {el.text for el in loaded.pages[0].elements}
        assert loaded_texts == {"首页", "热门", "搜索"}

        # 4. Validate with same elements — should return True
        assert cache.validate(app_pkg, app_ver, element_infos) is True

        # 5. Validate with different elements — should return False
        different_elements = [
            ElementInfo(text="设置", element_type="text_view", center_x=100, center_y=100),
            ElementInfo(text="关于", element_type="text_view", center_x=200, center_y=200),
        ]
        assert cache.validate(app_pkg, app_ver, different_elements) is False


# ---------------------------------------------------------------------------
# Test 3: Planner to explorer flow
# ---------------------------------------------------------------------------


class TestPlannerToExplorerFlow:
    """test_planner_to_explorer_flow"""

    @pytest.mark.asyncio
    @patch(_PATCH_EXEC, new_callable=AsyncMock)
    @patch(_PATCH_TAP, new_callable=AsyncMock)
    @patch(_PATCH_SOURCE, new_callable=AsyncMock)
    @patch(_PATCH_LAUNCH, new_callable=AsyncMock)
    async def test_planner_to_explorer_flow(
        self, mock_launch, mock_source, mock_tap, mock_exec
    ):
        # 1. Create mock LLM that returns valid exploration targets JSON
        target_json = [
            {
                "target_name": "热门视频页",
                "keywords": ["热门", "视频"],
                "reach_actions": [
                    {"type": "tap", "target_hint": "热门"},
                ],
                "priority": 1,
            }
        ]

        async def mock_llm(text: str) -> str:
            return json.dumps(target_json)

        # 2. Call ExplorationPlanner.plan() — verify targets parsed correctly
        planner = ExplorationPlanner(mock_llm)
        targets = await planner.plan("测试B站视频播放功能")
        assert len(targets) == 1
        assert targets[0].target_name == "热门视频页"
        assert targets[0].keywords == ["热门", "视频"]
        assert len(targets[0].reach_actions) == 1
        assert targets[0].reach_actions[0].type == "tap"
        assert targets[0].reach_actions[0].target_hint == "热门"
        assert targets[0].priority == 1

        # 3. Create a mock SessionManager
        mock_session = MagicMock()
        mock_session.create_session = AsyncMock(return_value="sess-1")
        mock_session.session_id = "sess-1"
        mock_session.close_session = AsyncMock()

        # 4. Mock all Appium tools
        mock_launch.return_value = {"result": "ok"}
        mock_source.return_value = {"source": FIXTURE_XML, "format": "xml"}
        mock_tap.return_value = {}  # success
        mock_exec.return_value = {"result": "ok"}

        # 5. Create AppExplorer with mocked planner that returns the targets
        explorer = AppExplorer(session_manager=mock_session, llm_callable=mock_llm)

        # 6. Call explorer.explore() — verify home page recorded and context map has pages
        result = await explorer.explore(
            prd_text="测试B站视频播放功能",
            app_package="tv.danmaku.bili",
        )

        # Should have at least home page + target page
        assert len(result.pages) >= 2, f"Expected >= 2 pages, got {len(result.pages)}"

        # Verify home page
        home = result.pages[0]
        assert home.name == "首页"
        assert home.breadcrumb == ["App启动"]
        assert len(home.elements) == 3

        # Verify target page
        target_page = result.pages[1]
        assert target_page.name == "热门视频页"
        assert target_page.exploration_status == "success"
        assert "首页" in target_page.breadcrumb

        # Verify app was launched and session was managed correctly
        mock_launch.assert_called_once()
        mock_session.close_session.assert_called_once()
