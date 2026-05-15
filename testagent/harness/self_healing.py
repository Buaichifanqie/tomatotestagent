from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from testagent.common import get_logger

if TYPE_CHECKING:
    from testagent.llm.base import ILLMProvider

_logger = get_logger(__name__)

_SEMANTIC_ATTRIBUTES = ("aria-label", "placeholder", "title", "data-testid", "name", "role", "alt")


@dataclass
class HealingResult:
    original_selector: str
    healed_selector: str
    healing_level: int
    confidence: float
    steps: list[str] = field(default_factory=list)


class LocatorHealer:
    def __init__(self, llm_provider: ILLMProvider | None = None) -> None:
        self._llm = llm_provider

    async def heal(self, selector: str, page_source: str, error: str) -> HealingResult:
        steps: list[str] = []
        current = selector

        if self._is_xpath(selector):
            steps.append(f"原始选择器已是 XPath: {selector}")
            sematic = await self._try_semantic(current, page_source)
            if sematic:
                steps.append(f"语义定位修复成功: {current} -> {sematic}")
                return HealingResult(
                    original_selector=selector,
                    healed_selector=sematic,
                    healing_level=2,
                    confidence=0.85,
                    steps=steps,
                )
            steps.append("语义定位修复失败，返回原始 XPath")
            return HealingResult(
                original_selector=selector,
                healed_selector=selector,
                healing_level=0,
                confidence=0.0,
                steps=steps,
            )

        xpath = await self.css_to_xpath(selector)
        if xpath and xpath != selector:
            steps.append(f"CSS→XPath 转换成功: {selector} -> {xpath}")
            current = xpath

            sematic = await self._try_semantic(current, page_source)
            if sematic:
                steps.append(f"语义定位修复成功: {xpath} -> {sematic}")
                return HealingResult(
                    original_selector=selector,
                    healed_selector=sematic,
                    healing_level=2,
                    confidence=0.85,
                    steps=steps,
                )

            steps.append("XPath→语义定位未生成，使用 XPath 作为最终定位器")
            return HealingResult(
                original_selector=selector,
                healed_selector=xpath,
                healing_level=1,
                confidence=self._compute_confidence(xpath),
                steps=steps,
            )

        sematic = await self._try_semantic(current, page_source)
        if sematic:
            steps.append(f"直接语义定位修复成功: {selector} -> {sematic}")
            return HealingResult(
                original_selector=selector,
                healed_selector=sematic,
                healing_level=2,
                confidence=0.8,
                steps=steps,
            )

        steps.append("所有降级策略均失败，无法修复定位器")
        return HealingResult(
            original_selector=selector,
            healed_selector=selector,
            healing_level=0,
            confidence=0.0,
            steps=steps,
        )

    async def css_to_xpath(self, css_selector: str) -> str:
        selector = css_selector.strip()
        if not selector:
            return ""

        try:
            xpath = self._css_to_xpath_converter(selector)
            if xpath == selector:
                if not re.search(r"[#\.\[\]:]", selector):
                    _logger.debug(
                        "CSS 已是有效 XPath",
                        extra={"extra_data": {"css": css_selector}},
                    )
                    return selector
                _logger.debug(
                    "CSS→XPath 转换无变化",
                    extra={"extra_data": {"css": css_selector}},
                )
                return ""
            _logger.debug(
                "CSS→XPath 转换",
                extra={"extra_data": {"css": css_selector, "xpath": xpath}},
            )
            return xpath
        except Exception as exc:
            _logger.warning(
                "CSS→XPath 转换失败",
                extra={"extra_data": {"css": css_selector, "error": str(exc)}},
            )
            return ""

    async def xpath_to_semantic(self, xpath: str, page_source: str) -> str:
        if self._llm is None:
            _logger.debug("LLM 未配置，跳过语义定位")
            return ""

        extracted = self._extract_element_context(page_source, text_limit=200)

        system_prompt = (
            "You are a locator healer assistant. Given an XPath selector that failed, "
            "and the page source context, generate a more robust semantic XPath locator. "
            "Use text content, aria-label, placeholder, or other semantic attributes. "
            "Return ONLY the XPath expression, no explanation."
        )

        user_prompt = (
            f"Failed XPath: {xpath}\n\n"
            f"Page context (relevant snippets):\n{extracted}\n\n"
            "Generate a semantic XPath locator using text(), @aria-label, @placeholder, "
            "or similar semantic attributes. Return only the XPath."
        )

        try:
            response = await self._llm.chat(
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=256,
                temperature=0.1,
            )

            raw = ""
            for block in response.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    raw += block.get("text", "")
                elif isinstance(block, str):
                    raw += block

            semantic_xpath = self._clean_llm_xpath(raw)
            if semantic_xpath and semantic_xpath != xpath:
                _logger.info(
                    "XPath→语义定位生成",
                    extra={"extra_data": {"original": xpath, "semantic": semantic_xpath}},
                )
                return semantic_xpath

            return ""
        except Exception as exc:
            _logger.warning(
                "语义定位 LLM 调用失败",
                extra={"extra_data": {"xpath": xpath, "error": str(exc)}},
            )
            return ""

    def build_healing_report(self, original: str, final: str, steps: list[str]) -> dict[str, Any]:
        return {
            "original_selector": original,
            "final_selector": final,
            "steps": steps,
            "healed": original != final,
        }

    def _is_xpath(self, selector: str) -> bool:
        s = selector.strip().lower()
        return s.startswith("//") or s.startswith("(//") or s.startswith("./") or s.startswith("../")

    def _compute_confidence(self, xpath: str) -> float:
        score = 0.7
        semantic_patterns = [
            r"@aria-label",
            r"@placeholder",
            r"@data-testid",
            r"@name",
            r"@title",
            r"@role",
            r"text\(\)",
            r"contains\(",
            r"@alt",
            r"@id\b(?![-])",
        ]
        for pattern in semantic_patterns:
            if re.search(pattern, xpath, re.IGNORECASE):
                score += 0.05

        return min(score, 0.95)

    _VALID_TAG = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.-]*$")

    def _css_to_xpath_converter(self, css: str) -> str:
        css = css.strip()
        if not css:
            return ""

        parts = re.split(r"\s+", css)
        if len(parts) == 1:
            single = self._convert_single(css)
            if single == css:
                if self._VALID_TAG.match(css) or css == "*":
                    return css
                return ""
            return f"//{single}" if single else ""

        xpath_parts: list[str] = []
        for part in parts:
            if part == ">":
                continue
            converted = self._convert_single(part)
            xpath_parts.append(converted)

        result = "//" + "/".join(xpath_parts)
        return result

    def _convert_single(self, part: str) -> str:
        tag = "*"
        ids: list[str] = []
        classes: list[str] = []
        attrs: list[str] = []
        pseudo: str | None = None
        i = 0
        n = len(part)

        while i < n:
            if part[i] == "#":
                j = i + 1
                while j < n and part[j] not in ("#", ".", "[", ":", "*"):
                    j += 1
                ids.append(part[i + 1 : j])
                i = j
            elif part[i] == ".":
                j = i + 1
                while j < n and part[j] not in ("#", ".", "[", ":", "*"):
                    j += 1
                classes.append(part[i + 1 : j])
                i = j
            elif part[i] == "[":
                j = i + 1
                depth = 1
                while j < n and depth > 0:
                    if part[j] == "[":
                        depth += 1
                    elif part[j] == "]":
                        depth -= 1
                    j += 1
                attrs.append(part[i + 1 : j - 1])
                i = j
            elif part[i] == ":":
                pseudo = part[i:]
                break
            elif part[i] == "*":
                tag = "*"
                i += 1
            else:
                j = i
                while j < n and part[j] not in ("#", ".", "[", ":", "*"):
                    j += 1
                tag = part[i:j]
                i = j

        predicates: list[str] = []
        if ids:
            for id_val in ids:
                predicates.append(f"@id='{id_val}'")
        if classes:
            for cls in classes:
                predicates.append(f"contains(concat(' ', normalize-space(@class), ' '), ' {cls} ')")
        for attr_expr in attrs:
            attr_expr = attr_expr.strip()
            if "=" in attr_expr:
                eq_idx = attr_expr.index("=")
                attr_name = attr_expr[:eq_idx].strip()
                rest = attr_expr[eq_idx + 1 :].strip()
                if rest and rest[0] in ('"', "'"):
                    end = rest.index(rest[0], 1) if rest[0] in rest[1:] else len(rest)
                    attr_val = rest[1:end]
                    predicates.append(f"@{attr_name}='{attr_val}'")
                else:
                    predicates.append(f"@{attr_name}='{rest}'")
            else:
                predicates.append(f"@{attr_expr}")

        if pseudo and pseudo.startswith(":nth-child("):
            idx_match = re.search(r":nth-child\((\d+)\)", pseudo)
            if idx_match:
                predicates.append(f"position()={idx_match.group(1)}")

        if predicates:
            return f"{tag}[{' and '.join(predicates)}]"
        return tag

    async def _try_semantic(self, selector: str, page_source: str) -> str:
        if not self._is_xpath(selector):
            return ""
        result = await self.xpath_to_semantic(selector, page_source)
        if result and result != selector:
            return result
        return ""

    def _extract_element_context(self, page_source: str, text_limit: int = 200) -> str:
        snippets: list[str] = []

        for attr in _SEMANTIC_ATTRIBUTES:
            pattern = re.compile(
                rf'<[a-z0-9]+[^>]*?\s{re.escape(attr)}=["\']([^"\']+)["\'][^>]*?>',
                re.IGNORECASE,
            )
            for match in pattern.finditer(page_source):
                tag_end = page_source.find(">", match.start()) + 1
                snippet = page_source[match.start() : tag_end]
                snippets.append(snippet)

        label_pattern = re.compile(
            r"<label[^>]*?>([^<]{1,100})</label>",
            re.IGNORECASE,
        )
        for match in label_pattern.finditer(page_source):
            snippets.append(f"<label>{match.group(1)}</label>")

        button_pattern = re.compile(
            r"<button[^>]*?>([^<]{1,50})</button>",
            re.IGNORECASE,
        )
        for match in button_pattern.finditer(page_source):
            snippets.append(f"<button>{match.group(1)}</button>")

        combined = "\n".join(snippets)
        if len(combined) > text_limit:
            combined = combined[:text_limit] + "..."
        return combined or "(no semantic attributes found in page source)"

    def _clean_llm_xpath(self, raw: str) -> str:
        raw = raw.strip().strip("`").strip()
        lines = raw.split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("//") or line.startswith("(//"):
                return line
        return ""
