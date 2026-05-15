from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.harness.self_healing import HealingResult, LocatorHealer
from testagent.mcp_servers.playwright_server.server import PlaywrightMCPServer
from testagent.mcp_servers.playwright_server.tools import (
    _execute_assertion,
    _try_heal_and_retry,
    browser_assert,
    browser_click,
    browser_type,
)


@pytest.fixture()
def healer() -> LocatorHealer:
    return LocatorHealer()


@pytest.fixture()
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=MagicMock(
            content=[{"type": "text", "text": "//button[@aria-label='Submit']"}],
            stop_reason="end_turn",
            usage={},
        )
    )
    return llm


class TestHealingResult:
    def test_healing_result_dataclass(self) -> None:
        result = HealingResult(
            original_selector="#btn",
            healed_selector="//button[@id='btn']",
            healing_level=1,
            confidence=0.85,
            steps=["CSS→XPath 转换成功"],
        )
        assert result.original_selector == "#btn"
        assert result.healed_selector == "//button[@id='btn']"
        assert result.healing_level == 1
        assert result.confidence == 0.85
        assert len(result.steps) == 1

    def test_healing_result_default_steps(self) -> None:
        result = HealingResult(
            original_selector="#btn",
            healed_selector="#btn",
            healing_level=0,
            confidence=0.0,
        )
        assert result.steps == []


class TestCssToXpath:
    @pytest.mark.asyncio
    async def test_id_selector(self, healer: LocatorHealer) -> None:
        result = await healer.css_to_xpath("#submit-btn")
        assert result == "//*[@id='submit-btn']"

    @pytest.mark.asyncio
    async def test_class_selector(self, healer: LocatorHealer) -> None:
        result = await healer.css_to_xpath(".btn-primary")
        assert "contains(concat(' ', normalize-space(@class), ' '), ' btn-primary ')" in result

    @pytest.mark.asyncio
    async def test_tag_selector(self, healer: LocatorHealer) -> None:
        result = await healer.css_to_xpath("button")
        assert result == "button"

    @pytest.mark.asyncio
    async def test_tag_with_id(self, healer: LocatorHealer) -> None:
        result = await healer.css_to_xpath("button#submit")
        assert result == "//button[@id='submit']"

    @pytest.mark.asyncio
    async def test_complex_selector(self, healer: LocatorHealer) -> None:
        result = await healer.css_to_xpath("div.form-group input#email")
        assert "//" in result
        assert "div" in result
        assert "input" in result

    @pytest.mark.asyncio
    async def test_attribute_selector(self, healer: LocatorHealer) -> None:
        result = await healer.css_to_xpath('[data-testid="login-btn"]')
        assert result == "//*[@data-testid='login-btn']"

    @pytest.mark.asyncio
    async def test_empty_selector(self, healer: LocatorHealer) -> None:
        result = await healer.css_to_xpath("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_nth_child_selector(self, healer: LocatorHealer) -> None:
        result = await healer.css_to_xpath("ul li:nth-child(3)")
        assert "position()=3" in result

    @pytest.mark.asyncio
    async def test_descendant_selector(self, healer: LocatorHealer) -> None:
        result = await healer.css_to_xpath("nav ul li a")
        assert result == "//nav/ul/li/a"

    @pytest.mark.asyncio
    async def test_wildcard_selector(self, healer: LocatorHealer) -> None:
        result = await healer.css_to_xpath("*")
        assert result == "*"


class TestXpathToSemantic:
    @pytest.mark.asyncio
    async def test_semantic_with_llm(self, healer: LocatorHealer, mock_llm: MagicMock) -> None:
        healer_with_llm = LocatorHealer(llm_provider=mock_llm)
        result = await healer_with_llm.xpath_to_semantic(
            "//div[1]/button[1]",
            '<html><body><button aria-label="Submit">Submit</button></body></html>',
        )
        assert result == "//button[@aria-label='Submit']"
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_semantic_without_llm_returns_empty(self, healer: LocatorHealer) -> None:
        result = await healer.xpath_to_semantic(
            "//div[1]/button[1]",
            "<html></html>",
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_semantic_llm_returns_same_xpath(self, healer: LocatorHealer) -> None:
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=MagicMock(
                content=[{"type": "text", "text": "//div[1]/button[1]"}],
                stop_reason="end_turn",
                usage={},
            )
        )
        healer_with_llm = LocatorHealer(llm_provider=llm)
        result = await healer_with_llm.xpath_to_semantic(
            "//div[1]/button[1]",
            "<html></html>",
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_semantic_llm_returns_code_block(self, healer: LocatorHealer) -> None:
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=MagicMock(
                content=[{"type": "text", "text": "```\n//button[@id='submit']\n```"}],
                stop_reason="end_turn",
                usage={},
            )
        )
        healer_with_llm = LocatorHealer(llm_provider=llm)
        result = await healer_with_llm.xpath_to_semantic("//div/button", "<html></html>")
        assert result == "//button[@id='submit']"

    @pytest.mark.asyncio
    async def test_semantic_llm_call_failure(self, healer: LocatorHealer) -> None:
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=Exception("API error"))
        healer_with_llm = LocatorHealer(llm_provider=llm)
        result = await healer_with_llm.xpath_to_semantic("//div/button", "<html></html>")
        assert result == ""


class TestContextExtraction:
    def test_extract_aria_label(self, healer: LocatorHealer) -> None:
        page_source = '<button aria-label="Close">X</button>'
        result = healer._extract_element_context(page_source, text_limit=500)
        assert "aria-label" in result

    def test_extract_placeholder(self, healer: LocatorHealer) -> None:
        page_source = '<input placeholder="Enter email">'
        result = healer._extract_element_context(page_source, text_limit=500)
        assert "placeholder" in result

    def test_extract_label_text(self, healer: LocatorHealer) -> None:
        page_source = "<label>Email Address</label>"
        result = healer._extract_element_context(page_source, text_limit=500)
        assert "Email Address" in result

    def test_extract_button_text(self, healer: LocatorHealer) -> None:
        page_source = "<button>Login</button>"
        result = healer._extract_element_context(page_source, text_limit=500)
        assert "Login" in result

    def test_extract_text_limit(self, healer: LocatorHealer) -> None:
        page_source = '<button aria-label="A">B</button>' + "<label>" + "x" * 500 + "</label>"
        result = healer._extract_element_context(page_source, text_limit=100)
        assert len(result) <= 103

    def test_empty_page_source(self, healer: LocatorHealer) -> None:
        result = healer._extract_element_context("", text_limit=200)
        assert "(no semantic attributes found in page source)" in result


class TestConfidenceScoring:
    def test_baseline_confidence(self, healer: LocatorHealer) -> None:
        score = healer._compute_confidence("//button")
        assert score == 0.7

    def test_semantic_attribute_confidence(self, healer: LocatorHealer) -> None:
        score = healer._compute_confidence("//button[@aria-label='Submit']")
        assert score == 0.75

    def test_id_based_confidence(self, healer: LocatorHealer) -> None:
        score = healer._compute_confidence("//button[@id='submit']")
        assert score == 0.75

    def test_multiple_semantic_boosts(self, healer: LocatorHealer) -> None:
        score = healer._compute_confidence("//button[@aria-label='Submit' and @data-testid='submit-btn']")
        assert score == 0.8

    def test_confidence_capped(self, healer: LocatorHealer) -> None:
        score = healer._compute_confidence(
            "//button[@aria-label='A' and @placeholder='B' and @data-testid='C' "
            "and @name='D' and @title='E' and contains(text(), 'Submit')]"
        )
        assert score <= 0.95

    def test_text_contains_boost(self, healer: LocatorHealer) -> None:
        score = healer._compute_confidence("//button[contains(text(), 'Login')]")
        assert score == 0.8


class TestHealingFlow:
    @pytest.mark.asyncio
    async def test_css_to_xpath_healing_level_1(self, healer: LocatorHealer) -> None:
        result = await healer.heal(
            "#submit-btn",
            "<html><body><button id='submit-btn'>Submit</button></body></html>",
            "Element not found",
        )
        assert result.healing_level == 1
        assert "//*[@id='submit-btn']" in result.healed_selector
        assert result.confidence >= 0.7

    @pytest.mark.asyncio
    async def test_css_to_xpath_to_semantic_healing_level_2(self, healer: LocatorHealer, mock_llm: MagicMock) -> None:
        healer_with_llm = LocatorHealer(llm_provider=mock_llm)
        result = await healer_with_llm.heal(
            "#login-btn",
            '<html><body><button aria-label="Submit">Submit</button></body></html>',
            "Element not found",
        )
        assert result.healing_level == 2
        assert result.healed_selector != "#login-btn"
        assert result.confidence == 0.85
        assert len(result.steps) >= 2

    @pytest.mark.asyncio
    async def test_xpath_input_skips_level_1(self, healer: LocatorHealer) -> None:
        result = await healer.heal(
            "//div[1]/button[1]",
            "<html></html>",
            "Element not found",
        )
        assert result.healing_level == 0

    @pytest.mark.asyncio
    async def test_xpath_with_semantic_heals_to_level_2(self, healer: LocatorHealer, mock_llm: MagicMock) -> None:
        healer_with_llm = LocatorHealer(llm_provider=mock_llm)
        result = await healer_with_llm.heal(
            "//div[1]/button[1]",
            '<html><body><button aria-label="Submit">Submit</button></body></html>',
            "Element not found",
        )
        assert result.healing_level == 2
        assert result.healed_selector == "//button[@aria-label='Submit']"

    @pytest.mark.asyncio
    async def test_no_healing_possible(self, healer: LocatorHealer) -> None:
        result = await healer.heal(
            "invalid-selector!!!",
            "<html></html>",
            "Element not found",
        )
        assert result.healing_level == 0
        assert result.healed_selector == "invalid-selector!!!"
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_css_fallback_to_direct_semantic(self, healer: LocatorHealer, mock_llm: MagicMock) -> None:
        healer_with_llm = LocatorHealer(llm_provider=mock_llm)
        result = await healer_with_llm.heal(
            "nonexistent",
            '<html><body><button aria-label="Submit">Submit</button></body></html>',
            "Element not found",
        )
        assert result.healing_level == 0
        assert result.healed_selector == "nonexistent"


class TestHealingReport:
    def test_report_healed(self, healer: LocatorHealer) -> None:
        report = healer.build_healing_report(
            "#btn",
            "//button[@id='btn']",
            ["CSS→XPath 转换成功"],
        )
        assert report["original_selector"] == "#btn"
        assert report["final_selector"] == "//button[@id='btn']"
        assert report["healed"] is True
        assert len(report["steps"]) == 1

    def test_report_not_healed(self, healer: LocatorHealer) -> None:
        report = healer.build_healing_report(
            "#btn",
            "#btn",
            ["无法修复"],
        )
        assert report["healed"] is False

    def test_report_empty_steps(self, healer: LocatorHealer) -> None:
        report = healer.build_healing_report("#btn", "#btn", [])
        assert report["steps"] == []


class TestIsXPath:
    def test_absolute_xpath(self, healer: LocatorHealer) -> None:
        assert healer._is_xpath("//div[@id='main']")

    def test_relative_xpath(self, healer: LocatorHealer) -> None:
        assert healer._is_xpath("./div/button")

    def test_parent_xpath(self, healer: LocatorHealer) -> None:
        assert healer._is_xpath("../div")

    def test_grouped_xpath(self, healer: LocatorHealer) -> None:
        assert healer._is_xpath("(//div)[1]")

    def test_css_selector_not_xpath(self, healer: LocatorHealer) -> None:
        assert not healer._is_xpath("#main")
        assert not healer._is_xpath(".btn")
        assert not healer._is_xpath("button")


class TestCleanLLMXPath:
    def test_clean_simple_xpath(self, healer: LocatorHealer) -> None:
        result = healer._clean_llm_xpath("//button[@id='submit']")
        assert result == "//button[@id='submit']"

    def test_clean_code_block(self, healer: LocatorHealer) -> None:
        result = healer._clean_llm_xpath("```\n//button[@id='submit']\n```")
        assert result == "//button[@id='submit']"

    def test_clean_with_explanation(self, healer: LocatorHealer) -> None:
        result = healer._clean_llm_xpath("Here is the XPath:\n//button[@id='submit']")
        assert result == "//button[@id='submit']"

    def test_clean_no_xpath(self, healer: LocatorHealer) -> None:
        result = healer._clean_llm_xpath("No valid XPath found")
        assert result == ""

    def test_clean_grouped_xpath(self, healer: LocatorHealer) -> None:
        result = healer._clean_llm_xpath("(//button)[1]")
        assert result == "(//button)[1]"


class TestIntegrationWithPlaywrightServer:
    @pytest.mark.asyncio
    async def test_server_creates_healer(self) -> None:
        server = PlaywrightMCPServer()
        assert server._healer is not None
        assert isinstance(server._healer, LocatorHealer)

    @pytest.mark.asyncio
    async def test_server_injects_healer_into_click(self) -> None:
        mock_page = MagicMock()
        mock_page.click = AsyncMock(side_effect=[Exception("Timeout"), None])
        mock_page.content = AsyncMock(return_value='<html><body><button id="btn">Click</button></body></html>')

        server = PlaywrightMCPServer()
        server._page = mock_page

        raw_result = await server.call_tool("browser_click", {"selector": "#btn"})
        result = json.loads(str(raw_result))
        assert "self_healing" in result or "clicked" in result

    @pytest.mark.asyncio
    async def test_server_injects_healer_into_type(self) -> None:
        mock_page = MagicMock()
        mock_page.fill = AsyncMock(side_effect=[Exception("Timeout"), None])
        mock_page.type = AsyncMock()
        mock_page.content = AsyncMock(return_value='<html><body><input id="email"></body></html>')

        server = PlaywrightMCPServer()
        server._page = mock_page

        raw_result = await server.call_tool("browser_type", {"selector": "#email", "text": "test@test.com"})
        result = json.loads(str(raw_result))
        assert "typed" in result or "self_healing" in result

    @pytest.mark.asyncio
    async def test_server_injects_healer_into_assert(self) -> None:
        mock_page = MagicMock()
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.content = AsyncMock(return_value='<html><body><button id="btn">Click</button></body></html>')

        server = PlaywrightMCPServer()
        server._page = mock_page

        raw_result = await server.call_tool("browser_assert", {"selector": "#nonexistent", "assertion": "visible"})
        result = json.loads(str(raw_result))
        assert "passed" in result


class TestBrowserClickWithHealer:
    @pytest.mark.asyncio
    async def test_click_without_page_returns_error(self) -> None:
        result = await browser_click("#btn")
        assert "error" in result
        assert result["error"] == "Browser not initialized"

    @pytest.mark.asyncio
    async def test_click_calls_page_click(self) -> None:
        mock_page = MagicMock()
        mock_page.click = AsyncMock()

        result = await browser_click("#submit", page=mock_page)
        assert result["clicked"] is True
        mock_page.click.assert_called_once_with("#submit", button="left")

    @pytest.mark.asyncio
    async def test_click_self_heals_on_failure(self) -> None:
        mock_page = MagicMock()
        mock_page.click = AsyncMock(side_effect=[Exception("Timeout"), None])
        mock_page.content = AsyncMock(return_value='<html><body><button id="btn">Click</button></body></html>')

        healer = LocatorHealer()
        result = await browser_click("#btn", page=mock_page, healer=healer)
        assert result["clicked"] is True
        assert "self_healing" in result
        assert result["self_healing"]["healed"] is True

    @pytest.mark.asyncio
    async def test_click_self_heal_fails_raises_exception(self) -> None:
        mock_page = MagicMock()
        mock_page.click = AsyncMock(side_effect=RuntimeError("Timeout"))
        mock_page.content = AsyncMock(return_value="<html></html>")

        healer = LocatorHealer()
        with pytest.raises(RuntimeError):
            await browser_click("#btn", page=mock_page, healer=healer)

    @pytest.mark.asyncio
    async def test_click_calls_on_heal_callback(self) -> None:
        mock_page = MagicMock()
        mock_page.click = AsyncMock(side_effect=[Exception("Timeout"), None])
        mock_page.content = AsyncMock(return_value='<html><body><button id="btn">Click</button></body></html>')

        callback = MagicMock()
        healer = LocatorHealer()
        result = await browser_click("#btn", page=mock_page, healer=healer, on_heal=callback)
        assert result["clicked"] is True
        callback.assert_called_once()


class TestBrowserTypeWithHealer:
    @pytest.mark.asyncio
    async def test_type_without_page_returns_error(self) -> None:
        result = await browser_type("#input", "hello")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_type_self_heals_on_failure(self) -> None:
        mock_page = MagicMock()
        mock_page.fill = AsyncMock(side_effect=[Exception("Timeout"), None])
        mock_page.type = AsyncMock()
        mock_page.content = AsyncMock(return_value='<html><body><input id="email"></body></html>')

        healer = LocatorHealer()
        result = await browser_type("#email", "test@test.com", page=mock_page, healer=healer)
        assert result["typed"] is True
        assert "self_healing" in result


class TestBrowserAssertWithHealer:
    @pytest.mark.asyncio
    async def test_assert_visible_self_heals_on_failure(self) -> None:
        mock_page = MagicMock()
        mock_page.wait_for_selector = AsyncMock(side_effect=Exception("Timeout"))
        mock_page.content = AsyncMock(return_value='<html><body><button id="btn">Click</button></body></html>')

        healer = LocatorHealer()
        result = await browser_assert("#missing-btn", "visible", page=mock_page, healer=healer)
        assert "passed" in result

    @pytest.mark.asyncio
    async def test_assert_text_self_heals(self) -> None:
        mock_element = MagicMock()
        mock_element.inner_text = AsyncMock(return_value="Actual Text")

        mock_page = MagicMock()
        mock_page.query_selector = AsyncMock(side_effect=[None, mock_element])
        mock_page.content = AsyncMock(return_value='<html><body><button id="btn">Actual Text</button></body></html>')

        healer = LocatorHealer()
        result = await browser_assert("#missing-btn", "text", expected="Actual Text", page=mock_page, healer=healer)
        assert "passed" in result


class TestTryHealAndRetry:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_healer(self) -> None:
        result = await _try_heal_and_retry("browser_click", "#btn", {}, MagicMock(), None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_fix(self) -> None:
        mock_page = MagicMock()
        mock_page.content = AsyncMock(return_value="<html></html>")

        healer = LocatorHealer()
        result = await _try_heal_and_retry("browser_click", "invalid!!", {}, mock_page, healer)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_heal_info_on_success(self) -> None:
        mock_page = MagicMock()
        mock_page.content = AsyncMock(return_value='<html><body><button id="btn">Click</button></body></html>')

        healer = LocatorHealer()
        result = await _try_heal_and_retry("browser_click", "#btn", {}, mock_page, healer)
        assert result is not None
        assert result["healed"] is True
        assert result["original_selector"] == "#btn"
        assert "healed_selector" in result
        assert result["healing_level"] >= 1
        assert "confidence" in result
        assert "steps" in result


class TestExecuteAssertion:
    @pytest.mark.asyncio
    async def test_visible_passes(self) -> None:
        mock_page = MagicMock()
        mock_page.wait_for_selector = AsyncMock()

        result = await _execute_assertion("visible", "#btn", None, mock_page)
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_visible_fails(self) -> None:
        mock_page = MagicMock()
        mock_page.wait_for_selector = AsyncMock(side_effect=Exception("Timeout"))

        result = await _execute_assertion("visible", "#btn", None, mock_page)
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_exists_element_found(self) -> None:
        mock_page = MagicMock()
        mock_page.query_selector = AsyncMock(return_value=MagicMock())

        result = await _execute_assertion("exists", "#btn", None, mock_page)
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_exists_element_not_found(self) -> None:
        mock_page = MagicMock()
        mock_page.query_selector = AsyncMock(return_value=None)

        result = await _execute_assertion("exists", "#btn", None, mock_page)
        assert result["passed"] is False

    @pytest.mark.asyncio
    async def test_text_matches(self) -> None:
        mock_element = MagicMock()
        mock_element.inner_text = AsyncMock(return_value="Hello")
        mock_page = MagicMock()
        mock_page.query_selector = AsyncMock(return_value=mock_element)

        result = await _execute_assertion("text", "#btn", "Hello", mock_page)
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_url_assertion(self) -> None:
        mock_page = MagicMock()
        mock_page.url = "https://example.com/dashboard"

        result = await _execute_assertion("url", ".el", "dashboard", mock_page)
        assert result["passed"] is True
