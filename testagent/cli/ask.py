from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx

from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)

_APPIUM_URL = "http://localhost:4723"

# ── 工具定义 (OpenAI-compatible format, 传给 LLM) ──────────────────────────

APPIUM_TOOLS: list[dict[str, Any]] = [
    {
        "name": "app_get_source",
        "description": "获取当前屏幕的 XML 页面源码，用于理解当前 UI 布局和元素",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "app_screenshot",
        "description": "截取当前屏幕截图（base64 编码），可用于可视化验证",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "app_tap",
        "description": "点击指定选择器的元素",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "元素选择器（resource-id / content-desc / xpath）"},
                "strategy": {
                    "type": "string",
                    "description": "定位策略：accessibility_id / uiautomator / xpath",
                    "enum": ["accessibility_id", "uiautomator", "xpath"],
                },
            },
            "required": ["selector"],
        },
    },
    {
        "name": "app_type",
        "description": "向输入框元素输入文本",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "元素选择器"},
                "text": {"type": "string", "description": "要输入的文本"},
                "strategy": {
                    "type": "string",
                    "description": "定位策略：accessibility_id / uiautomator / xpath",
                    "enum": ["accessibility_id", "uiautomator", "xpath"],
                },
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "app_swipe",
        "description": "执行滑动手势",
        "parameters": {
            "type": "object",
            "properties": {
                "start_x": {"type": "integer", "description": "起始 X 坐标"},
                "start_y": {"type": "integer", "description": "起始 Y 坐标"},
                "end_x": {"type": "integer", "description": "结束 X 坐标"},
                "end_y": {"type": "integer", "description": "结束 Y 坐标"},
                "duration": {"type": "integer", "description": "滑动持续时间(毫秒), 默认 500"},
            },
            "required": ["start_x", "start_y", "end_x", "end_y"],
        },
    },
    {
        "name": "app_assert_element",
        "description": "断言元素状态（可见性/文本/属性），返回通过/失败",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "元素选择器"},
                "assertion": {
                    "type": "string",
                    "description": "断言类型：visible(可见), text(文本匹配), attribute(属性存在)",
                    "enum": ["visible", "text", "attribute"],
                },
                "expected": {"type": "string", "description": "预期值（text/attribute 断言时使用）"},
                "strategy": {
                    "type": "string",
                    "description": "定位策略：accessibility_id / uiautomator / xpath",
                    "enum": ["accessibility_id", "uiautomator", "xpath"],
                },
            },
            "required": ["selector", "assertion"],
        },
    },
    {
        "name": "app_install",
        "description": "安装 APK 应用到设备",
        "parameters": {
            "type": "object",
            "properties": {
                "app_path": {"type": "string", "description": "APK 文件路径或 URL"},
            },
            "required": ["app_path"],
        },
    },
]

_SYSTEM_PROMPT = """\
You are TestAgent, an AI-powered mobile testing assistant connected to an Android emulator via Appium.

## Your Capabilities
You can explore and test mobile apps using the following tools:
- **app_get_source** — Get the current screen XML page source. Use this FIRST to understand the UI layout.
- **app_screenshot** — Take a screenshot of the current screen.
- **app_tap** — Tap/click a UI element using its selector.
- **app_type** — Type text into an input field.
- **app_swipe** — Swipe across the screen.
- **app_assert_element** — Check whether an element is visible, has certain text, or has an attribute.

## Testing Workflow
When given a testing task:
1. **Explore**: Call app_get_source to get the current screen's XML page source. Look for elements with resource-id, content-desc (content-desc maps to accessibility_id), and text attributes.
2. **Plan**: Identify the elements you need to interact with based on the XML source.
3. **Execute**: Use the tools to interact with the app — tap buttons, type text, swipe, etc.
4. **Verify**: Use app_assert_element to check expected element states.
5. **Report**: Summarize what you tested, what passed, and what failed.

## Key Tips
- `resource-id` attributes are the most reliable selectors — use them with `uiautomator` strategy (e.g., `resource-id` value as selector).
- `content-desc` attributes map to `accessibility_id` strategy.
- For complex elements, use `xpath` strategy.
- Always get the page source after each interaction to see how the UI changed.
- Take screenshots at key points to document the test.
- Keep interactions simple and sequential — one step at a time.
"""


def _strategy_from_source(source: str, selector: str) -> str:
    """Guess the best strategy based on the selector and page source."""
    if "://" not in selector and ("/" in selector or "//" in selector):
        return "xpath"
    if f'content-desc="{selector}"' in source or f"content-desc='{selector}'" in source:
        return "accessibility_id"
    return "uiautomator"


async def _check_appium_health() -> bool:
    """检查 Appium 服务器是否可用。"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{_APPIUM_URL}/status")
            return resp.status_code == 200
    except Exception:
        return False


async def _create_session() -> str | None:
    """在 Android 模拟器上创建 Appium 会话。"""
    capabilities = {
        "capabilities": {
            "alwaysMatch": {
                "platformName": "Android",
                "appium:automationName": "UiAutomator2",
                "appium:deviceName": "emulator-5554",
                "appium:udid": "emulator-5554",
                "appium:noReset": True,
                "appium:autoGrantPermissions": True,
                "appium:newCommandTimeout": 120,
            },
            "firstMatch": [{}],
        }
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_APPIUM_URL}/session", json=capabilities)
            if resp.status_code == 200:
                data = resp.json()
                sid = data.get("value", {}).get("sessionId") or data.get("sessionId")
                if sid:
                    return sid
            logger.warning(
                "Session creation failed",
                extra={"extra_data": {"status": resp.status_code, "body": resp.text[:200]}},
            )
            return None
    except Exception as exc:
        logger.error("Session creation error", extra={"extra_data": {"error": str(exc)}})
        return None


async def _close_session(session_id: str) -> None:
    """关闭 Appium 会话。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.delete(f"{_APPIUM_URL}/session/{session_id}")
    except Exception:
        pass


def _register_tool_handlers(session_id: str) -> Callable[[str, dict[str, Any]], Any]:
    """注册感知真实 session_id 的 Appium 工具处理器。

    返回 dispatch_fn 函数，可直接传给 agent_loop。
    """
    from testagent.agent.loop import TOOL_HANDLERS, register_tool_handler

    async def _handler_source(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_get_source

        result = await app_get_source(appium_url=_APPIUM_URL, session_id=session_id)
        src = result.get("source", "")
        if len(src) > 5000:
            result["source"] = src[:5000] + f"\n... [truncated {len(src) - 5000} more chars]"
        return {"result": result}

    async def _handler_screenshot(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_screenshot

        result = await app_screenshot(appium_url=_APPIUM_URL, session_id=session_id)
        b64 = result.get("screenshot_base64", "")
        if len(b64) > 500:
            result["screenshot_base64"] = b64[:200] + f"... [truncated {len(b64) - 200} chars]"
        return {"result": result}

    async def _handler_tap(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_tap

        result = await app_tap(
            selector=str(input_data.get("selector", "")),
            strategy=str(input_data.get("strategy", "accessibility_id")),
            appium_url=_APPIUM_URL,
            session_id=session_id,
        )
        return {"result": result}

    async def _handler_type(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_type

        result = await app_type(
            selector=str(input_data.get("selector", "")),
            text=str(input_data.get("text", "")),
            strategy=str(input_data.get("strategy", "accessibility_id")),
            appium_url=_APPIUM_URL,
            session_id=session_id,
        )
        return {"result": result}

    async def _handler_swipe(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_swipe

        result = await app_swipe(
            start_x=int(input_data.get("start_x", 0)),
            start_y=int(input_data.get("start_y", 0)),
            end_x=int(input_data.get("end_x", 0)),
            end_y=int(input_data.get("end_y", 0)),
            duration=int(input_data.get("duration", 500)),
            appium_url=_APPIUM_URL,
            session_id=session_id,
        )
        return {"result": result}

    async def _handler_assert(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_assert_element

        result = await app_assert_element(
            selector=str(input_data.get("selector", "")),
            assertion=str(input_data.get("assertion", "visible")),
            expected=input_data.get("expected"),
            strategy=str(input_data.get("strategy", "accessibility_id")),
            appium_url=_APPIUM_URL,
            session_id=session_id,
        )
        return {"result": result}

    async def _handler_install(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_install

        result = await app_install(
            app_path=str(input_data.get("app_path", "")),
            appium_url=_APPIUM_URL,
            session_id=session_id,
        )
        return {"result": result}

    # 注册所有 handler
    register_tool_handler("app_get_source", _handler_source)
    register_tool_handler("app_screenshot", _handler_screenshot)
    register_tool_handler("app_tap", _handler_tap)
    register_tool_handler("app_type", _handler_type)
    register_tool_handler("app_swipe", _handler_swipe)
    register_tool_handler("app_assert_element", _handler_assert)
    register_tool_handler("app_install", _handler_install)

    # 返回 dispatch_fn
    async def dispatch_fn(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return {"error": f"Unknown tool: {tool_name}"}
        return await handler(tool_input)

    return dispatch_fn


async def execute_natural_language(query: str) -> dict[str, Any]:
    """执行自然语言测试任务。

    参数:
        query: 用户的自然语言描述，如 "测试一下 android 搜索框功能"

    返回:
        包含测试结果和会话信息的字典
    """
    import time

    from testagent.agent.loop import agent_loop
    from testagent.config.settings import get_settings
    from testagent.llm.local_provider import LLMProviderFactory

    print("  Appium 服务器健康检查...")
    healthy = await _check_appium_health()
    if not healthy:
        return {
            "status": "failed",
            "error": "Appium 服务器不可用，请先启动 Appium (appium)",
            "session_id": None,
        }

    print("  创建 Appium 会话...")
    session_id = await _create_session()
    if not session_id:
        return {
            "status": "failed",
            "error": "无法在模拟器上创建 Appium 会话，请检查模拟器是否运行",
            "session_id": None,
        }
    print(f"  会话已创建: {session_id[:8]}...")
    await asyncio.sleep(2)  # 等 session 就绪

    # 初始化 LLM
    settings = get_settings()
    llm = LLMProviderFactory.create(settings)

    # 注册工具处理器
    dispatch_fn = _register_tool_handlers(session_id)

    # 构建消息
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": query},
    ]

    start_time = time.monotonic()
    print(f"  Agent 开始执行: \"{query}\"\n")

    try:
        result_messages = await agent_loop(
            messages=messages,
            tools=APPIUM_TOOLS,
            system=_SYSTEM_PROMPT,
            llm_provider=llm,
            dispatch_fn=dispatch_fn,
            max_rounds=30,
        )
    except Exception as exc:
        logger.error("Agent loop failed", extra={"extra_data": {"error": str(exc)}})
        await _close_session(session_id)
        return {
            "status": "error",
            "error": f"测试执行出错: {exc}",
            "session_id": session_id,
        }

    duration = time.monotonic() - start_time

    # 关闭会话
    await _close_session(session_id)

    # 提取结果
    assistant_msgs = [m for m in result_messages if m.get("role") == "assistant"]
    final_content = ""
    if assistant_msgs:
        last = assistant_msgs[-1]
        content = last.get("content", "")
        if isinstance(content, list):
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            final_content = "\n".join(texts)
        elif isinstance(content, str):
            final_content = content

    return {
        "status": "completed",
        "session_id": session_id,
        "duration": f"{duration:.1f}s",
        "summary": final_content,
        "message_count": len(result_messages),
    }


async def interactive_chat() -> None:
    """交互式自然语言测试聊天模式。"""
    from testagent.agent.loop import agent_loop
    from testagent.config.settings import get_settings
    from testagent.llm.local_provider import LLMProviderFactory

    # 检查 Appium
    print("  Appium 健康检查...")
    healthy = await _check_appium_health()
    if not healthy:
        print("  ! Appium 服务器不可用，请先启动 Appium")
        print("  ! 工具调用将不可用，仅支持对话\n")
    else:
        print("  Appium 已连接\n")

    session_id = None
    settings = get_settings()
    llm = LLMProviderFactory.create(settings)

    messages: list[dict[str, Any]] = []
    dispatch_fn = None

    print("  TestAgent 交互测试模式 — 输入 'exit' 退出, 'clear' 清除历史, 'new' 重建会话")
    print("  " + "-" * 50)

    while True:
        try:
            user_input = input("  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Goodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break
        if user_input.lower() == "clear":
            messages.clear()
            print("  Agent: 聊天历史已清除\n")
            continue
        if user_input.lower() == "new":
            if session_id:
                await _close_session(session_id)
                session_id = None
            dispatch_fn = None
            messages.clear()
            print("  Agent: 已重置会话\n")
            continue

        # 是否需要创建 Appium 会话
        if dispatch_fn is None and healthy:
            session_id = await _create_session()
            if session_id:
                await asyncio.sleep(2)
                dispatch_fn = _register_tool_handlers(session_id)
                print(f"  [Appium 会话已创建: {session_id[:8]}...]")
            else:
                print("  [无法创建 Appium 会话，将以对话模式运行]")

        # 传给 agent 的 tools（有会话时才给 Appium 工具）
        tools = APPIUM_TOOLS if dispatch_fn is not None else []

        messages.append({"role": "user", "content": user_input})

        try:
            result = await agent_loop(
                messages=messages,
                tools=tools,
                system=_SYSTEM_PROMPT,
                llm_provider=llm,
                dispatch_fn=dispatch_fn,
                max_rounds=30,
            )

            # 提取最终回复
            assistant_msgs = [m for m in result if m.get("role") == "assistant"]
            if assistant_msgs:
                last = assistant_msgs[-1]
                content = last.get("content", "")
                if isinstance(content, list):
                    texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    reply = "\n".join(texts)
                else:
                    reply = str(content)
                print(f"\n  Agent: {reply}\n")
        except Exception as exc:
            print(f"\n  [错误: {exc}]\n")

    # 清理
    if session_id:
        await _close_session(session_id)

    print("  Goodbye!")
