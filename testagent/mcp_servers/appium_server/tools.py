from __future__ import annotations

from typing import Any

import httpx

_VALID_STRATEGIES = {"accessibility_id", "uiautomator", "xpath"}
_VALID_ASSERTIONS = {"visible", "text", "attribute"}


def _build_selector(strategy: str, selector: str) -> dict[str, str]:
    if strategy == "accessibility_id":
        return {"strategy": "accessibility id", "selector": selector}
    if strategy == "uiautomator":
        return {"strategy": "-android uiautomator", "selector": selector}
    if strategy == "xpath":
        return {"strategy": "xpath", "selector": selector}
    return {"strategy": strategy, "selector": selector}


async def _appium_post(
    appium_url: str,
    path: str,
    payload: dict[str, object] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.post(f"{appium_url}{path}", json=payload or {})
    try:
        body: dict[str, Any] = response.json()
    except Exception:
        body = {"raw": response.text}
    return {"status_code": response.status_code, "body": body}


async def _find_element(
    appium_url: str,
    strategy: str,
    selector: str,
    timeout: int = 10,
) -> dict[str, Any]:
    payload: dict[str, object] = {
        "strategy": strategy,
        "selector": selector,
        "timeout": timeout,
    }
    return await _appium_post(appium_url, "/session/:sessionId/element", payload)


async def app_install(
    app_path: str,
    appium_url: str = "http://localhost:4723",
) -> dict[str, Any]:
    payload: dict[str, object] = {"appPath": app_path}
    return await _appium_post(appium_url, "/session/:sessionId/app/install", payload)


async def app_tap(
    selector: str,
    strategy: str = "accessibility_id",
    appium_url: str = "http://localhost:4723",
) -> dict[str, Any]:
    if strategy not in _VALID_STRATEGIES:
        return {"error": f"Invalid strategy '{strategy}'. Must be one of {sorted(_VALID_STRATEGIES)}"}
    find_result = await _find_element(appium_url, strategy, selector)
    if find_result["status_code"] != 200:
        return {"error": f"Element not found: {find_result['body']}", "status_code": find_result["status_code"]}
    element_id = find_result["body"].get("ELEMENT") or find_result["body"].get("elementId")
    if not element_id:
        value = find_result["body"].get("value", {})
        if isinstance(value, dict):
            element_id = value.get("ELEMENT") or value.get("elementId")
    if not element_id:
        return {"error": "Element found but no element ID returned", "find_result": find_result["body"]}
    payload: dict[str, object] = {"element": element_id}
    return await _appium_post(appium_url, "/session/:sessionId/element/" + element_id + "/click", payload)


async def app_swipe(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: int = 500,
    appium_url: str = "http://localhost:4723",
) -> dict[str, Any]:
    payload: dict[str, object] = {
        "actions": [
            {"type": "pointerMove", "duration": 0, "x": start_x, "y": start_y},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": duration},
            {"type": "pointerMove", "duration": duration, "x": end_x, "y": end_y},
            {"type": "pointerUp", "button": 0},
        ],
    }
    return await _appium_post(appium_url, "/session/:sessionId/actions", payload)


async def app_type(
    selector: str,
    text: str,
    strategy: str = "accessibility_id",
    appium_url: str = "http://localhost:4723",
) -> dict[str, Any]:
    if strategy not in _VALID_STRATEGIES:
        return {"error": f"Invalid strategy '{strategy}'. Must be one of {sorted(_VALID_STRATEGIES)}"}
    find_result = await _find_element(appium_url, strategy, selector)
    if find_result["status_code"] != 200:
        return {"error": f"Element not found: {find_result['body']}", "status_code": find_result["status_code"]}
    element_id = find_result["body"].get("ELEMENT") or find_result["body"].get("elementId")
    if not element_id:
        value = find_result["body"].get("value", {})
        if isinstance(value, dict):
            element_id = value.get("ELEMENT") or value.get("elementId")
    if not element_id:
        return {"error": "Element found but no element ID returned", "find_result": find_result["body"]}
    payload: dict[str, object] = {"element": element_id, "text": text}
    return await _appium_post(appium_url, "/session/:sessionId/element/" + element_id + "/value", payload)


async def app_assert_element(
    selector: str,
    assertion: str,
    expected: str | None = None,
    strategy: str = "accessibility_id",
    appium_url: str = "http://localhost:4723",
) -> dict[str, Any]:
    if strategy not in _VALID_STRATEGIES:
        return {"error": f"Invalid strategy '{strategy}'. Must be one of {sorted(_VALID_STRATEGIES)}"}
    if assertion not in _VALID_ASSERTIONS:
        return {"error": f"Invalid assertion '{assertion}'. Must be one of {sorted(_VALID_ASSERTIONS)}"}
    find_result = await _find_element(appium_url, strategy, selector)
    if find_result["status_code"] != 200:
        if assertion == "visible":
            return {"passed": False, "reason": f"Element not found: {find_result['body']}"}
        return {"error": f"Element not found: {find_result['body']}", "status_code": find_result["status_code"]}
    element_id = find_result["body"].get("ELEMENT") or find_result["body"].get("elementId")
    if not element_id:
        value = find_result["body"].get("value", {})
        if isinstance(value, dict):
            element_id = value.get("ELEMENT") or value.get("elementId")
    if not element_id:
        return {"error": "Element found but no element ID returned", "find_result": find_result["body"]}
    if assertion == "visible":
        return {"passed": True, "reason": "Element is visible"}
    if assertion == "text":
        attr_result = await _appium_post(appium_url, "/session/:sessionId/element/" + element_id + "/attribute/text")
        actual_text = ""
        if attr_result["status_code"] == 200:
            actual_text = attr_result["body"].get("value", "")
        if expected is not None:
            passed = actual_text == expected
            return {"passed": passed, "actual": actual_text, "expected": expected}
        return {"passed": True, "actual": actual_text}
    if assertion == "attribute":
        if expected is None:
            return {"error": "Attribute name is required for 'attribute' assertion. Pass it in 'expected'."}
        attr_result = await _appium_post(
            appium_url, "/session/:sessionId/element/" + element_id + "/attribute/" + expected
        )
        attr_value = None
        if attr_result["status_code"] == 200:
            attr_value = attr_result["body"].get("value")
        return {"passed": attr_value is not None, "attribute": expected, "value": attr_value}
    return {"error": f"Unhandled assertion type: {assertion}"}


async def app_screenshot(
    appium_url: str = "http://localhost:4723",
) -> dict[str, Any]:
    result = await _appium_post(appium_url, "/session/:sessionId/screenshot")
    if result["status_code"] != 200:
        return {"error": f"Screenshot failed: {result['body']}", "status_code": result["status_code"]}
    screenshot_data = result["body"].get("value", "")
    if isinstance(screenshot_data, str) and screenshot_data:
        return {"screenshot_base64": screenshot_data, "format": "png"}
    return {"error": "No screenshot data returned", "body": result["body"]}


async def app_get_source(
    appium_url: str = "http://localhost:4723",
) -> dict[str, Any]:
    result = await _appium_post(appium_url, "/session/:sessionId/source")
    if result["status_code"] != 200:
        return {"error": f"Get source failed: {result['body']}", "status_code": result["status_code"]}
    source = result["body"].get("value", "")
    return {"source": source, "format": "xml"}
