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
        "description": "点击指定选择器的元素，或点击屏幕上的坐标点 (x, y)。用 vision_find_element 找到目标后传入返回的 center 坐标进行点击。",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "元素选择器（resource-id / content-desc / xpath），与坐标二选一"},
                "strategy": {
                    "type": "string",
                    "description": "定位策略：accessibility_id / uiautomator / xpath",
                    "enum": ["accessibility_id", "uiautomator", "xpath"],
                },
                "x": {"type": "integer", "description": "点击的 X 坐标（与 selector 二选一，vision_find_element 返回的 center.x）"},
                "y": {"type": "integer", "description": "点击的 Y 坐标（与 selector 二选一，vision_find_element 返回的 center.y）"},
            },
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
]

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
        logger.error(
            "Session creation error",
            extra={
                "extra_data": {
                    "error": str(exc) or type(exc).__name__,
                    "error_type": type(exc).__name__,
                }
            },
        )
        return None


async def _close_session(session_id: str) -> None:
    """关闭 Appium 会话。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.delete(f"{_APPIUM_URL}/session/{session_id}")
    except Exception:
        pass


def _register_tool_handlers(
    session_id: str,
    glm_client: Any = None,
) -> Callable[[str, dict[str, Any]], Any]:
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
        return {"result": result}

    async def _handler_tap(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_tap

        x_raw = input_data.get("x")
        y_raw = input_data.get("y")
        x = int(x_raw) if x_raw is not None else None
        y = int(y_raw) if y_raw is not None else None

        result = await app_tap(
            selector=str(input_data.get("selector", "")),
            strategy=str(input_data.get("strategy", "accessibility_id")),
            x=x,
            y=y,
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

    # ── Vision tool handlers ────────────────────────────────────
    if glm_client is not None:

        async def _handler_vision_find(input_data: dict[str, Any]) -> dict[str, Any]:
            from testagent.mcp_servers.vision_server.tools import vision_find_element

            result = await vision_find_element(
                image=str(input_data.get("image", "")),
                target=str(input_data.get("target", "")),
                context=input_data.get("context"),
                glm_client=glm_client,
            )
            return {"result": result}

        async def _handler_vision_describe(input_data: dict[str, Any]) -> dict[str, Any]:
            from testagent.mcp_servers.vision_server.tools import vision_describe_screen

            result = await vision_describe_screen(
                image=str(input_data.get("image", "")),
                glm_client=glm_client,
            )
            return {"result": result}

        register_tool_handler("vision_find_element", _handler_vision_find)
        register_tool_handler("vision_describe_screen", _handler_vision_describe)

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

    # 初始化 LLM 和 Vision
    settings = get_settings()
    llm = LLMProviderFactory.create(settings)

    from testagent.mcp_servers.vision_server.glm_client import GLMClient

    glm_client: Any = None
    vision_key = settings.vision_api_key.get_secret_value()
    if vision_key:
        glm_client = GLMClient(
            api_key=vision_key,
            api_url=settings.vision_api_url,
            model=settings.vision_model,
            timeout=settings.vision_timeout,
            max_retries=settings.vision_max_retries,
        )

    # 注册工具处理器
    dispatch_fn = _register_tool_handlers(session_id, glm_client=glm_client)

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

    from testagent.mcp_servers.vision_server.glm_client import GLMClient

    glm_client: Any = None
    vision_key = settings.vision_api_key.get_secret_value()
    if vision_key:
        glm_client = GLMClient(
            api_key=vision_key,
            api_url=settings.vision_api_url,
            model=settings.vision_model,
            timeout=settings.vision_timeout,
            max_retries=settings.vision_max_retries,
        )

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
                dispatch_fn = _register_tool_handlers(session_id, glm_client=glm_client)
                print(f"  [Appium 会话已创建: {session_id[:8]}...]")
            else:
                print("  [无法创建 Appium 会话，将以对话模式运行]")

        # 传给 agent 的 tools（有会话时才给 Appium 工具）
        tools = APPIUM_TOOLS if dispatch_fn is not None else []

        messages.append({"role": "user", "content": user_input})

        def _on_progress(round_info: dict[str, Any], tool_results: list[dict[str, Any]]) -> None:
            """Print intermediate agent progress in real-time."""
            assistant_msg = round_info.get("assistant", {})
            content = assistant_msg.get("content") or ""
            tool_calls = round_info.get("tool_calls", [])
            is_final = round_info.get("final", False)

            # Print LLM text output
            if content:
                text = str(content)
                if isinstance(content, list):
                    texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    text = "\n".join(texts)
                if text.strip():
                    print(f"  Agent: {text.strip()}")

            # Print tool calls
            for tc in (tool_calls or []):
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args_str = fn.get("arguments", "{}")
                print(f"  -> 调用工具: {name}({args_str[:200]})")

            # Print tool results (first 300 chars each)
            for tr in (tool_results or []):
                tr_str = json.dumps(tr, ensure_ascii=False)
                if len(tr_str) > 300:
                    tr_str = tr_str[:297] + "..."
                print(f"  <- 结果: {tr_str}")

            if is_final:
                print()

        try:
            await agent_loop(
                messages=messages,
                tools=tools,
                system=_SYSTEM_PROMPT,
                llm_provider=llm,
                dispatch_fn=dispatch_fn,
                max_rounds=30,
                progress_callback=_on_progress,
            )
        except Exception as exc:
            print(f"\n  [错误: {exc}]\n")

    # 清理
    if session_id:
        await _close_session(session_id)

    print("  Goodbye!")
