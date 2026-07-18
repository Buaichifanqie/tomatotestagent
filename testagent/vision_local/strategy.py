"""元素识别策略（ElementSourceStrategy）。

遵循 ``PlatformFactory`` 策略模式，提供可切换的元素查找策略。

- ``MultimodalVisionStrategy``：封装现有多模态大模型传图方案。
- ``LocalVisionStrategy``：基于结构化页面解析的本地视觉方案。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from testagent.vision_local.engine import LocalVisionEngine

logger = logging.getLogger(__name__)


class ElementSourceStrategy(ABC):
    """元素识别策略抽象基类。"""

    @abstractmethod
    async def find_element(
        self,
        target: str,
        session_manager: Any,
        llm_provider: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """在屏幕中查找目标元素，返回 {x, y} 或 None（含 suggestion）。"""

    @abstractmethod
    async def describe_screen(
        self,
        session_manager: Any,
        **kwargs: Any,
    ) -> str:
        """描述当前屏幕内容。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称: multimodal / yolo / yolo_with_dom。"""


class MultimodalVisionStrategy(ElementSourceStrategy):
    """现有策略：截图发送到多模态大模型分析。

    封装 ``VolcanoVisionClient``，与 ExecutionEngine 现有逻辑一致。
    """

    def __init__(
        self,
        vision_client: Any | None = None,
        get_screen_size_fn: Any | None = None,
    ) -> None:
        self._vision_client = vision_client
        self._get_screen_size_fn = get_screen_size_fn

    async def find_element(
        self,
        target: str,
        session_manager: Any,
        llm_provider: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """使用多模态模型查找元素。

        实际由 ExecutionEngine 原生的 _vision_find_element 处理，
        此方法作为策略接口的统一包装。
        """
        # 这个策略不走这里 —— 实际逻辑保留在 ExecutionEngine 中
        # 此方法仅供策略工厂统一调用
        raise NotImplementedError(
            "MultimodalVisionStrategy 应直接调用 ExecutionEngine._vision_find_element"
        )

    async def describe_screen(
        self,
        session_manager: Any,
        **kwargs: Any,
    ) -> str:
        raise NotImplementedError

    @property
    def name(self) -> str:
        return "multimodal"


class LocalVisionStrategy(ElementSourceStrategy):
    """本地视觉策略：YOLOv8 + OCR + DOM → 结构化 JSON → LLM → 坐标。

    通过 ``LocalVisionEngine`` 编排 DOM/视觉通道，
    产生结构化 JSON 后由 LLM 做元素匹配决策。
    """

    def __init__(
        self,
        vision_engine: LocalVisionEngine | None = None,
        llm_provider: Any = None,
        use_dom: bool = True,
    ) -> None:
        self._engine = vision_engine
        self._llm_provider = llm_provider
        self._use_dom = use_dom

    async def find_element(
        self,
        target: str,
        session_manager: Any = None,
        llm_provider: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """使用本地视觉 + LLM 查找元素。

        Args:
            target: 目标元素描述。
            session_manager: 用于截图和获取 DOM XML。
            llm_provider: LLM provider（用于元素匹配决策）。

        Kwargs:
            screenshot_base64: 可选的预截图数据。
            dom_xml: 可选的预 DOM XML。
            skill_hard_rules: 硬性约束。
            operation_history: 历史操作记录。
            page_width: 屏幕宽度。
            page_height: 屏幕高度。

        Returns:
            {x, y} 坐标字典，或 None（未找到）。
        """
        if self._engine is None:
            logger.error("[LocalVisionStrategy] engine not initialized")
            return None

        screenshot_base64 = kwargs.get("screenshot_base64", "")
        dom_xml = kwargs.get("dom_xml", "")
        skill_hard_rules = kwargs.get("skill_hard_rules", "")
        operation_history = kwargs.get("operation_history", "")
        page_width = kwargs.get("page_width", 0)
        page_height = kwargs.get("page_height", 0)

        # 需要截图或 DOM 才能继续
        if not screenshot_base64 and not dom_xml:
            if session_manager is not None:
                # auto-acquire screenshot + DOM from session
                try:
                    from testagent.mcp_servers.appium_server.tools import (
                        app_get_source,
                        app_screenshot,
                    )
                    from testagent.mcp_servers.shared_cache import get_screenshot

                    # 截图
                    scr = await app_screenshot(
                        appium_url=session_manager.appium_url,
                        session_id=session_manager.session_id,
                    )
                    scr_id = scr.get("screenshot_id", "")
                    if scr_id:
                        screenshot_base64 = get_screenshot(scr_id) or ""

                    # DOM XML
                    src = await app_get_source(
                        appium_url=session_manager.appium_url,
                        session_id=session_manager.session_id,
                    )
                    dom_xml = src.get("source", "")

                    # 屏幕尺寸
                    if not page_width or not page_height:
                        from testagent.mcp_servers.appium_server.tools import app_exec

                        wm_result = await app_exec(
                            command="shell wm size",
                            appium_url=session_manager.appium_url,
                            session_id=session_manager.session_id,
                        )
                        stdout = wm_result.get("stdout", "")
                        import re
                        match = re.search(r"Override size:\s*(\d+)x(\d+)", stdout)
                        if not match:
                            match = re.search(r"Physical size:\s*(\d+)x(\d+)", stdout)
                        if match:
                            page_width = int(match.group(1))
                            page_height = int(match.group(2))
                except Exception as e:
                    logger.warning(f"[LocalVisionStrategy] auto-acquire failed: {e}")

        source_hint = "visual"
        if self._use_dom:
            source_hint = "auto"

        # 获取页面结构
        page_struct = await self._engine.get_page_structure(
            screenshot_base64=screenshot_base64,
            dom_xml=dom_xml,
            page_width=page_width,
            page_height=page_height,
            source_hint=source_hint,
        )

        if not page_struct.get("elements") and not page_struct.get("element_count", 0) > 0:
            logger.warning(f"[LocalVisionStrategy] 无页面元素，target={target}")
            return None

        # 用 LLM 匹配元素
        provider = llm_provider or self._llm_provider
        coords = await self._engine.find_element_by_llm(
            target=target,
            page_structure=page_struct,
            llm_provider=provider,
            skill_hard_rules=skill_hard_rules,
            operation_history=operation_history,
        )
        return coords

    async def describe_screen(
        self,
        session_manager: Any,
        **kwargs: Any,
    ) -> str:
        """返回当前屏幕的结构化文本描述。"""
        if self._engine is None:
            return ""

        screenshot_base64 = kwargs.get("screenshot_base64", "")
        dom_xml = kwargs.get("dom_xml", "")
        page_width = kwargs.get("page_width", 0)
        page_height = kwargs.get("page_height", 0)

        # 自动获取数据
        if not screenshot_base64 and not dom_xml and session_manager:
            try:
                from testagent.mcp_servers.appium_server.tools import (
                    app_get_source,
                    app_screenshot,
                )
                from testagent.mcp_servers.shared_cache import get_screenshot

                scr = await app_screenshot(
                    appium_url=session_manager.appium_url,
                    session_id=session_manager.session_id,
                )
                scr_id = scr.get("screenshot_id", "")
                if scr_id:
                    screenshot_base64 = get_screenshot(scr_id) or ""
                src = await app_get_source(
                    appium_url=session_manager.appium_url,
                    session_id=session_manager.session_id,
                )
                dom_xml = src.get("source", "")
            except Exception as e:
                logger.warning(f"[LocalVisionStrategy] describe_screen auto-acquire failed: {e}")

        source_hint = "visual"
        if self._use_dom:
            source_hint = "auto"

        page_struct = await self._engine.get_page_structure(
            screenshot_base64=screenshot_base64,
            dom_xml=dom_xml,
            page_width=page_width,
            page_height=page_height,
            source_hint=source_hint,
        )
        context = await self._engine.get_page_context_for_llm(page_struct)
        return context

    @property
    def name(self) -> str:
        return "yolo_with_dom" if self._use_dom else "yolo"
