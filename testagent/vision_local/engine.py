"""本地视觉引擎：编排 DOM 通道和视觉通道，为 LLM 提供结构化页面数据。

核心流程：
1. 尝试 DOM 快通道（如有 XML 且 DOM 足够丰富）
2. 降级到视觉通道（YOLOv8 + OCR + 颜色分析）
3. 两通道输出统一的 ``PageStructure`` 供 LLM 决策
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from testagent.vision_local.dom_parser import DomParser
from testagent.vision_local.recognizer import PageElementRecognizer
from testagent.vision_local.types import PageStructure

logger = logging.getLogger(__name__)


class LocalVisionEngine:
    """本地视觉引擎：编排 DOM + 视觉分析，产生结构化页面 JSON。"""

    def __init__(
        self,
        dom_parser: DomParser | None = None,
        recognizer: PageElementRecognizer | None = None,
        use_dom: bool = True,
    ) -> None:
        self._dom_parser = dom_parser or DomParser()
        self._recognizer = recognizer
        self._use_dom = use_dom

    # ── 公共接口 ───────────────────────────────────────────────

    async def get_page_structure(
        self,
        screenshot_base64: str = "",
        dom_xml: str = "",
        page_width: int = 0,
        page_height: int = 0,
        source_hint: str = "auto",
    ) -> dict[str, Any]:
        """获取当前页面的结构化数据。

        Args:
            screenshot_base64: 截图的 base64 编码（用于视觉通道）。
            dom_xml: uiautomator2 的 XML 源码（用于 DOM 通道）。
            page_width: 已知的屏幕宽度（px），0 则从数据推断。
            page_height: 已知的屏幕高度（px）。
            source_hint:
                - ``"dom"``: 强制 DOM 通道
                - ``"visual"``: 强制视觉通道
                - ``"auto"``: DOM 优先，降级视觉

        Returns:
            兼容 ``PageStructure.to_integrated_dict()`` 格式的 dict。
        """
        # ── 尝试 DOM 通道 ──────────────────────────────────────────
        if source_hint in ("auto", "dom") and dom_xml:
            try:
                dom_elements, dw, dh = self._dom_parser.parse(dom_xml)
                if self._dom_parser.is_rich_dom(dom_elements):
                    logger.info(
                        f"[LocalVision] DOM 通道: {len(dom_elements)} 元素, "
                        f"rich=True, 使用 DOM 数据"
                    )
                    pw = page_width or dw or 1080
                    ph = page_height or dh or 2400
                    return PageStructure(
                        page_width=pw,
                        page_height=ph,
                        source="dom",
                        elements=dom_elements,
                    ).to_integrated_dict()
                else:
                    logger.info("[LocalVision] DOM 不够丰富，降级到视觉通道")
            except Exception as e:
                logger.warning(f"[LocalVision] DOM 解析失败: {e}")

        # ── 视觉通道 ──────────────────────────────────────────────
        if source_hint in ("auto", "visual") and screenshot_base64 and self._recognizer:
            try:
                page_struct = self._recognizer.recognize_from_base64(screenshot_base64)
                if page_width and page_height:
                    page_struct.page_width = page_width
                    page_struct.page_height = page_height
                logger.info(
                    f"[LocalVision] 视觉通道: {len(page_struct.elements)} 元素, "
                    f"source=visual"
                )
                return page_struct.to_integrated_dict()
            except Exception as e:
                logger.error(f"[LocalVision] 视觉识别失败: {e}")
                return {
                    "page_width": page_width or 1080,
                    "page_height": page_height or 2400,
                    "source": "error",
                    "elements": [],
                    "element_count": 0,
                    "error": str(e),
                }

        # ── 无可用数据 ────────────────────────────────────────────
        return {
            "page_width": page_width or 1080,
            "page_height": page_height or 2400,
            "source": "empty",
            "elements": [],
            "element_count": 0,
        }

    async def find_element_by_llm(
        self,
        target: str,
        page_structure: dict[str, Any],
        llm_callable: Callable[[str], Any] | None = None,
        llm_provider: Any = None,
        skill_hard_rules: str = "",
        operation_history: str = "",
    ) -> dict[str, Any] | None:
        """在结构化页面数据中用 LLM 查找目标元素，返回坐标。

        Args:
            target: 目标元素描述（如"搜索框"、"首页 Tab"）。
            page_structure: ``get_page_structure()`` 返回的结构化页面数据。
            llm_callable: async callable，接受 prompt 字符串，返回 LLM 响应。
            llm_provider: 备选：LLM provider 对象（有 ``chat()`` 方法）。
            skill_hard_rules: 硬性约束文本。
            operation_history: 历史操作记录文本。

        Returns:
            ``{"x": int, "y": int}`` 或 None（未找到）。
        """
        # 将页面结构转为紧凑 LLM 上下文
        elements_json = json.dumps(
            page_structure.get("elements", []),
            ensure_ascii=False,
            indent=2,
        )
        pw = page_structure.get("page_width", 1080)
        ph = page_structure.get("page_height", 2400)
        source = page_structure.get("source", "unknown")

        prompt = f"""你是一个自动化测试助手。当前页面已通过 {source} 通道解析为结构化数据。

## 页面信息
页面尺寸: {pw}x{ph}
页面元素 ({page_structure.get('element_count', 0)} 个):

{elements_json}

## 任务
在以上元素中找到目标: 「{target}」

请分析：
1. 目标是否在当前页面中可见？
2. 如果可见，返回对应元素的坐标（实际像素值）。

## 回复格式（仅 JSON）
{{
  "found": true,
  "element_id": "元素ID",
  "x": 中心点X像素坐标,
  "y": 中心点Y像素坐标,
  "reason": "为什么选择这个元素"
}}

或

{{
  "found": false,
  "reason": "为什么找不到"
}}"""

        if skill_hard_rules:
            prompt += f"\n\n## 硬性约束\n{skill_hard_rules}"

        if operation_history:
            prompt += f"\n\n## 历史操作\n{operation_history}"

        # Call LLM
        try:
            response_text = ""
            if llm_callable is not None:
                response_text = await llm_callable(prompt)
            elif llm_provider is not None:
                from testagent.llm.base import LLMProvider

                provider: LLMProvider = llm_provider  # type: ignore
                response = await provider.chat(
                    system="你是一个自动化测试助手。请始终用 JSON 回复。",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=1024,
                )
                for block in response.content:
                    if block.get("type") == "text":
                        response_text += str(block.get("text", ""))
            else:
                logger.error("[LocalVision] find_element_by_llm: 未提供 LLM callable 或 provider")
                return None
        except Exception as e:
            logger.error(f"[LocalVision] LLM 调用失败: {e}")
            return None

        if not response_text:
            return None

        # 解析 JSON 响应
        try:
            start = response_text.find("{")
            end = response_text.rfind("}")
            if start >= 0 and end > start:
                decision = json.loads(response_text[start:end + 1])
            else:
                return None
        except (json.JSONDecodeError, ValueError):
            return None

        if decision.get("found") and decision.get("x") is not None and decision.get("y") is not None:
            x = int(decision["x"])
            y = int(decision["y"])
            logger.info(
                f"[LocalVision] LLM 选择元素 {decision.get('element_id', '?')} "
                f"-> ({x}, {y}): {decision.get('reason', '')}"
            )
            return {"x": x, "y": y}

        return None

    async def get_page_context_for_llm(self, page_structure: dict[str, Any]) -> str:
        """生成供 LLM 决策用的紧凑文本上下文。

        将 ``PageStructure`` 转为 ``to_llm_context()`` 格式字符串。
        """
        ps = PageStructure(
            page_width=page_structure.get("page_width", 1080),
            page_height=page_structure.get("page_height", 2400),
            source=page_structure.get("source", "unknown"),
        )
        # Rehydrate elements if they exist as dicts
        raw_elements = page_structure.get("elements", [])
        if raw_elements:
            from testagent.vision_local.dom_parser import DomParser

            ps.elements = DomParser.to_visual_elements(raw_elements)
        return ps.to_llm_context()
