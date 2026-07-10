# testagent/platform/ios_platform.py
from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path
from typing import Any

from testagent.platform.interface import AbstractPlatform, BaseRecorder, SessionInfo


class iOSRecorder(BaseRecorder):
    """iOS screen recorder using Appium mobile: startRecordingScreen API.

    iOS does not have adb screenrecord. Uses the standard Appium recording
    API which is available for both platforms but is the primary (only) option
    for iOS.
    """

    def __init__(self, output_dir: str, tc_id: str, device_udid: str = "") -> None:
        from pathlib import Path as _Path
        super().__init__(output_dir, tc_id, device_udid)
        self._output_path = _Path(output_dir) / "recordings" / tc_id
        self._segment_paths: list[str] = []
        self._session_manager: Any = None
        self._is_recording = False

    def set_session_manager(self, sm: Any) -> None:
        self._session_manager = sm

    async def start(self) -> bool:
        self._output_path.mkdir(parents=True, exist_ok=True)
        if not self._session_manager:
            return False
        from testagent.mcp_servers.appium_server.tools import app_start_recording
        try:
            result = await app_start_recording(
                appium_url=self._session_manager.appium_url,
                session_id=self._session_manager.session_id,
            )
            self._is_recording = not result.get("error")
            return self._is_recording
        except Exception:
            return False

    async def stop(self) -> None:
        if not self._is_recording or not self._session_manager:
            return
        from testagent.mcp_servers.appium_server.tools import app_stop_recording
        try:
            result = await app_stop_recording(
                appium_url=self._session_manager.appium_url,
                session_id=self._session_manager.session_id,
            )
            video_b64 = result.get("video_base64", "")
            if video_b64:
                self._output_path.mkdir(parents=True, exist_ok=True)
                seg_path = self._output_path / "recording.mp4"
                seg_path.write_bytes(base64.b64decode(video_b64))
                self._segment_paths.append(str(seg_path))
        except Exception:
            pass
        finally:
            self._is_recording = False

    async def check_and_split(self) -> None:
        # iOS recording is single-shot, no segmentation needed
        pass

    def get_segments(self) -> list[str]:
        return list(self._segment_paths)


class iOSPlatform(AbstractPlatform):

    @property
    def platform_name(self) -> str:
        return "iOS"

    @property
    def automation_name(self) -> str:
        return "XCUITest"

    def build_capabilities(self, udid: str = "", **kwargs: Any) -> dict[str, Any]:
        caps: dict[str, Any] = {
            "platformName": "iOS",
            "appium:automationName": "XCUITest",
            "appium:deviceName": udid or "iPhone",
            "appium:udid": udid,
            "appium:noReset": True,
            "appium:autoAcceptAlerts": True,
            "appium:newCommandTimeout": 300,
            "appium:usePrebuiltWDA": True,
        }
        wda_port = kwargs.get("wda_local_port", 8100)
        if wda_port:
            caps["appium:wdaLocalPort"] = wda_port
        return caps

    async def launch_app(self, app_id: str, session_info: SessionInfo) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import _appium_post
        payload: dict[str, object] = {
            "script": "mobile: launchApp",
            "args": [{"bundleId": app_id}],
        }
        return await _appium_post(
            session_info.appium_url,
            "/session/:sessionId/execute/sync",
            payload,
            session_id=session_info.session_id,
        )

    async def teardown_app(self, app_id: str, session_info: SessionInfo) -> None:
        from testagent.mcp_servers.appium_server.tools import _appium_post
        payload: dict[str, object] = {
            "script": "mobile: terminateApp",
            "args": [{"bundleId": app_id}],
        }
        await _appium_post(
            session_info.appium_url,
            "/session/:sessionId/execute/sync",
            payload,
            session_id=session_info.session_id,
        )

    async def list_connected_devices(self) -> list[dict[str, str]]:
        devices: list[dict[str, str]] = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "xcrun", "xctrace", "list", "devices",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            for line in stdout.decode().splitlines():
                line = line.strip()
                if not line or "=" in line:
                    continue
                # "iPhone 15 Pro (00008110-xxx) [simulator]" etc.
                m = re.search(r"\(([^)]+)\)", line)
                if m:
                    udid = m.group(1)
                    name = line.split("(")[0].strip()
                    status = "simulator" if "simulator" in line.lower() else "device"
                    devices.append({"udid": udid, "name": name, "status": status})
        except Exception:
            pass
        return devices

    async def detect_installed_apps(self, udid: str) -> list[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ios-deploy", "--detect", "--udid", udid,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            lines = stdout.decode().strip().splitlines()
            if lines:
                return [l.strip() for l in lines if l.strip()]
        except Exception:
            pass
        return []

    async def detect_app_version(self, app_id: str, udid: str) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ideviceinstaller", "-u", udid, "-l", "-o", "xml",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            output = stdout.decode()
            if app_id in output:
                import plistlib
                try:
                    data = plistlib.loads(stdout)
                    for app in data:
                        if app.get("CFBundleIdentifier") == app_id:
                            return app.get("CFBundleShortVersionString") or app.get("CFBundleVersion")
                except Exception:
                    pass
        except Exception:
            pass
        return None

    async def go_back(self, session_info: SessionInfo) -> bool:
        from testagent.mcp_servers.appium_server.tools import _appium_post
        try:
            result = await _appium_post(
                session_info.appium_url,
                "/session/:sessionId/back",
                {},
                session_id=session_info.session_id,
            )
            if not result.get("error"):
                await asyncio.sleep(1)
                return True
        except Exception:
            pass
        return False

    async def press_keyboard_done(self, session_info: SessionInfo) -> bool:
        from testagent.mcp_servers.appium_server.tools import _appium_post, _find_element
        try:
            find_result = await _find_element(
                session_info.appium_url, "ios_predicate",
                "type == 'XCUIElementTypeButton' AND label IN {'Done', 'Search', 'Go', 'return'}",
                timeout=5, session_id=session_info.session_id,
            )
            if find_result.get("status_code") == 200:
                body = find_result["body"]
                el_id = body.get("ELEMENT") or body.get("elementId")
                if not el_id:
                    value = body.get("value", {})
                    if isinstance(value, dict):
                        el_id = value.get("ELEMENT") or value.get("elementId")
                if el_id:
                    await _appium_post(
                        session_info.appium_url,
                        f"/session/:sessionId/element/{el_id}/click",
                        {}, session_id=session_info.session_id,
                    )
                    await asyncio.sleep(0.5)
                    return True
        except Exception:
            pass
        return False

    async def get_screen_size(self, session_info: SessionInfo) -> tuple[int, int]:
        from testagent.mcp_servers.appium_server.tools import _appium_get
        try:
            result = await _appium_get(
                session_info.appium_url,
                "/session/:sessionId/window/rect",
                session_id=session_info.session_id,
            )
            if result["status_code"] == 200:
                value = result["body"].get("value", {})
                if isinstance(value, dict):
                    w = int(value.get("width", 390))
                    h = int(value.get("height", 844))
                    return w, h
        except Exception:
            pass
        return 390, 844  # iPhone 14 default

    def get_find_element_strategies(self) -> list[str]:
        return ["accessibility_id", "ios_predicate", "ios_class_chain", "xpath"]

    def get_default_selector_strategy(self) -> str:
        return "ios_predicate"

    def create_recorder(
        self,
        output_dir: str,
        tc_id: str,
        device_udid: str = "",
        session_manager: Any = None,
    ) -> BaseRecorder:
        rec = iOSRecorder(output_dir, tc_id, device_udid)
        if session_manager:
            rec.set_session_manager(session_manager)
        return rec

    def get_appium_args(self) -> list[str]:
        return []
