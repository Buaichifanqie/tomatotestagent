from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

import httpx

from testagent.common.errors import LLMTokenLimitError
from testagent.common.logging import get_logger
from testagent.db_toolkit.connection import ConnectionManager
from testagent.db_toolkit.env import detect_environment
from testagent.db_toolkit.models import DbEnv, Environment
from testagent.db_toolkit.tools import (
    DB_TOOL_DEFINITIONS,
    ToolkitState,
    handle_db_cleanup,
    handle_db_execute,
    handle_db_inspect,
    handle_db_query,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)

_APPIUM_URL = "http://localhost:4723"

# 禁用 Windows 系统代理对 localhost 的影响（httpx 在 Windows 上会自动走系统代理）
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,0.0.0.0")

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
        "name": "app_launch",
        "description": "通过包名直接启动应用，比截图找图标点击更快更稳定。例如: app_launch(package='com.example.app')",
        "parameters": {
            "type": "object",
            "properties": {
                "package": {"type": "string", "description": "应用包名，如 com.example.app"},
                "activity": {"type": "string", "description": "可选，Activity 名称，如 .MainActivity"},
            },
            "required": ["package"],
        },
    },
    {
        "name": "app_exec",
        "description": "在设备上执行 shell 命令（通过 Appium mobile:shell）。适用于快速操作如开关WiFi、检查设备状态等。示例: svc wifi enable / svc wifi disable / input keyevent KEYCODE_HOME / dumpsys battery",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "vision_find_element",
        "description": "通过视觉分析在截图中查找目标 UI 元素。先用 app_screenshot 获取截图得到 screenshot_id，再传入此工具进行分析。返回元素坐标和导航建议。",
        "parameters": {
            "type": "object",
            "properties": {
                "screenshot_id": {"type": "string", "description": "app_screenshot 返回的截图引用 ID（推荐方式）"},
                "target": {"type": "string", "description": "要查找的目标的自然语言描述，如'美团 app 图标'"},
                "context": {"type": "string", "description": "可选的上下文信息"},
            },
            "required": ["screenshot_id", "target"],
        },
    },
    {
        "name": "vision_describe_screen",
        "description": "通过视觉分析描述当前屏幕的所有内容和布局。先用 app_screenshot 获取截图得到 screenshot_id，再传入此工具进行分析。",
        "parameters": {
            "type": "object",
            "properties": {
                "screenshot_id": {"type": "string", "description": "app_screenshot 返回的截图引用 ID（推荐方式）"},
            },
            "required": ["screenshot_id"],
        },
    },
    {
        "name": "app_wait",
        "description": "等待指定秒数让界面加载或动画完成。启动应用后、点击后、滑动后，等待界面稳定后再进行下一步。",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "等待秒数，默认 2 秒"},
            },
            "required": [],
        },
    },
    {
        "name": "run_single_plan",
        "description": "对单个需求文档执行完整测试流程：解析PRD → 生成用例 → 执行 → 生成报告。支持本地文件路径、URL链接、或直接粘贴的需求文本。当用户给出多个文档时，逐个调用此工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "requirement": {
                    "type": "string",
                    "description": "需求文档的路径、URL、或需求描述文本",
                },
                "name": {
                    "type": "string",
                    "description": "可选，自定义测试计划名称",
                },
            },
            "required": ["requirement"],
        },
    },
]

def _build_system_prompt() -> str:
    """动态构建 system prompt，根据配置注入 DB toolkit 信息。"""
    from testagent.config.settings import get_settings

    prompt = """\
You are TestAgent, an AI-powered mobile testing assistant connected to an Android emulator via Appium.

## Available Tools
- **app_launch(package, activity?)** — 通过包名启动应用
- **app_exec(command)** — 在设备上执行 shell 命令
- **app_get_source()** — 获取当前屏幕 XML 页面源码
- **app_screenshot()** — 截取当前屏幕截图，返回 screenshot_id
- **app_tap(selector?, x?, y?, strategy?)** — 点击元素或坐标。如有 text/content-desc，用 strategy="uiautomator" 传 selector 更精准
- **app_type(selector, text, strategy?)** — 向输入框输入文本
- **app_swipe(start_x, start_y, end_x, end_y, duration?)** — 滑动手势
- **app_assert_element(selector, assertion, expected?, strategy?)** — 断言元素状态
- **app_wait(seconds?)** — 等待指定秒数让界面加载稳定
- **vision_find_element(screenshot_id, target, context?)** — 在截图中视觉查找目标 UI 元素，返回坐标
- **vision_describe_screen(screenshot_id)** — 视觉描述当前屏幕内容
- **run_single_plan(requirement, name?)** — 对单个需求文档执行完整测试流程（PRD解析→用例生成→执行→报告）"""

    # 动态注入 DB toolkit 工具描述
    settings = get_settings()
    app_db_url = settings.app_db_url
    if app_db_url:
        prompt += f"""

## 数据库工具
- **db_inspect(connection_url, tables?, include_sample?, sample_limit?)** — 查看数据库表结构、列信息和样本数据
- **db_query(connection_url, sql, params?)** — 执行 SELECT 查询读取数据（自动添加 LIMIT 保护）
- **db_execute(connection_url, sql, params?, confirm?)** — 执行写操作 INSERT/UPDATE/DELETE（仅测试环境可用，先 preview 再 confirm）
- **db_cleanup(connection_url)** — 清理本次会话中创建的测试数据

## 数据库连接
当前测试数据库连接 URL: `{app_db_url}`
进行数据库操作时，直接使用上述 URL 作为 connection_url 参数，无需询问用户。

## 数据库操作行为规范
每次执行写操作（db_execute）后，必须立即用 db_query 查询受影响的表，展示操作后的完整数据状态。
例如：INSERT 后查询该表所有数据，UPDATE 后查询被修改的行，DELETE 后查询确认已删除。
这样用户可以直观看到每次操作的效果。"""

    prompt += """

## Notes
- app_launch 成功后会自动等待 3 秒让应用加载
- app_tap / app_swipe 成功后会自动等待 2 秒让界面稳定
- Session 过期会自动恢复，无需人工干预

## 批量测试能力
当用户给出多个需求文档时（多个文件路径、多个URL、或多段需求文本），
你应该：
1. 识别并提取所有需求文档
2. 逐个调用 run_single_plan 工具执行测试
3. 最后汇总所有结果，给出总结报告

示例：
  用户："请测试 doc1.md doc2.md doc3.md"
  → 调用 run_single_plan("doc1.md")
  → 调用 run_single_plan("doc2.md")
  → 调用 run_single_plan("doc3.md")
  → 汇总输出

注意：
- 如果用户没有特别说明顺序，按文档列出顺序执行
- 如果用户说"先测X再测Y"，尊重用户指定的顺序
- 每个文档测试完后简要汇报进度
- 单个文档失败不影响其他文档，继续执行剩余文档
- 对于单个需求文档，同样使用 run_single_plan 工具
"""
    return prompt


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


def _ensure_android_home() -> str | None:
    """Auto-detect Android SDK path and set ANDROID_HOME / ANDROID_SDK_ROOT if not already set."""
    if os.environ.get("ANDROID_HOME") and os.environ.get("ANDROID_SDK_ROOT"):
        return os.environ["ANDROID_HOME"]

    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk"),
        os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Android", "Sdk"),
        "C:\\Android\\Sdk",
        os.path.expanduser("~/Android/Sdk"),
    ]

    for path in candidates:
        if not path:
            continue
        if os.path.isdir(os.path.join(path, "platform-tools")):
            os.environ["ANDROID_HOME"] = path
            os.environ["ANDROID_SDK_ROOT"] = path
            logger.debug(
                "Auto-detected Android SDK",
                extra={"extra_data": {"path": path}},
            )
            return path
    return None


_appium_process: asyncio.subprocess.Process | None = None

# Device screen dimensions — fetched once at session init, cached globally
_device_width: int = 1080
_device_height: int = 2400


def _find_appium() -> str:
    """查找 Appium 可执行文件路径。

    在 Windows 上 npm 安装的是 appium.cmd（批处理文件），
    create_subprocess_exec 需要完整路径才能正确执行。
    """
    # 先尝试 shutil.which() — 跨平台
    resolved = shutil.which("appium")
    if resolved:
        return resolved
    # Windows 下 npm 全局安装目录
    if platform.system() == "Windows":
        npm_dir = os.path.join(os.environ.get("APPDATA", ""), "npm")
        for name in ("appium.cmd", "appium"):
            full = os.path.join(npm_dir, name)
            if os.path.isfile(full):
                return full
        # 也检查 LOCALAPPDATA\npm
        npm_dir2 = os.path.join(os.environ.get("LOCALAPPDATA", ""), "npm")
        for name in ("appium.cmd", "appium"):
            full = os.path.join(npm_dir2, name)
            if os.path.isfile(full):
                return full
    return "appium"  # fallback, maybe Unix PATH works


async def _kill_process_on_port(port: int) -> None:
    """Kill any process listening on the given port.

    策略：
    1. Windows: netstat → taskkill（最通用，无语言问题）
    2. Unix: pkill / lsof + kill
    """
    pids: set[str] = set()

    if platform.system() == "Windows":
        # 方法 A: netstat（最通用，所有 Windows 版本可用）
        try:
            proc = await asyncio.create_subprocess_exec(
                "netstat", "-ano",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode("utf-8", errors="replace")
            for line in output.splitlines():
                if f":{port}" in line and ("LISTENING" in line or "ESTABLISHED" in line):
                    parts = line.strip().split()
                    if parts:
                        pid = parts[-1]
                        if pid.isdigit():
                            pids.add(pid)
        except Exception:
            pass

        # 方法 B: PowerShell（备用）
        if not pids:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "powershell", "-NoProfile", "-NonInteractive", "-Command",
                    f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue "
                    f"| Select-Object -ExpandProperty OwningProcess",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await proc.communicate()
                for pid in stdout.decode().strip().splitlines():
                    pid = pid.strip()
                    if pid and pid.isdigit():
                        pids.add(pid)
            except Exception:
                pass

        # 杀进程
        for pid in pids:
            try:
                await asyncio.create_subprocess_exec(
                    "taskkill", "/F", "/PID", pid,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except Exception:
                pass
    else:
        try:
            await asyncio.create_subprocess_exec(
                "pkill", "-f", "appium",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception:
            pass

    if pids:
        await asyncio.sleep(1)


async def _ensure_appium_running() -> bool:
    """Ensure Appium server is running with ANDROID_HOME set.

    策略：
    1. 先检查已有 Appium 是否可用（仅 /status 检查，不创建 session）
    2. 如果不可用，杀掉旧进程、启动新实例
    3. 等待新实例就绪
    """
    global _appium_process

    # ── 步骤 1: 检查已有 Appium ──
    for _ in range(5):
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{_APPIUM_URL}/status")
            if resp.status_code == 200:
                # 验证 Appium 是否能真正创建 session（测试 ANDROID_HOME 等环境是否就绪）
                test_sid = await _create_session()
                if test_sid:
                    await _close_session(test_sid)
                    logger.info("Existing Appium is healthy, session verified")
                    return True
                logger.warning("Appium server is up but session creation failed, will restart...")
                break
        except (httpx.RequestError, httpx.TimeoutException):
            pass
        await asyncio.sleep(1)

    # ── 步骤 2: 已有 Appium 不可用，杀旧进程 ──
    logger.info("Existing Appium not available, restarting...")

    if _appium_process and _appium_process.returncode is None:
        _appium_process.kill()
        try:
            await asyncio.wait_for(_appium_process.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        _appium_process = None

    await _kill_process_on_port(4723)
    await asyncio.sleep(2)

    # ── 步骤 3: 启动新 Appium ──
    android_home = _ensure_android_home()
    extra_env = {}
    if android_home:
        extra_env["ANDROID_HOME"] = android_home
        extra_env["ANDROID_SDK_ROOT"] = android_home

    appium_path = _find_appium()
    env = {**os.environ, **extra_env}

    if platform.system() == "Windows" and android_home:
        import tempfile

        wrapper = os.path.join(tempfile.gettempdir(), "_appium_testagent_wrapper.bat")
        with open(wrapper, "w", encoding="ascii") as f:
            f.write(
                f'@echo off\r\n'
                f'set "ANDROID_HOME={android_home}"\r\n'
                f'set "ANDROID_SDK_ROOT={android_home}"\r\n'
                f'"{appium_path}" --allow-insecure "*:adb_shell" %*\r\n'
            )
        _appium_process = await asyncio.create_subprocess_exec(
            wrapper,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
    elif appium_path.endswith(".cmd"):
        _appium_process = await asyncio.create_subprocess_shell(
            appium_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
    else:
        _appium_process = await asyncio.create_subprocess_exec(
            appium_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )

    # ── 步骤 4: 等待就绪 ──
    for _ in range(30):
        await asyncio.sleep(1)
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                resp = await client.get(f"{_APPIUM_URL}/status")
            if resp.status_code == 200:
                test_sid = await _create_session()
                if test_sid:
                    await _close_session(test_sid)
                    logger.info("Appium started with ANDROID_HOME, session verified OK")
                    return True
                logger.warning("Appium server is up but session creation failed, waiting...")
        except httpx.RequestError:
            continue

    logger.error("Failed to start Appium or create test session")
    return False


async def _create_session() -> str | None:
    """在 Android 模拟器上创建 Appium 会话。

    尝试多种 capability 格式以兼容不同 Appium 版本：
    1. 先试 appium:androidHome（Appium 2.x 标准格式）
    2. 再试 androidHome（部分旧版兼容）
    """
    android_home = _ensure_android_home()
    # Auto-detect first connected device UDID
    import subprocess as _sp
    _detected_udid = "emulator-5554"  # fallback default
    try:
        _dev_result = _sp.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
        for _line in _dev_result.stdout.splitlines():
            _parts = _line.strip().split()
            if len(_parts) >= 2 and _parts[1] == "device":
                _detected_udid = _parts[0]
                break
    except Exception:
        pass

    always_match: dict[str, object] = {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:deviceName": _detected_udid,
        "appium:udid": _detected_udid,
        "appium:noReset": True,
        "appium:autoGrantPermissions": True,
        "appium:newCommandTimeout": 120,
        # 启用 adb_shell 功能（Appium 3.x 格式：*:adb_shell）
        "appium:allowInsecure": "*:adb_shell",
    }

    # Build capability variants for ANDROID_HOME
    cap_variants: list[dict[str, object]] = [dict(always_match)]
    if android_home:
        cap_variants[0]["appium:androidHome"] = android_home
        # Also try without appium: prefix (some Appium versions)
        alt = dict(always_match)
        alt["androidHome"] = android_home
        cap_variants.append(alt)

    for caps in cap_variants:
        capabilities = {"capabilities": {"alwaysMatch": caps, "firstMatch": [{}]}}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_APPIUM_URL}/session", json=capabilities)
                if resp.status_code == 200:
                    data = resp.json()
                    sid = data.get("value", {}).get("sessionId") or data.get("sessionId")
                    if sid:
                        return sid
                body = resp.text[:200]
                logger.warning(
                    "Session creation failed",
                    extra={"extra_data": {"status": resp.status_code, "body": body}},
                )
        except Exception as exc:
            logger.error(
                "Session creation error",
                extra={"extra_data": {"error": str(exc) or type(exc).__name__}},
            )

    return None


async def _close_session(session_id: str) -> None:
    """关闭 Appium 会话。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.delete(f"{_APPIUM_URL}/session/{session_id}")
    except Exception:
        pass


async def _get_device_screen_size(session_id: str) -> tuple[int, int]:
    """通过 ADB 获取设备真实屏幕分辨率（物理像素）。

    返回 (width, height)，失败时返回默认 (1080, 2400)。
    """
    from testagent.mcp_servers.appium_server.tools import app_exec

    try:
        result = await app_exec(
            command="wm size",
            appium_url=_APPIUM_URL,
            session_id=session_id,
        )
        body = result.get("body", {})
        value = str(body.get("value", ""))
        # "Physical size: 1080x2400" or "1080x2400"
        m = re.search(r"(\d+)x(\d+)", value)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            logger.debug(
                "Device screen size",
                extra={"extra_data": {"width": w, "height": h}},
            )
            return w, h
    except Exception as exc:
        logger.warning(
            "Failed to get device screen size",
            extra={"extra_data": {"error": str(exc)}},
        )
    return 1080, 2400


def _register_tool_handlers(
    session_id: str,
    vision_client: Any = None,
    device_width: int | None = None,
    device_height: int | None = None,
) -> Callable[[str, dict[str, Any]], Any]:
    """注册感知真实 session_id 的 Appium 工具处理器。

    返回 dispatch_fn 函数，可直接传给 agent_loop。
    """
    global _device_width, _device_height
    if device_width is not None:
        _device_width = device_width
    if device_height is not None:
        _device_height = device_height
    dw = _device_width
    dh = _device_height
    from testagent.agent.loop import TOOL_HANDLERS, register_tool_handler

    async def _handler_source(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_get_source

        result = await app_get_source(appium_url=_APPIUM_URL, session_id=session_id)
        src = result.get("source", "")
        if len(src) > 2500:
            result["source"] = src[:2500] + f"\n... [truncated {len(src) - 2500} more chars]"
        return {"result": result}

    async def _handler_screenshot(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_screenshot

        result = await app_screenshot(appium_url=_APPIUM_URL, session_id=session_id)
        return {"result": result}

    async def _handler_tap(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_exec, app_tap

        x_raw = input_data.get("x")
        y_raw = input_data.get("y")
        x = int(x_raw) if x_raw is not None else None
        y = int(y_raw) if y_raw is not None else None

        # 坐标点击：精确点击，不做任何偏移或 XML 元素查找
        if x is not None and y is not None:
            result = await app_exec(
                command=f"input tap {x} {y}",
                appium_url=_APPIUM_URL, session_id=session_id,
            )
            await asyncio.sleep(2)
            return {"result": result, "method": "adb"}

        result = await app_tap(
            selector=str(input_data.get("selector", "")),
            strategy=str(input_data.get("strategy", "accessibility_id")),
            appium_url=_APPIUM_URL,
            session_id=session_id,
        )
        # 点击后等待界面稳定再返回
        if result.get("status_code") == 200 or "status_code" not in result:
            await asyncio.sleep(2)
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
        if result.get("status_code") == 200 or "status_code" not in result:
            await asyncio.sleep(2)
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

    async def _handler_launch(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_launch

        result = await app_launch(
            package=str(input_data.get("package", "")),
            activity=str(input_data.get("activity", "")),
            appium_url=_APPIUM_URL,
            session_id=session_id,
        )
        # 启动应用后等待 5 秒让界面加载完成（冷启动可能需要较长时间）
        if result.get("result", "").startswith("App"):
            await asyncio.sleep(5)
        return {"result": result}

    async def _handler_exec(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_exec

        result = await app_exec(
            command=str(input_data.get("command", "")),
            appium_url=_APPIUM_URL,
            session_id=session_id,
        )
        return {"result": result}

    async def _handler_wait(input_data: dict[str, Any]) -> dict[str, Any]:
        seconds = int(input_data.get("seconds", 2))
        await asyncio.sleep(seconds)
        return {"result": {"waited": seconds, "message": f"已等待 {seconds} 秒"}}

    # 注册所有 handler
    register_tool_handler("app_get_source", _handler_source)
    register_tool_handler("app_screenshot", _handler_screenshot)
    register_tool_handler("app_tap", _handler_tap)
    register_tool_handler("app_type", _handler_type)
    register_tool_handler("app_swipe", _handler_swipe)
    register_tool_handler("app_assert_element", _handler_assert)
    register_tool_handler("app_install", _handler_install)
    register_tool_handler("app_launch", _handler_launch)
    register_tool_handler("app_exec", _handler_exec)
    register_tool_handler("app_wait", _handler_wait)

    async def _handler_run_single_plan(input_data: dict[str, Any]) -> dict[str, Any]:
        from testagent.cli.plan import run_single_plan

        req = str(input_data.get("requirement", ""))
        plan_name = str(input_data.get("name", ""))

        if not req:
            return {"error": "Missing 'requirement' parameter"}

        result = await run_single_plan(
            requirement=req,
            name=plan_name,
            auto_yes=True,
            log_fn=lambda msg: print(f"  [plan] {msg}"),
        )

        return {
            "status": result.status,
            "requirement": result.requirement_source,
            "summary": result.summary,
            "report_path": result.report_path,
            "case_count": result.case_count,
            "passed": result.passed,
            "failed": result.failed,
            "duration": result.duration,
            "error": result.error,
        }

    register_tool_handler("run_single_plan", _handler_run_single_plan)

    # ── Vision tool handlers ────────────────────────────────────
    if vision_client is not None:

        async def _handler_vision_find(input_data: dict[str, Any]) -> dict[str, Any]:
            from testagent.mcp_servers.vision_server.tools import vision_find_element

            result = await vision_find_element(
                screenshot_id=input_data.get("screenshot_id"),
                image=input_data.get("image"),
                target=str(input_data.get("target", "")),
                context=input_data.get("context"),
                vision_client=vision_client,
                device_width=dw,
                device_height=dh,
            )
            return {"result": result}

        async def _handler_vision_describe(input_data: dict[str, Any]) -> dict[str, Any]:
            from testagent.mcp_servers.vision_server.tools import vision_describe_screen

            result = await vision_describe_screen(
                screenshot_id=input_data.get("screenshot_id"),
                image=input_data.get("image"),
                vision_client=vision_client,
                device_width=dw,
                device_height=dh,
            )
            return {"result": result}

        register_tool_handler("vision_find_element", _handler_vision_find)
        register_tool_handler("vision_describe_screen", _handler_vision_describe)

    # Register DB toolkit tools
    from testagent.config.settings import get_settings
    _settings = get_settings()
    _app_db_url = _settings.app_db_url

    db_conn_mgr = ConnectionManager()
    db_env = detect_environment(_app_db_url) if _app_db_url else DbEnv(
        level=Environment.PRODUCTION, connection_url="", detected_by="default",
    )
    db_state = ToolkitState(env=db_env, conn_manager=db_conn_mgr)

    def _make_db_handler(fn):
        async def _handler(input_data: dict[str, Any]) -> dict[str, Any]:
            return await fn(db_state, input_data)
        return _handler

    register_tool_handler("db_inspect", _make_db_handler(handle_db_inspect))
    register_tool_handler("db_query", _make_db_handler(handle_db_query))
    register_tool_handler("db_execute", _make_db_handler(handle_db_execute))
    register_tool_handler("db_cleanup", _make_db_handler(handle_db_cleanup))

    # 返回 dispatch_fn
    async def dispatch_fn(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return {"error": f"Unknown tool: {tool_name}"}
        return await handler(tool_input)

    return dispatch_fn


async def _recover_and_retry(
    tool_name: str,
    tool_input: dict[str, Any],
    vision_client: Any,
) -> tuple[dict[str, Any], str | None, object]:
    """Close dead session, create new one, re-register handlers, retry tool call."""
    logger.info("Session dead, attempting auto-recovery...")
    new_sid = await _create_session()
    if not new_sid:
        return {"error": "Session recovery failed: could not create new session"}, None, None
    await asyncio.sleep(2)
    global _device_width, _device_height
    _device_width, _device_height = await _get_device_screen_size(new_sid)
    new_dispatch_fn = _register_tool_handlers(new_sid, vision_client=vision_client)

    # Retry the failed call with fresh session
    handler = None
    from testagent.agent.loop import TOOL_HANDLERS

    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return {"error": f"Unknown tool: {tool_name}"}, new_sid, new_dispatch_fn
    retry_result = await handler(tool_input)
    logger.info(
        "Session auto-recovery successful",
        extra={"extra_data": {"new_session": new_sid[:8]}},
    )
    return retry_result, new_sid, new_dispatch_fn


def _wrap_with_session_recovery(
    dispatch_fn: object,
    session_id: str | None,
    vision_client: Any,
) -> object:
    """Wrap dispatch function with automatic session recovery.

    When a tool call returns "invalid session id", automatically creates a
    new Appium session, re-registers handlers, and retries the failed call.
    """
    import json as _json

    async def _wrapped(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        nonlocal dispatch_fn, session_id

        result = await dispatch_fn(tool_name, tool_input)
        result_str = _json.dumps(result, ensure_ascii=False)

        _session_dead_patterns = (
            "invalid session id",
            "session is either terminated",
            "instrumentation process is not running",
            "cannot be proxied to UiAutomator2",
            "has already been deleted",
        )
        if any(p in result_str for p in _session_dead_patterns):
            retry_result, new_sid, new_dispatch = await _recover_and_retry(
                tool_name, tool_input, vision_client
            )
            if new_sid:
                session_id = new_sid
                dispatch_fn = new_dispatch
                return retry_result

        return result

    return _wrapped


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
    appium_ok = await _ensure_appium_running()
    if not appium_ok:
        return {
            "status": "failed",
            "error": "无法启动 Appium 服务器或创建 Android 会话，请检查模拟器是否运行",
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

    from testagent.mcp_servers.vision_server.volcano_client import VolcanoVisionClient

    vision_client: Any = None
    vision_key = settings.vision_api_key.get_secret_value()
    if vision_key:
        vision_client = VolcanoVisionClient(
            api_key=vision_key,
            api_url=settings.vision_api_url,
            model=settings.vision_model,
            timeout=settings.vision_timeout,
            max_retries=settings.vision_max_retries,
        )

    # 获取设备分辨率并注册工具处理器
    global _device_width, _device_height
    _device_width, _device_height = await _get_device_screen_size(session_id)
    dispatch_fn = _register_tool_handlers(session_id, vision_client=vision_client,
                                          device_width=_device_width, device_height=_device_height)

    # 构建消息
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": query},
    ]

    # Wrap dispatch with auto session recovery
    safe_dispatch = _wrap_with_session_recovery(dispatch_fn, session_id, vision_client)

    start_time = time.monotonic()
    print(f"  Agent 开始执行: \"{query}\"\n")

    try:
        result_messages = await agent_loop(
            messages=messages,
            tools=APPIUM_TOOLS + DB_TOOL_DEFINITIONS,
            system=_build_system_prompt(),
            llm_provider=llm,
            dispatch_fn=safe_dispatch,
            max_rounds=1000,
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

    # 检查是否被 max_rounds 截断
    if result_messages and result_messages[-1].get("tool_calls"):
        print(f"  [已执行完 {len(result_messages)} 轮但任务可能未完成，考虑分步执行]")

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


async def _check_session_alive(session_id: str) -> bool:
    """Check if UiAutomator2 instrumentation is still responsive."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{_APPIUM_URL}/session/{session_id}/source")
            if resp.status_code == 200:
                return True
            body = resp.text[:300]
            if "instrumentation process is not running" in body:
                return False
            return True
    except Exception:
        return False


async def _recover_session(
    session_id: str | None,
    vision_client: Any = None,
) -> tuple[str | None, Callable | None, Any]:
    """Close dead session and create a new one."""
    if session_id:
        await _close_session(session_id)

    new_sid = await _create_session()
    if new_sid:
        await asyncio.sleep(2)
        global _device_width, _device_height
        _device_width, _device_height = await _get_device_screen_size(new_sid)
        dispatch_fn = _register_tool_handlers(new_sid, vision_client=vision_client)
        print(f"  [会话已自动恢复: {new_sid[:8]}...]")
        return new_sid, dispatch_fn, vision_client
    return None, None, vision_client


def _clean_orphan_tool_messages(messages: list[dict[str, Any]]) -> None:
    """Remove stale tool messages and strip unfulfilled tool_calls from assistant messages.

    After auto-compact strips the middle of a conversation:
    1. Tool messages in the tail may reference tool_call_ids from assistant messages
       that were compacted away — these orphaned tool messages are removed.
    2. Assistant messages with tool_calls may be missing their tool responses
       (compacted away), which violates the LLM API requirement that every
       assistant tool_calls must be followed by tool responses — strip tool_calls
       from these assistant messages so the sequence is valid.
    """
    # Step 1: Collect tool_call_ids that still have matching tool responses
    tid_has_response: set[str] = set()
    for msg in messages:
        if msg.get("role") == "tool":
            tid = msg.get("tool_call_id", "")
            if tid:
                tid_has_response.add(tid)

    # Step 2: Remove orphaned tool messages (tool msgs with no matching assistant tool_call)
    # First collect all valid tool_call_ids from assistant messages
    valid_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in (msg.get("tool_calls") or []):
                tid = tc.get("id", "") if isinstance(tc, dict) else ""
                if tid:
                    valid_ids.add(tid)

    i = len(messages) - 1
    removed = 0
    while i >= 0:
        msg = messages[i]
        if msg.get("role") == "tool":
            tid = msg.get("tool_call_id", "")
            if tid and tid not in valid_ids:
                messages.pop(i)
                removed += 1
        i -= 1

    if removed:
        logger.debug(
            "Removed orphaned tool messages",
            extra={"extra_data": {"count": removed}},
        )

    # Step 3: Strip tool_calls from assistant messages whose tool responses
    # were compacted away. Scan from the END so we can safely check "following"
    # messages (which are towards the end of the list).
    stripped = 0
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") != "assistant":
            continue
        tc_list = msg.get("tool_calls")
        if not tc_list:
            continue

        # Collect the tool_call_ids this assistant message expects
        expected_tids: set[str] = set()
        for tc in tc_list:
            tid = tc.get("id", "") if isinstance(tc, dict) else ""
            if tid:
                expected_tids.add(tid)

        if not expected_tids:
            continue

        # Check which of these tool_call_ids have tool responses AFTER this message
        found_tids: set[str] = set()
        for j in range(i + 1, len(messages)):
            later = messages[j]
            if later.get("role") == "tool":
                tid = later.get("tool_call_id", "")
                if tid in expected_tids:
                    found_tids.add(tid)

        # If any tool_calls are missing their responses, strip ALL tool_calls
        # (having partial tool responses is also invalid for the API)
        if found_tids != expected_tids:
            del msg["tool_calls"]
            stripped += 1

    if stripped:
        logger.debug(
            "Stripped tool_calls from assistant messages with missing responses",
            extra={"extra_data": {"count": stripped}},
        )


async def interactive_chat() -> None:
    """交互式自然语言测试聊天模式。"""
    global _device_width, _device_height
    from testagent.agent.loop import agent_loop
    from testagent.config.settings import get_settings
    from testagent.llm.local_provider import LLMProviderFactory

    # 检查 Appium
    print("  Appium 健康检查...")
    appium_ok = await _ensure_appium_running()
    if not appium_ok:
        print("  ! 无法启动 Appium 或创建会话，将以对话模式运行\n")
    else:
        print("  Appium 已连接\n")

    session_id = None
    settings = get_settings()
    llm = LLMProviderFactory.create(settings)

    from testagent.mcp_servers.vision_server.volcano_client import VolcanoVisionClient

    vision_client: Any = None
    vision_key = settings.vision_api_key.get_secret_value()
    if vision_key:
        vision_client = VolcanoVisionClient(
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
        if dispatch_fn is None and appium_ok:
            # 如果第一次创建失败，重试几次（Appium 可能还在初始化）
            session_id = None
            for attempt in range(5):
                session_id = await _create_session()
                if session_id:
                    break
                if attempt < 4:
                    await asyncio.sleep(2)
            if session_id:
                await asyncio.sleep(2)
                _device_width, _device_height = await _get_device_screen_size(session_id)
                dispatch_fn = _register_tool_handlers(session_id, vision_client=vision_client)
                print(f"  [Appium 会话已创建: {session_id[:8]}...]")
            else:
                # 可能是旧 Appium 没杀死，尝试重启
                print("  [无法创建 Appium 会话，尝试重启 Appium...]")
                appium_ok = await _ensure_appium_running()
                if appium_ok:
                    for attempt in range(5):
                        session_id = await _create_session()
                        if session_id:
                            break
                        if attempt < 4:
                            await asyncio.sleep(2)
                    if session_id:
                        await asyncio.sleep(2)
                        _device_width, _device_height = await _get_device_screen_size(session_id)
                        dispatch_fn = _register_tool_handlers(session_id, vision_client=vision_client)
                        print(f"  [Appium 会话已创建: {session_id[:8]}...]")
                    else:
                        print("  [仍无法创建 Appium 会话，将以对话模式运行]")
                else:
                    print("  [无法创建 Appium 会话，将以对话模式运行]")

        # 传给 agent 的 tools（有会话时才给 Appium 工具）
        tools = (APPIUM_TOOLS + DB_TOOL_DEFINITIONS) if dispatch_fn is not None else []

        # 清洗 messages 中的孤立 tool 消息：如果 tool message 之前没有匹配的
        # assistant tool_calls（auto-compact 后可能出现），移除它们以避免 LLM API 报错
        _clean_orphan_tool_messages(messages)

        messages.append({"role": "user", "content": user_input})

        # Wrap dispatch with auto session recovery
        safe_dispatch = (
            _wrap_with_session_recovery(dispatch_fn, session_id, vision_client)
            if dispatch_fn
            else None
        )

        def _safe_print(text: str) -> None:
            try:
                print(text)
            except UnicodeEncodeError:
                safe = text.encode("ascii", errors="replace").decode("ascii")
                print(safe)

        def _on_progress(round_info: dict[str, Any], tool_results: list[dict[str, Any]]) -> None:
            """Print intermediate agent progress in real-time."""
            round_n = round_info.get("round", 0)
            assistant_msg = round_info.get("assistant", {})
            content = assistant_msg.get("content") or ""
            tool_calls = round_info.get("tool_calls", [])
            is_final = round_info.get("final", False)

            # 轮次接近上限时告警
            max_r = 1000
            if round_n >= max_r - 3:
                _safe_print(f"  [警告: 已达 {round_n}/{max_r} 轮, 即将自动结束]")

            # Print LLM text output
            if content:
                text = str(content)
                if isinstance(content, list):
                    texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    text = "\n".join(texts)
                if text.strip():
                    _safe_print(f"  Agent: {text.strip()}")

            # Print tool calls
            for tc in (tool_calls or []):
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args_str = fn.get("arguments", "{}")
                _safe_print(f"  -> 调用工具: {name}({args_str[:200]})")

            # Print tool results (first 300 chars each)
            for tr in (tool_results or []):
                tr_str = json.dumps(tr, ensure_ascii=True)
                if len(tr_str) > 300:
                    tr_str = tr_str[:297] + "..."
                _safe_print(f"  <- 结果: {tr_str}")

            if is_final:
                print()

        try:
            result_msgs = await agent_loop(
                messages=messages,
                tools=tools,
                system=_build_system_prompt(),
                llm_provider=llm,
                dispatch_fn=safe_dispatch or dispatch_fn,
                max_rounds=1000,
                progress_callback=_on_progress,
            )
            # 检查最后一条消息是否含 tool_calls（说明被 max_rounds 截断）
            if result_msgs and result_msgs[-1].get("tool_calls"):
                _safe_print("  [已达最大轮数，任务可能未完成。输入新指令可继续]")
        except LLMTokenLimitError:
            print("  [Token 预算已耗尽，正在清理历史并重置...]")
            messages.clear()
            llm.reset_budget()
            print("  [已重置，请继续输入]")
        except asyncio.TimeoutError:
            logger.warning("LLM API 调用超时，重试中...")
            print("  [LLM 超时，正在重试...]")
            try:
                result_msgs = await agent_loop(
                    messages=messages,
                    tools=tools,
                    system=_build_system_prompt(),
                    llm_provider=llm,
                    dispatch_fn=safe_dispatch or dispatch_fn,
                    max_rounds=1000,
                    progress_callback=_on_progress,
                )
                if result_msgs and result_msgs[-1].get("tool_calls"):
                    _safe_print("  [已达最大轮数，任务可能未完成。输入新指令可继续]")
            except Exception as retry_exc:
                import traceback
                logger.error("重试仍然失败: %s\n%s", retry_exc, traceback.format_exc())
                exc_type = type(retry_exc).__name__
                print(f"\n  [错误: {exc_type}] {retry_exc}\n")
        except Exception as exc:
            import traceback
            logger.error("交互循环异常: %s\n%s", exc, traceback.format_exc())
            exc_type = type(exc).__name__
            exc_msg = str(exc) or f"<{exc_type}: 无详细信息>"
            print(f"\n  [错误: {exc_msg}]\n")

        # 检查会话是否还存活（UiAutomator2 instrumentation 可能崩溃）
        if session_id and dispatch_fn and not await _check_session_alive(session_id):
            print("  [UiAutomator2 进程已崩溃，正在自动恢复会话...]")
            new_sid, new_dispatch_fn, vision_client = await _recover_session(
                session_id, vision_client=vision_client
            )
            session_id = new_sid
            dispatch_fn = new_dispatch_fn
            if session_id:
                print("  [会话已自动恢复，请继续操作]")
            else:
                print("  [会话恢复失败，将重新创建]")
                dispatch_fn = None

    # 清理
    if session_id:
        await _close_session(session_id)

    print("  Goodbye!")
