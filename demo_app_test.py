"""App测试演示脚本 - 使用TestAgent的Appium Server模块连接Android模拟器执行测试"""

import os
os.environ["PYTHONIOENCODING"] = "utf-8"

import asyncio
import json
import time

import httpx

APPIUM_URL = "http://localhost:4723"


async def create_session() -> str | None:
    """在模拟器上创建一个Appium Session"""
    capabilities = {
        "capabilities": {
            "alwaysMatch": {
                "platformName": "Android",
                "appium:automationName": "UiAutomator2",
                "appium:deviceName": "emulator-5554",
                "appium:udid": "emulator-5554",
                "appium:noReset": True,
                "appium:autoGrantPermissions": True,
                "appium:shouldTerminateApp": True,
                "appium:ensureWebviewsHavePages": False,
                "appium:nativeWebScreenshot": True,
                "appium:newCommandTimeout": 60,
                "appium:connectHardwareKeyboard": True,
            },
            "firstMatch": [{}],
        }
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        # 尝试W3C session创建
        response = await client.post(f"{APPIUM_URL}/session", json=capabilities)

        if response.status_code == 200:
            data = response.json()
            session_id = data.get("value", {}).get("sessionId") or data.get("sessionId")
            print(f"✅ Session created: {session_id}")
            return session_id
        else:
            print(f"❌ Session creation failed: {response.status_code} {response.text[:200]}")
            return None


async def get_device_info(session_id: str) -> dict:
    """获取设备信息"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
        response = await client.get(f"{APPIUM_URL}/session/{session_id}")
        if response.status_code == 200:
            return response.json()
        return {}


async def list_contexts(session_id: str) -> list:
    """列出可用上下文"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
        response = await client.get(f"{APPIUM_URL}/session/{session_id}/contexts")
        if response.status_code == 200:
            data = response.json()
            return data.get("value", [])
        return []


async def take_screenshot(session_id: str) -> str | None:
    """截取当前屏幕并保存"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
        response = await client.get(f"{APPIUM_URL}/session/{session_id}/screenshot")
        if response.status_code == 200:
            data = response.json()
            return data.get("value")
        return None


async def get_page_source(session_id: str) -> str:
    """获取当前页面XML源码"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
        response = await client.get(f"{APPIUM_URL}/session/{session_id}/source")
        if response.status_code == 200:
            data = response.json()
            source = data.get("value", "")
            return source[:2000]  # 截取前2000字符
        return f"Failed: {response.status_code}"


async def get_performance_data(session_id: str) -> dict:
    """获取性能数据"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
        # 获取电池信息
        try:
            response = await client.get(f"{APPIUM_URL}/session/{session_id}/appium/device/battery_info")
            if response.status_code == 200:
                data = response.json()
                return data.get("value", {})
        except Exception:
            pass
        return {}


async def delete_session(session_id: str) -> None:
    """关闭Session"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
        try:
            await client.delete(f"{APPIUM_URL}/session/{session_id}")
            print(f"✅ Session closed: {session_id}")
        except Exception as e:
            print(f"⚠️ Session close warning: {e}")


async def test_appium_health() -> bool:
    """检查Appium Server状态"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as client:
        try:
            response = await client.get(f"{APPIUM_URL}/status")
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Appium Server Status: OK")
                print(f"   版本: {data.get('value', {}).get('build', {}).get('version', 'unknown')}")
                return True
            return False
        except Exception as e:
            print(f"❌ Appium Server connection failed: {e}")
            return False


async def main():
    print("=" * 60)
    print("TestAgent - App测试演示")
    print("=" * 60)

    # 1. 检查Appium Server
    print("\n[1/5] 检查Appium Server状态...")
    healthy = await test_appium_health()
    if not healthy:
        print("❌ Appium Server未运行，请先启动 appium")
        return

    # 2. 创建Session
    print("\n[2/5] 创建Appium Session...")
    session_id = await create_session()
    if not session_id:
        print("❌ 无法创建Session")
        return

    try:
        # 3. 获取设备信息
        print("\n[3/5] 获取设备信息...")
        info = await get_device_info(session_id)
        caps = info.get("value", {}).get("capabilities", {}) or info.get("capabilities", {})
        print(f"   Device: {caps.get('deviceName', 'N/A')}")
        print(f"   Platform: {caps.get('platformName', 'N/A')} {caps.get('platformVersion', 'N/A')}")
        print(f"   Manufacturer: {caps.get('deviceManufacturer', 'N/A')}")
        print(f"   Model: {caps.get('deviceModel', 'N/A')}")
        print(f"   API Level: {caps.get('deviceApiLevel', 'N/A')}")

        # 4. 截屏
        print("\n[4/5] 截取屏幕...")
        screenshot_base64 = await take_screenshot(session_id)
        if screenshot_base64:
            import base64

            screenshot_bytes = base64.b64decode(screenshot_base64)
            screenshot_path = "app_screenshot.png"
            with open(screenshot_path, "wb") as f:
                f.write(screenshot_bytes)
            print(f"   ✅ 截图已保存: {screenshot_path} ({len(screenshot_bytes)} bytes)")
        else:
            print("   ⚠️ 截图失败")

        # 获取页面源码
        print("\n  获取页面XML源码...")
        source = await get_page_source(session_id)
        if source and not source.startswith("Failed"):
            print(f"   ✅ 页面源码获取成功 ({len(source)} chars)")
            # 保存到文件
            with open("app_page_source.xml", "w", encoding="utf-8") as f:
                f.write(source)
        else:
            print(f"   ⚠️ {source}")

        # 5. 测试结果汇总
        print("\n[5/5] 测试结果汇总")
        print("-" * 40)

        # 使用项目的Appium工具进行验证
        print("\n  使用TestAgent Appium MCP工具验证...")
        from testagent.mcp_servers.appium_server.tools import app_screenshot as mcp_screenshot

        result = await mcp_screenshot(appium_url=APPIUM_URL)
        if "screenshot_base64" in result:
            print(f"   ✅ TestAgent MCP截图工具正常: 包含截图数据")
        else:
            print(f"   ⚠️ MCP截图工具返回: {result}")

    finally:
        # 清理
        await delete_session(session_id)

    print("\n" + "=" * 60)
    print("🎉 App测试演示完成!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
