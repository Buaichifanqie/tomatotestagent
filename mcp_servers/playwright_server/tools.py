from __future__ import annotations

import base64
import re
from typing import Any


async def browser_navigate(
    url: str,
    wait_until: str = "load",
    *,
    page: Any = None,
) -> dict[str, object]:
    if page is None:
        return {"error": "Browser not initialized"}
    response = await page.goto(url, wait_until=wait_until)
    return {
        "url": page.url,
        "title": await page.title(),
        "status_code": response.status if response else None,
    }


async def browser_click(
    selector: str,
    button: str = "left",
    *,
    page: Any = None,
) -> dict[str, object]:
    if page is None:
        return {"error": "Browser not initialized"}
    await page.click(selector, button=button)
    return {
        "selector": selector,
        "clicked": True,
    }


async def browser_type(
    selector: str,
    text: str,
    clear: bool = True,
    *,
    page: Any = None,
) -> dict[str, object]:
    if page is None:
        return {"error": "Browser not initialized"}
    if clear:
        await page.fill(selector, "")
    await page.type(selector, text)
    return {
        "selector": selector,
        "typed": True,
        "text": text,
    }


async def browser_screenshot(
    selector: str | None = None,
    full_page: bool = False,
    *,
    page: Any = None,
) -> dict[str, object]:
    if page is None:
        return {"error": "Browser not initialized"}
    if selector:
        element = await page.query_selector(selector)
        if element is None:
            return {"error": f"Element not found: {selector}"}
        screenshot_bytes = await element.screenshot()
    else:
        screenshot_bytes = await page.screenshot(full_page=full_page)
    return {
        "screenshot_base64": base64.b64encode(screenshot_bytes).decode("utf-8"),
        "format": "png",
        "full_page": full_page if not selector else False,
    }


async def browser_assert(
    selector: str,
    assertion: str,
    expected: str | None = None,
    *,
    page: Any = None,
) -> dict[str, object]:
    if page is None:
        return {"error": "Browser not initialized"}

    valid_assertions = {
        "visible",
        "hidden",
        "enabled",
        "disabled",
        "exists",
        "text",
        "value",
        "attribute",
        "count",
        "url",
        "title",
    }
    if assertion not in valid_assertions:
        return {"error": f"Unknown assertion: {assertion}. Valid: {', '.join(sorted(valid_assertions))}"}

    if assertion in {"visible", "hidden", "enabled", "disabled"}:
        try:
            if assertion == "visible":
                await page.wait_for_selector(selector, state="visible", timeout=5000)
                passed = True
            elif assertion == "hidden":
                await page.wait_for_selector(selector, state="hidden", timeout=5000)
                passed = True
            elif assertion == "enabled":
                await page.wait_for_selector(selector, state="visible", timeout=5000)
                passed = await page.is_enabled(selector)
            elif assertion == "disabled":
                await page.wait_for_selector(selector, state="visible", timeout=5000)
                passed = not await page.is_enabled(selector)
            return {"assertion": assertion, "passed": passed, "selector": selector}
        except Exception:
            return {"assertion": assertion, "passed": False, "selector": selector}

    if assertion == "exists":
        element = await page.query_selector(selector)
        return {"assertion": assertion, "passed": element is not None, "selector": selector}

    if assertion in {"text", "value"}:
        element = await page.query_selector(selector)
        if element is None:
            return {"assertion": assertion, "passed": False, "selector": selector, "expected": expected}
        actual = await element.inner_text() if assertion == "text" else await element.input_value()
        return {
            "assertion": assertion,
            "passed": actual == expected,
            "selector": selector,
            "actual": actual,
            "expected": expected,
        }

    if assertion == "attribute":
        if expected is None:
            return {"error": "assertion 'attribute' requires expected parameter in format 'name=value'"}
        parts = expected.split("=", 1)
        attr_name = parts[0]
        attr_value = parts[1] if len(parts) > 1 else ""
        element = await page.query_selector(selector)
        if element is None:
            return {"assertion": assertion, "passed": False, "selector": selector, "expected": expected}
        actual = await element.get_attribute(attr_name)
        return {
            "assertion": assertion,
            "passed": actual == attr_value,
            "selector": selector,
            "actual": actual,
            "expected": attr_value,
        }

    if assertion == "count":
        elements = await page.query_selector_all(selector)
        actual_count = len(elements)
        expected_count = int(expected) if expected else 1
        return {
            "assertion": assertion,
            "passed": actual_count == expected_count,
            "selector": selector,
            "actual": actual_count,
            "expected": expected_count,
        }

    if assertion == "url":
        current_url = page.url
        if expected:
            return {
                "assertion": assertion,
                "passed": re.search(expected, current_url) is not None,
                "selector": selector,
                "actual": current_url,
                "expected": expected,
            }
        return {"assertion": assertion, "passed": True, "selector": selector, "actual": current_url}

    if assertion == "title":
        current_title = await page.title()
        if expected:
            return {
                "assertion": assertion,
                "passed": re.search(expected, current_title) is not None,
                "selector": selector,
                "actual": current_title,
                "expected": expected,
            }
        return {"assertion": assertion, "passed": True, "selector": selector, "actual": current_title}

    return {"error": f"Unhandled assertion: {assertion}"}


async def browser_get_console(
    *,
    console_messages: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {"console_messages": list(console_messages) if console_messages else []}


async def browser_get_network(
    url_pattern: str | None = None,
    *,
    network_requests: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    requests = list(network_requests) if network_requests else []
    if url_pattern:
        try:
            pattern = re.compile(url_pattern)
            requests = [r for r in requests if pattern.search(str(r.get("url", "")))]
        except re.error:
            return {"error": f"Invalid regex pattern: {url_pattern}", "requests": []}
    return {"requests": requests}
