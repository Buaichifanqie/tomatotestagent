# App 测试视觉理解改造与智能导航 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 App 测试引入视觉理解能力（Vision MCP Server + GLM-4.6V-Flash），实现截图→多模态分析→坐标交互的完整流程，并修复智能滑动导航问题。

**Architecture:** 新增独立的 `vision_server` MCP Server（继承 `BaseMCPServer`），负责截图分析（调用 GLM API）。增强 `appium_server` 新增录屏工具。增强 `AppiumRunner` 实现每次 action 后自动截图。Agent 层增强系统提示，支持视觉+XML 双模式理解。

**Tech Stack:** Python 3.12+, httpx, GLM-4.6V-Flash API (OpenAI-compatible), Appium

---

### Task 1: Vision MCP Server — 基础结构

**Files:**
- Create: `testagent/mcp_servers/vision_server/__init__.py`
- Create: `testagent/mcp_servers/vision_server/__main__.py`
- Create: `testagent/mcp_servers/vision_server/server.py`
- Create: `testagent/mcp_servers/vision_server/tools.py`
- Modify: `testagent/config/settings.py` (添加 vision 配置项)
- Create: `testagent/mcp_servers/vision_server/glm_client.py`

- [ ] **Step 1: 创建目录和包初始化文件**

创建 `testagent/mcp_servers/vision_server/__init__.py`:

```python
from __future__ import annotations

from testagent.mcp_servers.vision_server.server import VisionMCPServer

__all__ = ["VisionMCPServer"]
```

创建 `testagent/mcp_servers/vision_server/glm_client.py`（GLM API 客户端）:

```python
from __future__ import annotations

import json
from typing import Any

import httpx

from testagent.common.logging import get_logger

logger = get_logger(__name__)

DEFAULT_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-4.6v-flash"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3

_VISION_SYSTEM_PROMPT = """你是一个手机界面分析专家。你的任务是分析手机截图，找出用户指定的 UI 元素。

## 分析规范
1. 仔细查看截图中的所有 UI 元素，包括应用图标、按钮、输入框、文字标签等
2. 如果找到目标元素，返回其精确坐标
3. 坐标格式：返回元素在截图中的像素坐标
4. 如果目标元素不在当前屏幕中，指出当前屏幕上有什么，并建议如何导航（滑动方向）找到目标

## 坐标返回格式
你可以在描述中包含坐标信息，格式为：
- 中心点坐标: (x, y)
- 边界框: [x1, y1, x2, y2]
- 也可以同时提供两种格式

## 滑动建议
如果目标不在当前屏幕，建议滑动方向（swipe_left/swipe_right/swipe_up/swipe_down），并说明原因。"""


class GLMClient:
    """GLM-4.6V-Flash API client for visual analysis."""

    def __init__(
        self,
        api_key: str,
        api_url: str = DEFAULT_API_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries

    async def analyze(self, image_base64: str, prompt: str) -> dict[str, Any]:
        """Send screenshot to GLM API for analysis.

        Args:
            image_base64: Base64-encoded PNG screenshot (without data:image prefix)
            prompt: User prompt describing what to find/analyze

        Returns:
            Parsed response with content text
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_base64}"
                            },
                        },
                        {
                            "type": "text",
                            "text": f"{_VISION_SYSTEM_PROMPT}\n\n{prompt}",
                        },
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 1024,
        }

        last_exception: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout)) as client:
                    response = await client.post(self._api_url, headers=headers, json=payload)
                    response.raise_for_status()
                    result = response.json()
                    content = result["choices"][0]["message"]["content"]
                    return {"content": content, "finish_reason": result["choices"][0].get("finish_reason", "")}
            except httpx.HTTPStatusError as e:
                last_exception = e
                if attempt < self._max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning("GLM API error, retrying", extra={"extra_data": {"attempt": attempt + 1, "wait": wait, "error": str(e)}})
                    await asyncio.sleep(wait)
                else:
                    logger.error("GLM API failed after max retries", extra={"extra_data": {"error": str(e)}})
            except Exception as e:
                last_exception = e
                logger.error("GLM API unexpected error", extra={"extra_data": {"error": str(e)}})
                break

        return {"error": str(last_exception) if last_exception else "Unknown error"}
```

需要在 `glm_client.py` 顶部补上 `import asyncio`:

```python
import asyncio
```

（已包含在上述代码中）

- [ ] **Step 2: 实现 tools.py**

创建 `testagent/mcp_servers/vision_server/tools.py`:

```python
from __future__ import annotations

import re
from typing import Any

from testagent.mcp_servers.vision_server.glm_client import GLMClient

_FIND_ELEMENT_PROMPT_TEMPLATE = """请在截图中找到以下目标：{target}

请分析：
1. 目标是否在当前屏幕中可见？
2. 如果可见，返回元素的坐标（中心点和边界框）
3. 如果不可见，当前屏幕主要有什么内容？建议向哪个方向滑动来寻找目标？

请按以下格式回复：
- found: true/false
- 如果找到，提供 center 坐标 (x, y) 和 bounds [x1, y1, x2, y2]
- 如果没找到，提供 suggestion 滑动方向
- 简要描述你看到的内容"""

_DESCRIBE_SCREEN_PROMPT = """请详细描述当前手机屏幕的内容，包括：
1. 屏幕上所有可交互的元素（应用图标、按钮、输入框、菜单等）
2. 每个元素的大致位置和功能描述
3. 屏幕的整体布局结构
4. 当前屏幕上用户可以执行哪些操作

请以结构化的方式列出所有元素。"""


def _parse_coordinates(text: str) -> dict[str, Any]:
    """Parse coordinate information from GLM response text.

    Handles multiple formats:
    - Center point: (x, y) or center: (x, y)
    - Bounding box: [x1, y1, x2, y2] or bounds: [x1, y1, x2, y2]
    """
    result: dict[str, Any] = {}

    # Parse center point: (digits, digits)
    center_match = re.search(r'[（(]\s*(\d+)\s*[，,]\s*(\d+)\s*[）)]', text)
    if center_match:
        result["center"] = {"x": int(center_match.group(1)), "y": int(center_match.group(2))}

    # Parse bounding box: [digits, digits, digits, digits]
    bounds_match = re.search(r'\[\s*(\d+)\s*[，,]\s*(\d+)\s*[，,]\s*(\d+)\s*[，,]\s*(\d+)\s*\]', text)
    if bounds_match:
        result["bounds"] = {
            "x1": int(bounds_match.group(1)),
            "y1": int(bounds_match.group(2)),
            "x2": int(bounds_match.group(3)),
            "y2": int(bounds_match.group(4)),
        }

    return result


def _parse_found_status(text: str) -> bool:
    """Check if the model says the target is found."""
    lower = text.lower()
    # Check for explicit "found: true" pattern
    if re.search(r'found[:\s]*true', lower):
        return True
    if re.search(r'found[:\s]*false', lower):
        return False
    # Heuristic: if coordinates are present, assume found
    if re.search(r'[（(]\s*\d+\s*[，,]\s*\d+\s*[）)]', text):
        return True
    return False


def _parse_suggestion(text: str) -> str | None:
    """Parse navigation suggestion from response."""
    lower = text.lower()
    swipe_patterns = [
        (r'swipe_left|向左滑|左滑|向右划', "swipe_left"),
        (r'swipe_right|向右滑|右滑|向左划', "swipe_right"),
        (r'swipe_up|向上滑|上滑|向下划', "swipe_up"),
        (r'swipe_down|向下滑|下滑|向上划', "swipe_down"),
        (r'scroll_down|向下滚动', "scroll_down"),
        (r'scroll_up|向上滚动', "scroll_up"),
    ]
    for pattern, suggestion in swipe_patterns:
        if re.search(pattern, lower):
            return suggestion
    return None


async def vision_find_element(
    image: str,
    target: str,
    context: str | None = None,
    glm_client: GLMClient | None = None,
) -> dict[str, Any]:
    """Find a UI element on screen by visual analysis.

    Args:
        image: Base64-encoded screenshot
        target: Natural language description of the target element
        context: Optional context from previous screen analysis
        glm_client: GLMClient instance

    Returns:
        Dict with found, center, bounds, suggestion, description keys
    """
    prompt = _FIND_ELEMENT_PROMPT_TEMPLATE.format(target=target)
    if context:
        prompt = f"之前的屏幕分析：{context}\n\n{prompt}"

    if glm_client is None:
        return {"error": "GLM client not initialized", "found": False}

    result = await glm_client.analyze(image, prompt)
    if "error" in result:
        return {"error": result["error"], "found": False}

    content = result.get("content", "")

    coords = _parse_coordinates(content)
    found = _parse_found_status(content)
    suggestion = _parse_suggestion(content)

    return {
        "found": found or bool(coords.get("center")),
        "center": coords.get("center"),
        "bounds": coords.get("bounds"),
        "suggestion": suggestion,
        "description": content.strip(),
    }


async def vision_describe_screen(
    image: str,
    glm_client: GLMClient | None = None,
) -> dict[str, Any]:
    """Describe the current screen content visually.

    Args:
        image: Base64-encoded screenshot
        glm_client: GLMClient instance

    Returns:
        Dict with elements, layout, suggestions keys
    """
    if glm_client is None:
        return {"error": "GLM client not initialized"}

    result = await glm_client.analyze(image, _DESCRIBE_SCREEN_PROMPT)
    if "error" in result:
        return {"error": result["error"]}

    content = result.get("content", "")

    return {
        "description": content.strip(),
        "layout": content.strip()[:500],
    }
```

- [ ] **Step 3: 实现 server.py**

创建 `testagent/mcp_servers/vision_server/server.py`:

```python
from __future__ import annotations

import json
from typing import Any, ClassVar

from testagent.mcp_servers.base import BaseMCPServer
from testagent.mcp_servers.vision_server.glm_client import GLMClient
from testagent.mcp_servers.vision_server.tools import (
    vision_describe_screen,
    vision_find_element,
)


class VisionMCPServer(BaseMCPServer):
    server_name = "vision_server"

    def __init__(
        self,
        api_key: str = "",
        api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        model: str = "glm-4.6v-flash",
        timeout: int = 30,
        max_retries: int = 3,
    ) -> None:
        self._glm_client = GLMClient(
            api_key=api_key,
            api_url=api_url,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
        )

    _tools_spec: ClassVar[list[dict[str, object]]] = [
        {
            "name": "vision_find_element",
            "description": "通过视觉分析在截图中查找目标 UI 元素，返回坐标和导航建议。先截取屏幕截图，再调用此工具进行分析。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "base64 编码的 PNG 截图数据",
                    },
                    "target": {
                        "type": "string",
                        "description": "要查找的目标的自然语言描述，如'美团 app 图标'、'搜索框'",
                    },
                    "context": {
                        "type": "string",
                        "description": "可选，之前的屏幕分析上下文，用于辅助导航决策",
                    },
                },
                "required": ["image", "target"],
            },
        },
        {
            "name": "vision_describe_screen",
            "description": "通过视觉分析描述当前屏幕的内容和布局，返回可交互元素列表和布局信息",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "base64 编码的 PNG 截图数据",
                    },
                },
                "required": ["image"],
            },
        },
    ]

    _tool_registry: ClassVar[dict[str, Any]] = {
        "vision_find_element": vision_find_element,
        "vision_describe_screen": vision_describe_screen,
    }

    async def list_tools(self) -> list[dict[str, object]]:
        return self._tools_spec

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            from inspect import iscoroutinefunction

            injected = {**arguments, "glm_client": self._glm_client}
            if iscoroutinefunction(tool):
                result = await tool(**injected)
            else:
                result = tool(**injected)
            if isinstance(result, (dict, list)):
                return json.dumps(result, ensure_ascii=False)
            return str(result)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    async def list_resources(self) -> list[dict[str, object]]:
        return [
            {
                "uri": "vision://status",
                "name": "Vision Server Status",
                "mimeType": "text/plain",
                "description": "Vision MCP Server 运行状态",
            },
        ]

    async def health_check(self) -> bool:
        # Vision server is healthy if GLM client has an API key configured
        return bool(self._glm_client._api_key)
```

- [ ] **Step 4: 创建 __main__.py**

创建 `testagent/mcp_servers/vision_server/__main__.py`:

```python
"""MCP stdio server entry point for Vision analysis (GLM-4.6V-Flash).

This wraps the VisionMCPServer as a proper MCP stdio server.

Usage:
    python -m testagent.mcp_servers.vision_server
"""

import json

import mcp.server as server
import mcp.server.stdio
import mcp.types as types

from testagent.mcp_servers.vision_server.server import VisionMCPServer

_vision_server = VisionMCPServer(
    api_key="",
    api_url="https://open.bigmodel.cn/api/paas/v4/chat/completions",
    model="glm-4.6v-flash",
)
mcp = server.Server("vision_server")


@mcp.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=spec["name"],
            description=spec["description"],
            inputSchema=spec["inputSchema"],
        )
        for spec in _vision_server._tools_spec
    ]


@mcp.call_tool()
async def call_tool(
    name: str,
    arguments: dict[str, object] | None,
) -> list[types.TextContent]:
    if arguments is None:
        arguments = {}
    result = await _vision_server.call_tool(name, arguments)
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return [types.TextContent(type="text", text=text)]


async def main() -> None:
    async with server.stdio.stdio_server() as (read, write):
        await mcp.run(read, write, mcp.create_initialization_options())


if __name__ == "__main__":
    import anyio

    anyio.run(main)
```

- [ ] **Step 5: 更新 settings.py 添加 vision 配置**

修改 `testagent/config/settings.py`:

在 `_SECRET_FIELDS` 中添加 `"vision_api_key"`:

```python
_SECRET_FIELDS = frozenset(
    {
        "openai_api_key",
        "meilisearch_api_key",
        "postgres_password",
        "vision_api_key",
    }
)
```

在 `TestAgentSettings` 类中添加字段（在 `data_retention_days` 之前）:

```python
    vision_api_key: SecretStr = SecretStr("")
    vision_api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    vision_model: str = "glm-4.6v-flash"
    vision_timeout: int = 30
    vision_max_retries: int = 3
```

- [ ] **Step 6: 提交**

```bash
git add testagent/mcp_servers/vision_server/
git add testagent/config/settings.py
git commit -m "feat: add Vision MCP Server with GLM-4.6V-Flash integration"
```

---

### Task 2: Appium MCP Server — 新增录屏工具

**Files:**
- Modify: `testagent/mcp_servers/appium_server/tools.py`
- Modify: `testagent/mcp_servers/appium_server/server.py`

- [ ] **Step 1: 在 tools.py 中添加录屏函数**

在 `app_get_source` 函数之后追加（tools.py 末尾）:

```python
async def app_start_recording(
    appium_url: str = "http://localhost:4723",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Start screen recording on the device."""
    payload: dict[str, object] = {
        "options": {
            "timeLimit": 180,
            "videoType": "h264",
            "videoQuality": "medium",
            "bitRate": 4000000,
        }
    }
    return await _appium_post(
        appium_url,
        "/session/:sessionId/appium/start_recording_screen",
        payload,
        session_id=session_id,
    )


async def app_stop_recording(
    appium_url: str = "http://localhost:4723",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Stop screen recording and return the recorded video."""
    result = await _appium_post(
        appium_url,
        "/session/:sessionId/appium/stop_recording_screen",
        {},
        timeout=60,
        session_id=session_id,
    )
    if result["status_code"] != 200:
        return {"error": f"Stop recording failed: {result['body']}", "status_code": result["status_code"]}
    video_data = result["body"].get("value", "")
    if isinstance(video_data, str) and video_data:
        return {"video_base64": video_data, "format": "mp4"}
    return {"error": "No video data returned", "body": result["body"]}
```

- [ ] **Step 2: 在 server.py 中注册新工具**

在 `server.py` 的 import 部分添加:

```python
from testagent.mcp_servers.appium_server.tools import (
    app_assert_element,
    app_get_source,
    app_install,
    app_screenshot,
    app_start_recording,
    app_stop_recording,
    app_swipe,
    app_tap,
    app_type,
)
```

在 `_tools_spec` 列表末尾（`app_get_source` 之后）添加两个工具规范:

```python
        {
            "name": "app_start_recording",
            "description": "开始录制设备屏幕，录制为 MP4 视频",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "app_stop_recording",
            "description": "停止屏幕录制并返回录制的视频（base64 编码的 MP4）",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
```

在 `_tool_registry` 中添加:

```python
    _tool_registry: ClassVar[dict[str, Any]] = {
        "app_install": app_install,
        "app_tap": app_tap,
        "app_swipe": app_swipe,
        "app_type": app_type,
        "app_assert_element": app_assert_element,
        "app_screenshot": app_screenshot,
        "app_get_source": app_get_source,
        "app_start_recording": app_start_recording,
        "app_stop_recording": app_stop_recording,
    }
```

- [ ] **Step 3: 提交**

```bash
git add testagent/mcp_servers/appium_server/tools.py
git add testagent/mcp_servers/appium_server/server.py
git commit -m "feat(appium): add screen recording tools (start/stop)"
```

---

### Task 3: AppiumRunner — 每次 action 后自动截图

**Files:**
- Modify: `testagent/harness/runners/appium_runner.py`

- [ ] **Step 1: 在 _execute_local 中添加自动截图**

修改 `testagent/harness/runners/appium_runner.py` 的 `_execute_local` 方法（约第 182-200 行）:

在 for 循环中，action 执行后立即截图:

```python
        try:
            for i, action in enumerate(actions):
                action_result = await self._execute_action(action, i)
                if action.get("assertion"):
                    assertion_results.update(action_result)
                # Auto screenshot after each action for audit trail
                await self._capture_screenshot()

            duration_ms = self._now_ms() - start_ms
```

- [ ] **Step 2: 提交**

```bash
git add testagent/harness/runners/appium_runner.py
git commit -m "feat(runner): auto screenshot after each action in AppiumRunner"
```

---

### Task 4: Vision 配置文件 + 注册

**Files:**
- Create: `configs/vision_config.json`
- Modify: `testagent/mcp_servers/vision_server/__main__.py` (从配置读取 API Key)
- Modify: `testagent/mcp_servers/vision_server/server.py` (支持从配置初始化)

- [ ] **Step 1: 创建 JSON 配置文件**

```json
{
  "api_key": "",
  "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
  "model": "glm-4.6v-flash",
  "timeout": 30,
  "max_retries": 3
}
```

- [ ] **Step 2: 增强 VisionMCPServer 支持从配置加载**

修改 `testagent/mcp_servers/vision_server/server.py` 的 `__init__`，添加 `from_settings` 类方法:

在 VisionMCPServer 类中添加:

```python
    @classmethod
    def from_settings(cls, settings: TestAgentSettings | None = None) -> VisionMCPServer:
        """Create VisionMCPServer from TestAgentSettings."""
        if settings is None:
            from testagent.config.settings import get_settings
            settings = get_settings()
        return cls(
            api_key=settings.vision_api_key.get_secret_value(),
            api_url=settings.vision_api_url,
            model=settings.vision_model,
            timeout=settings.vision_timeout,
            max_retries=settings.vision_max_retries,
        )
```

需要在 `server.py` 顶部添加条件导入:

```python
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings

from testagent.mcp_servers.base import BaseMCPServer
from testagent.mcp_servers.vision_server.glm_client import GLMClient
from testagent.mcp_servers.vision_server.tools import (
    vision_describe_screen,
    vision_find_element,
)
```

- [ ] **Step 3: 提交**

```bash
git add configs/vision_config.json
git add testagent/mcp_servers/vision_server/server.py
git commit -m "feat(vision): add config file and from_settings factory method"
```

---

### Task 5: 更新 ask.py 系统提示和工具定义

**Files:**
- Modify: `testagent/cli/ask.py`

- [ ] **Step 1: 在 APPIUM_TOOLS 中添加 vision 工具定义**

在 `APPIUM_TOOLS` 列表末尾添加:

```python
    {
        "name": "vision_find_element",
        "description": "通过视觉分析在截图中查找目标 UI 元素。传入 base64 截图和目标描述，返回元素坐标。如果目标不在当前屏幕，会建议滑动方向。",
        "parameters": {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "base64 编码的 PNG 截图"},
                "target": {"type": "string", "description": "要查找的目标的自然语言描述，如'美团 app 图标'"},
                "context": {"type": "string", "description": "可选的上下文信息"},
            },
            "required": ["image", "target"],
        },
    },
    {
        "name": "vision_describe_screen",
        "description": "通过视觉分析描述当前屏幕的所有内容和布局，返回可交互元素列表",
        "parameters": {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "base64 编码的 PNG 截图"},
            },
            "required": ["image"],
        },
    },
```

- [ ] **Step 2: 更新系统提示**

将 `_SYSTEM_PROMPT` 更新为:

```python
_SYSTEM_PROMPT = """\
You are TestAgent, an AI-powered mobile testing assistant connected to an Android emulator via Appium.

## Your Capabilities
You have TWO ways to understand the mobile screen:
1. **XML Analysis** (app_get_source) — Get the current screen's XML page source for precise element selectors
2. **Visual Analysis** (vision_find_element / vision_describe_screen) — Use screenshot + multimodal AI to understand the screen visually

Available tools:
- **vision_find_element** — Find a UI element on screen by visual analysis. Pass a screenshot and describe what you're looking for.
- **vision_describe_screen** — Get a visual description of the current screen content and layout.
- **app_get_source** — Get the current screen XML page source.
- **app_screenshot** — Take a screenshot of the current screen.
- **app_tap** — Tap/click a UI element using its selector or coordinates.
- **app_type** — Type text into an input field.
- **app_swipe** — Swipe across the screen.
- **app_assert_element** — Check whether an element is visible, has certain text, or has an attribute.
- **app_start_recording** — Start recording the device screen.
- **app_stop_recording** — Stop recording and get the video.

## Testing Workflow
When given a testing task:
1. **Visually Explore**: Take a screenshot and call vision_describe_screen to understand the current screen layout.
2. **Find Elements**: When you need to interact with a specific element, take a screenshot and call vision_find_element with a description of what you're looking for.
3. **Smart Navigation**: If the target isn't found on the current screen, vision_find_element will suggest a swipe direction. Follow the suggestion, then retry.
4. **Interact**: Use app_tap (with coordinates from vision analysis) or app_type to interact with elements.
5. **Verify**: Use app_assert_element to check expected element states.
6. **Record**: Use app_start_recording at the start and app_stop_recording at the end to capture video evidence.

## Smart Navigation Rules
- If an element isn't found on the current screen, try swiping to find it
- Follow the AI's suggested swipe direction first
- If the AI is unsure, try: swipe left → swipe right → swipe up → swipe down
- Take a screenshot after each swipe and re-analyze
- Report clearly if the target can't be found after trying all directions

## Key Tips
- Prefer visual analysis (vision_find_element) for finding elements by appearance
- Use coordinates (x, y) from vision analysis with app_tap
- Use XML source (app_get_source) as a fallback when you need exact selectors
- Take screenshots at key points to document the test
- Keep interactions simple and sequential — one step at a time
"""
```

- [ ] **Step 3: 实现 vision 工具调用处理**

在 `ask.py` 的 `_register_tool_handlers` 函数区域，添加 vision 工具的处理:

找到 `_register_tool_handlers` 函数，在 appium 工具处理器区域后添加 vision 工具处理器:

```python
    # ── Vision tool handlers ────────────────────────────────────

    async def _handle_vision_find_element(image: str, target: str, context: str | None = None) -> str:
        nonlocal vision_client
        if vision_client is None:
            return json.dumps({"error": "Vision client not initialized"})
        result = await vision_find_element(image, target, context, glm_client=vision_client)
        return json.dumps(result, ensure_ascii=False)

    async def _handle_vision_describe_screen(image: str) -> str:
        nonlocal vision_client
        if vision_client is None:
            return json.dumps({"error": "Vision client not initialized"})
        result = await vision_describe_screen(image, glm_client=vision_client)
        return json.dumps(result, ensure_ascii=False)

    tool_handlers["vision_find_element"] = _handle_vision_find_element
    tool_handlers["vision_describe_screen"] = _handle_vision_describe_screen
```

在 `ask_app` 函数中初始化 `vision_client`:

```python
    # Initialize GLM client for vision
    from testagent.config.settings import get_settings
    from testagent.mcp_servers.vision_server.glm_client import GLMClient

    settings = get_settings()
    vision_client = GLMClient(
        api_key=settings.vision_api_key.get_secret_value(),
        api_url=settings.vision_api_url,
        model=settings.vision_model,
        timeout=settings.vision_timeout,
        max_retries=settings.vision_max_retries,
    )
```

- [ ] **Step 4: 提交**

```bash
git add testagent/cli/ask.py
git commit -m "feat(cli): enhance system prompt with vision capabilities and smart navigation"
```

---

### Task 6: 更新 AGENTS.md — 添加 Vision Server 文档

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: 在目录结构部分添加 vision_server**

在 `testagent/mcp_servers/` 章节中添加:

```
├── testagent/mcp_servers/vision_server/   # Vision MCP Server：截图多模态分析（GLM-4.6V-Flash）
```

- [ ] **Step 2: 在技能清单中更新 app_smoke_test 的描述**

在预置 Skill 清单表格中，修改 `app_smoke_test` 的 required_mcp_servers 列，添加 `vision_server`:

```
| `app_smoke_test` | App 核心流程冒烟测试 | App | MVP | appium_server, vision_server, database_server | req_docs, locator_library |
```

- [ ] **Step 3: 提交**

```bash
git add AGENTS.md
git commit -m "docs: add vision_server to architecture docs"
```

---

### Task 7: 单元测试 — Vision MCP Server

**Files:**
- Create: `tests/unit/test_vision_server.py`
- Create: `tests/unit/test_vision_tools.py`

- [ ] **Step 1: 测试 Vision Server 结构**

创建 `tests/unit/test_vision_server.py`:

```python
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.mcp_servers.vision_server.glm_client import GLMClient
from testagent.mcp_servers.vision_server.server import VisionMCPServer
from testagent.mcp_servers.vision_server.tools import (
    _parse_coordinates,
    _parse_found_status,
    _parse_suggestion,
    vision_describe_screen,
    vision_find_element,
)


class TestCoordinateParsing:
    def test_parse_center_point_parentheses(self) -> None:
        text = "目标位于屏幕中央，坐标 (540, 1200)"
        result = _parse_coordinates(text)
        assert result.get("center") == {"x": 540, "y": 1200}

    def test_parse_center_point_chinese_parentheses(self) -> None:
        text = "目标位于（320， 800）"
        result = _parse_coordinates(text)
        assert result.get("center") == {"x": 320, "y": 800}

    def test_parse_bounding_box(self) -> None:
        text = "元素边界框 [100, 200, 300, 400]"
        result = _parse_coordinates(text)
        assert result.get("bounds") == {"x1": 100, "y1": 200, "x2": 300, "y2": 400}

    def test_parse_bounding_box_chinese_comma(self) -> None:
        text = "元素边界框[100，200，300，400]"
        result = _parse_coordinates(text)
        assert result.get("bounds") == {"x1": 100, "y1": 200, "x2": 300, "y2": 400}

    def test_parse_center_and_bounds(self) -> None:
        text = "中心点 (540, 1200)，边界框 [100, 200, 300, 400]"
        result = _parse_coordinates(text)
        assert result.get("center") == {"x": 540, "y": 1200}
        assert result.get("bounds") == {"x1": 100, "y1": 200, "x2": 300, "y2": 400}

    def test_no_coordinates(self) -> None:
        text = "屏幕上没有找到目标元素"
        result = _parse_coordinates(text)
        assert result == {}


class TestFoundStatusParsing:
    def test_found_true_explicit(self) -> None:
        assert _parse_found_status("found: true，目标在屏幕中央") is True

    def test_found_false_explicit(self) -> None:
        assert _parse_found_status("found: false，目标不在当前屏幕") is False

    def test_found_with_coordinates(self) -> None:
        assert _parse_found_status("目标位于(540, 1200)") is True

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

    def test_no_suggestion(self) -> None:
        assert _parse_suggestion("目标在当前屏幕中") is None


class TestVisionMCPServer:
    def test_server_name(self) -> None:
        server = VisionMCPServer(api_key="test-key")
        assert server.server_name == "vision_server"

    def test_list_tools(self) -> None:
        server = VisionMCPServer(api_key="test-key")
        tools = server.list_tools()
        tool_names = [t["name"] for t in tools]
        assert "vision_find_element" in tool_names
        assert "vision_describe_screen" in tool_names
        assert len(tools) == 2

    def test_call_tool_unknown(self) -> None:
        server = VisionMCPServer(api_key="test-key")
        result = server.call_tool("unknown_tool", {})
        result_str = result if isinstance(result, str) else ""
        assert "Unknown tool" in result_str

    @pytest.mark.asyncio
    async def test_call_tool_vision_find_element_no_client(self) -> None:
        server = VisionMCPServer(api_key="")
        result = await server.call_tool("vision_find_element", {"image": "abc", "target": "test"})
        result_dict = json.loads(result) if isinstance(result, str) else {}
        assert result_dict.get("found") is False
        assert "error" in result_dict

    def test_health_check_no_key(self) -> None:
        server = VisionMCPServer(api_key="")
        result = server.health_check()
        assert result is False

    def test_health_check_with_key(self) -> None:
        server = VisionMCPServer(api_key="test-key")
        result = server.health_check()
        assert result is True

    def test_list_resources(self) -> None:
        server = VisionMCPServer(api_key="test-key")
        resources = server.list_resources()
        assert len(resources) == 1
        assert resources[0]["uri"] == "vision://status"

    def test_from_settings(self) -> None:
        with patch("testagent.mcp_servers.vision_server.server.TestAgentSettings") as mock_settings:
            mock_settings.return_value.vision_api_key.get_secret_value.return_value = "key-from-settings"
            mock_settings.return_value.vision_api_url = "https://test.api.com"
            mock_settings.return_value.vision_model = "test-model"
            mock_settings.return_value.vision_timeout = 60
            mock_settings.return_value.vision_max_retries = 5

            from testagent.config.settings import TestAgentSettings
            server = VisionMCPServer.from_settings(mock_settings.return_value)
            assert server._glm_client._api_key == "key-from-settings"
            assert server._glm_client._api_url == "https://test.api.com"
            assert server._glm_client._model == "test-model"


class TestGLMClient:
    def test_init(self) -> None:
        client = GLMClient(api_key="test-key")
        assert client._api_key == "test-key"
        assert client._model == "glm-4.6v-flash"
        assert client._timeout == 30
        assert client._max_retries == 3

    @pytest.mark.asyncio
    async def test_analyze_success(self) -> None:
        client = GLMClient(api_key="test-key", max_retries=1)
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
    async def test_analyze_retry_then_fail(self) -> None:
        client = GLMClient(api_key="test-key", max_retries=2)
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = Exception("Server Error")

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await client.analyze("base64image", "找到目标")
            assert "error" in result


class TestVisionTools:
    @pytest.mark.asyncio
    async def test_vision_find_element_no_client(self) -> None:
        result = await vision_find_element("image_data", "test target")
        assert result.get("found") is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_vision_find_element_success(self) -> None:
        mock_client = AsyncMock(spec=GLMClient)
        mock_client.analyze.return_value = {
            "content": "found: true，目标在屏幕中央，坐标 (540, 1200)，边界框 [100, 200, 300, 400]",
            "finish_reason": "stop",
        }

        result = await vision_find_element("image_data", "美团 app", glm_client=mock_client)
        assert result["found"] is True
        assert result["center"] == {"x": 540, "y": 1200}
        assert result["bounds"] == {"x1": 100, "y1": 200, "x2": 300, "y2": 400}

    @pytest.mark.asyncio
    async def test_vision_find_element_not_found_with_suggestion(self) -> None:
        mock_client = AsyncMock(spec=GLMClient)
        mock_client.analyze.return_value = {
            "content": "found: false，目标不在当前屏幕，建议向左滑动",
            "finish_reason": "stop",
        }

        result = await vision_find_element("image_data", "快手 app", glm_client=mock_client)
        assert result["found"] is False
        assert result["suggestion"] == "swipe_left"

    @pytest.mark.asyncio
    async def test_vision_describe_screen_no_client(self) -> None:
        result = await vision_describe_screen("image_data")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_vision_describe_screen_success(self) -> None:
        mock_client = AsyncMock(spec=GLMClient)
        mock_client.analyze.return_value = {
            "content": "当前屏幕是 Android 主桌面，包含以下应用图标：时钟、设置、相机、电话、短信。底部有导航栏。",
            "finish_reason": "stop",
        }

        result = await vision_describe_screen("image_data", glm_client=mock_client)
        assert "error" not in result
        assert "description" in result
```

- [ ] **Step 2: 运行测试验证**

Run: `pytest tests/unit/test_vision_server.py tests/unit/test_vision_tools.py -v`

Expected: All tests pass

- [ ] **Step 3: 提交**

```bash
git add tests/unit/test_vision_server.py tests/unit/test_vision_tools.py
git commit -m "test: add unit tests for Vision MCP Server"
```

---

### Task 8: 单元测试 — Appium Server 录屏工具

**Files:**
- Modify: `tests/unit/test_appium_server.py`

- [ ] **Step 1: 添加录屏工具测试**

在 `tests/unit/test_appium_server.py` 中添加测试类:

```python
class TestAppiumRecordingTools:
    """Test screen recording tools."""

    @pytest.mark.asyncio
    async def test_start_recording(self) -> None:
        from testagent.mcp_servers.appium_server.tools import app_start_recording

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": None}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            result = await app_start_recording(appium_url="http://localhost:4723", session_id="test-session")
            assert result["status_code"] == 200
            # Verify session_id was injected into the path
            call_path = mock_post.call_args[0][0]
            assert "test-session" in call_path

    @pytest.mark.asyncio
    async def test_stop_recording_success(self) -> None:
        from testagent.mcp_servers.appium_server.tools import app_stop_recording

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": "base64videodata"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await app_stop_recording(appium_url="http://localhost:4723", session_id="test-session")
            assert result.get("video_base64") == "base64videodata"
            assert result.get("format") == "mp4"

    @pytest.mark.asyncio
    async def test_stop_recording_failure(self) -> None:
        from testagent.mcp_servers.appium_server.tools import app_stop_recording

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"value": ""}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await app_stop_recording(appium_url="http://localhost:4723", session_id="test-session")
            assert "error" in result
```

- [ ] **Step 2: 运行测试**

Run: `pytest tests/unit/test_appium_server.py::TestAppiumRecordingTools -v`

Expected: All tests pass

- [ ] **Step 3: 提交**

```bash
git add tests/unit/test_appium_server.py
git commit -m "test: add recording tools unit tests"
```

---

### Task 9: 集成测试 — Vision + Appium 联合流程

**Files:**
- Create: `tests/integration/test_vision_appium_flow.py`

- [ ] **Step 1: 写集成测试**

```python
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.mcp_servers.vision_server.tools import (
    vision_describe_screen,
    vision_find_element,
)


@pytest.mark.asyncio
async def test_vision_find_then_tap_flow() -> None:
    """Simulate the full flow: screenshot -> vision find -> tap coordinates."""

    # Step 1: Take screenshot (mocked)
    screenshot_base64 = "mocked_base64_screenshot_data"

    # Step 2: Vision analysis finds the element
    mock_glm = AsyncMock()
    mock_glm.analyze.return_value = {
        "content": "found: true，找到美团 app 图标，中心坐标 (540, 1200)",
        "finish_reason": "stop",
    }

    vision_result = await vision_find_element(screenshot_base64, "美团 app", glm_client=mock_glm)
    assert vision_result["found"] is True
    assert vision_result["center"] == {"x": 540, "y": 1200}

    # Step 3: Use coordinates to tap via Appium
    center = vision_result["center"]
    tap_x, tap_y = center["x"], center["y"]

    # Verify coordinates are valid integers for tapping
    assert isinstance(tap_x, int)
    assert isinstance(tap_y, int)
    assert tap_x > 0
    assert tap_y > 0


@pytest.mark.asyncio
async def test_vision_smart_navigation_flow() -> None:
    """Simulate the smart navigation flow: not found -> swipe -> found."""

    screenshot_b64 = "mocked_screenshot"
    mock_glm = AsyncMock()

    # First analysis: not found, suggest swipe left
    mock_glm.analyze.return_value = {
        "content": "found: false，目标不在当前屏幕，当前屏幕显示的是主桌面第一页，有微信、支付宝等。建议向左滑动查看第二页。",
        "finish_reason": "stop",
    }

    result1 = await vision_find_element(screenshot_b64, "快手 app", glm_client=mock_glm)
    assert result1["found"] is False
    assert result1["suggestion"] == "swipe_left"

    # After swiping left, take another screenshot and re-analyze
    mock_glm.analyze.return_value = {
        "content": "found: true，找到快手 app 图标，位于屏幕第二行第三列，中心坐标 (800, 600)",
        "finish_reason": "stop",
    }

    result2 = await vision_find_element(screenshot_b64, "快手 app", glm_client=mock_glm)
    assert result2["found"] is True
    assert result2["center"] == {"x": 800, "y": 600}


@pytest.mark.asyncio
async def test_vision_describe_then_interact_flow() -> None:
    """Simulate: describe screen -> find search box -> type text."""

    screenshot_b64 = "mocked_screenshot"
    mock_glm = AsyncMock()

    # Step 1: Describe the screen
    mock_glm.analyze.return_value = {
        "content": "当前屏幕是美团 app 首页，顶部有搜索框，中间有各种分类图标（外卖、美食、酒店等），底部有导航栏。",
        "finish_reason": "stop",
    }

    describe_result = await vision_describe_screen(screenshot_b64, glm_client=mock_glm)
    assert "搜索框" in describe_result["description"]
    assert "error" not in describe_result

    # Step 2: Find the search box
    mock_glm.analyze.return_value = {
        "content": "found: true，搜索框位于屏幕顶部，中心坐标 (540, 150)，边界框 [100, 120, 980, 180]",
        "finish_reason": "stop",
    }

    find_result = await vision_find_element(screenshot_b64, "搜索框", glm_client=mock_glm)
    assert find_result["found"] is True
    assert find_result["center"] == {"x": 540, "y": 150}
    assert find_result["bounds"] == {"x1": 100, "y1": 120, "x2": 980, "y2": 180}
```

- [ ] **Step 2: 提交**

```bash
git add tests/integration/test_vision_appium_flow.py
git commit -m "test: add integration tests for vision + appium flow"
```
