from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from testagent.common.logging import get_logger
from testagent.mcp_servers.appium_server.tools import app_get_source
from testagent.rule_engine.context_manager import ContextManager

logger = get_logger(__name__)


class UIExtractor:
    """Extract values from UI using three-layer fallback.

    MVP: DOM extraction only.
    V1.1+: OCR (Layer 2) and VLM (Layer 3) will be added.
    """

    def __init__(self, appium_url: str, session_id: str) -> None:
        self._appium_url = appium_url
        self._session_id = session_id

    async def extract(self, config: dict[str, Any], context: ContextManager) -> Any:
        """Extract a single value from UI.

        Args:
            config: Dict with 'semantic', optional 'locator', optional 'transform'.
            context: Current context for variable resolution.

        Returns:
            Extracted text value, or None if not found.
        """
        # Layer 1: DOM extraction
        result = await self._extract_from_dom(config)
        if result is not None:
            # Apply transform if specified
            transform = config.get("transform")
            if transform:
                result = self._apply_transform(result, transform)
            return result

        # Layer 2: OCR (deferred to V1.1+)
        logger.info(f"DOM extraction failed for '{config.get('semantic', '?')}', OCR not yet implemented")

        # Layer 3: VLM (deferred to V1.1+)
        return None

    async def extract_collection(self, config: dict[str, Any], context: ContextManager) -> list[str]:
        """Extract all text items from a list/collection element.

        Returns:
            List of text strings, or empty list if not found.
        """
        source = await self._get_page_source()
        if not source:
            return []

        locator = config.get("locator", {})
        resource_id = locator.get("resource_id", "")

        try:
            root = ET.fromstring(source)
        except ET.ParseError:
            return []

        # Find the parent container
        if resource_id:
            container = root.find(f".//*[@resource-id='{resource_id}']")
        else:
            container = root

        if container is None:
            return []

        # Extract all child text nodes
        texts = []
        for node in container.iter("node"):
            text = node.get("text", "").strip()
            if text:
                texts.append(text)

        return texts

    async def _extract_from_dom(self, config: dict[str, Any]) -> str | None:
        """Extract text from DOM using locator."""
        source = await self._get_page_source()
        if not source:
            return None

        locator = config.get("locator", {})
        resource_id = locator.get("resource_id", "")
        text_match = locator.get("text", "")

        try:
            root = ET.fromstring(source)
        except ET.ParseError:
            return None

        # Try resource-id first
        if resource_id:
            node = root.find(f".//*[@resource-id='{resource_id}']")
            if node is not None:
                return node.get("text", "").strip() or None

        # Try text content match
        if text_match:
            for node in root.iter("node"):
                if node.get("text", "") == text_match:
                    return text_match

        # Try semantic matching (find element near text label)
        semantic = config.get("semantic", "")
        if semantic:
            return self._semantic_extract(root, semantic)

        return None

    @staticmethod
    def _semantic_extract(root: ET.Element, semantic: str) -> str | None:
        """Try to find a value element near a label matching the semantic text.

        Strategy: find a node whose text contains the semantic keyword,
        then look for a sibling or nearby node with a numeric/currency value.
        """
        # Find the label node
        label_node = None
        for node in root.iter("node"):
            node_text = node.get("text", "")
            if semantic in node_text:
                label_node = node
                break

        if label_node is None:
            return None

        # Look for value in siblings (same parent)
        parent = None
        for p in root.iter("node"):
            for child in p:
                if child is label_node:
                    parent = p
                    break

        if parent is not None:
            for child in parent:
                if child is label_node:
                    continue
                text = child.get("text", "").strip()
                # Look for numeric/currency values
                if text and re.search(r"[\d¥$€£]", text):
                    return text

        # Fallback: return the label text itself if it contains a value
        label_text = label_node.get("text", "").strip()
        if re.search(r"[\d¥$€£]", label_text):
            return label_text

        return None

    async def _get_page_source(self) -> str | None:
        """Get current page source from Appium."""
        try:
            result = await app_get_source(
                appium_url=self._appium_url,
                session_id=self._session_id,
            )
            return result.get("source", "")
        except Exception as e:
            logger.warning(f"Failed to get page source: {e}")
            return None

    @staticmethod
    def _apply_transform(value: str, transform: str) -> str:
        """Apply a simple string transform."""
        if transform == "strip_currency":
            return re.sub(r"[¥$€£,]", "", value).strip()
        return value
