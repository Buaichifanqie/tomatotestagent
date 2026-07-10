# testagent/platform/android_platform.py
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from testagent.common.adb_utils import adb_command
from testagent.platform.interface import AbstractPlatform, BaseRecorder, SessionInfo


class AndroidRecorder(BaseRecorder):
    """Android screen recorder using adb shell screenrecord (180s segments).

    Ported from testagent/plan/segmented_recorder.py with no functional changes.
    """

    _SEGMENT_LIMIT_S = 180
    _MIN_FILE_BYTES = 1024

    def __init__(self, output_dir: str, tc_id: str, device_udid: str = "") -> None:
        from pathlib import Path as _Path
        super().__init__(output_dir, tc_id, device_udid)
        self._output_path = _Path(output_dir) / "recordings" / tc_id
        self._segment_paths: list[str] = []
        self._device_path: str = ""
        self._local_path: str = ""
        self._segment_counter = 0
        self._adb_process: asyncio.subprocess.Process | None = None
        self._is_recording = False

    def _adb_cmd(self, *args: str) -> list[str]:
        if self._device_udid:
            return ["adb", "-s", self._device_udid, *args]
        return ["adb", *args]

    async def start(self) -> bool:
        self._output_path.mkdir(parents=True, exist_ok=True)
        return await self._start_segment()

    async def stop(self) -> None:
        if not self._is_recording:
            return
        await self._stop_current_segment()

    async def check_and_split(self) -> None:
        if not self._is_recording or self._adb_process is None:
            return
        if self._adb_process.returncode is not None:
            await self._pull_current_segment()
            await self._start_segment()

    def get_segments(self) -> list[str]:
        return list(self._segment_paths)

    async def _start_segment(self) -> bool:
        self._segment_counter += 1
        self._device_path = f"/sdcard/{self._tc_id}_seg{self._segment_counter:03d}.mp4"
        self._local_path = str(self._output_path / f"seg{self._segment_counter:03d}.mp4")
        try:
            self._adb_process = await asyncio.create_subprocess_exec(
                *self._adb_cmd("shell", "screenrecord",
                               "--time-limit", str(self._SEGMENT_LIMIT_S),
                               "--bit-rate", "2000000", self._device_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._is_recording = True
            await asyncio.sleep(1)
            return True
        except Exception:
            self._is_recording = False
            self._adb_process = None
            return False

    async def _stop_current_segment(self) -> None:
        if not self._is_recording or self._adb_process is None:
            return
        try:
            kill_proc = await asyncio.create_subprocess_exec(
                *self._adb_cmd("shell", "pkill", "-2", "screenrecord"),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await kill_proc.wait()
        except Exception:
            pass
        try:
            await asyncio.wait_for(self._adb_process.wait(), timeout=15)
        except asyncio.TimeoutError:
            self._adb_process.kill()
            await self._adb_process.wait()
        self._adb_process = None
        await self._pull_current_segment()

    async def _pull_current_segment(self) -> None:
        if not self._device_path or not self._local_path:
            return
        try:
            pull = await asyncio.create_subprocess_exec(
                *self._adb_cmd("pull", self._device_path, self._local_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await pull.wait()
            local = Path(self._local_path)
            if local.exists() and local.stat().st_size >= self._MIN_FILE_BYTES:
                self._segment_paths.append(self._local_path)
            else:
                local.unlink(missing_ok=True)
            try:
                rm = await asyncio.create_subprocess_exec(
                    *self._adb_cmd("shell", "rm", "-f", self._device_path),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await rm.wait()
            except Exception:
                pass
        except Exception:
            pass
        finally:
            self._is_recording = False
            self._adb_process = None
            self._device_path = ""
            self._local_path = ""


class AndroidPlatform(AbstractPlatform):

    @property
    def platform_name(self) -> str:
        return "Android"

    @property
    def automation_name(self) -> str:
        return "UiAutomator2"

    def build_capabilities(self, udid: str = "", **kwargs: Any) -> dict[str, Any]:
        caps: dict[str, Any] = {
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:deviceName": udid or "emulator-5554",
            "appium:udid": udid or "emulator-5554",
            "appium:noReset": True,
            "appium:autoGrantPermissions": True,
            "appium:newCommandTimeout": 300,
            "appium:allowInsecure": "*:adb_shell",
        }
        system_port = kwargs.get("system_port", 8200)
        caps["appium:systemPort"] = system_port
        android_home = self._detect_android_home()
        if android_home:
            caps["appium:androidHome"] = android_home
        return caps

    def _detect_android_home(self) -> str | None:
        from testagent.common.appium_manager import ensure_android_home
        return ensure_android_home()

    async def launch_app(self, app_id: str, session_info: SessionInfo) -> dict[str, Any]:
        from testagent.mcp_servers.appium_server.tools import app_launch as _app_launch
        return await _app_launch(
            package=app_id,
            appium_url=session_info.appium_url,
            session_id=session_info.session_id,
        )

    async def teardown_app(self, app_id: str, session_info: SessionInfo) -> None:
        adb_command(session_info.device_udid, "shell", "am", "force-stop", app_id,
                     capture_output=True, timeout=10)
        for cmd in ("svc wifi enable", "svc data enable"):
            try:
                adb_command(session_info.device_udid, "shell", cmd,
                             capture_output=True, timeout=10)
            except Exception:
                pass

    async def list_connected_devices(self) -> list[dict[str, str]]:
        result = adb_command("", "devices", capture_output=True, text=True, timeout=5)
        devices: list[dict[str, str]] = []
        for line in result.stdout.splitlines():
            if "device" not in line or "devices" in line:
                continue
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            devices.append({"udid": parts[0], "name": parts[0], "status": parts[1]})
        return devices

    async def detect_installed_apps(self, udid: str) -> list[str]:
        result = adb_command(udid, "shell", "pm", "list", "packages", "-3",
                              capture_output=True, text=True, timeout=10)
        return [line.replace("package:", "").strip()
                for line in result.stdout.split("\n") if line.startswith("package:")]

    async def detect_app_version(self, app_id: str, udid: str) -> str | None:
        try:
            result = adb_command(udid, "shell", "dumpsys", "package", app_id,
                                  capture_output=True, text=True, timeout=15)
            for line in result.stdout.split("\n"):
                stripped = line.strip()
                if stripped.startswith("versionName="):
                    version = stripped.split("=", 1)[1].strip()
                    if version:
                        return version
        except Exception:
            pass
        return None

    async def go_back(self, session_info: SessionInfo) -> bool:
        from testagent.mcp_servers.appium_server.tools import app_exec
        try:
            result = await app_exec(
                command="input keyevent KEYCODE_BACK",
                appium_url=session_info.appium_url,
                session_id=session_info.session_id,
            )
            if not result.get("error"):
                await asyncio.sleep(1)
                return True
        except Exception:
            pass
        return False

    async def press_keyboard_done(self, session_info: SessionInfo) -> bool:
        try:
            adb_command(session_info.device_udid, "shell", "input", "keyevent",
                         "KEYCODE_ENTER", capture_output=True, text=True, timeout=10)
            await asyncio.sleep(1)
            return True
        except Exception:
            return False

    async def get_screen_size(self, session_info: SessionInfo) -> tuple[int, int]:
        from testagent.mcp_servers.appium_server.tools import app_exec
        try:
            result = await app_exec(command="wm size",
                                     appium_url=session_info.appium_url,
                                     session_id=session_info.session_id)
            value = str(result.get("body", {}).get("value", ""))
            m = re.search(r"(\d+)x(\d+)", value)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
            else:
                w, h = 1080, 2400
            rot_result = await app_exec(
                command="dumpsys display | grep 'mCurrentOrientation'",
                appium_url=session_info.appium_url,
                session_id=session_info.session_id,
            )
            rot_value = str(rot_result.get("body", {}).get("value", ""))
            rot_match = re.search(r"mCurrentOrientation[=:]\s*(\d+)", rot_value)
            orientation = int(rot_match.group(1)) if rot_match else 0
            if orientation in (1, 3):
                w, h = h, w
            return w, h
        except Exception:
            return 1080, 2400

    def get_find_element_strategies(self) -> list[str]:
        return ["accessibility_id", "uiautomator", "xpath"]

    def get_default_selector_strategy(self) -> str:
        return "uiautomator"

    def create_recorder(
        self,
        output_dir: str,
        tc_id: str,
        device_udid: str = "",
        session_manager: Any = None,
    ) -> BaseRecorder:
        return AndroidRecorder(output_dir, tc_id, device_udid)

    def get_appium_args(self) -> list[str]:
        return ["--allow-insecure", "*:adb_shell"]
