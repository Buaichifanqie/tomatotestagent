"""Shared Appium lifecycle management for CLI commands.

Ensures the Appium server is running with ANDROID_HOME / ANDROID_SDK_ROOT
set in its environment, which the UiAutomator2 driver requires regardless of
the ``appium:androidHome`` capability.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import tempfile
from dataclasses import dataclass
from typing import Optional

import httpx

from testagent.common.logging import get_logger

logger = get_logger(__name__)

_APPIUM_URL = "http://localhost:4723"


def ensure_android_home() -> str | None:
    """Auto-detect Android SDK path and set ANDROID_HOME / ANDROID_SDK_ROOT."""
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
            logger.debug("Auto-detected Android SDK", extra={"extra_data": {"path": path}})
            return path
    return None


def _find_appium() -> str:
    """Find the Appium executable path (handles Windows .cmd wrapper)."""
    resolved = shutil.which("appium")
    if resolved:
        return resolved
    if platform.system() == "Windows":
        npm_dir = os.path.join(os.environ.get("APPDATA", ""), "npm")
        for name in ("appium.cmd", "appium"):
            full = os.path.join(npm_dir, name)
            if os.path.isfile(full):
                return full
        npm_dir2 = os.path.join(os.environ.get("LOCALAPPDATA", ""), "npm")
        for name in ("appium.cmd", "appium"):
            full = os.path.join(npm_dir2, name)
            if os.path.isfile(full):
                return full
    return "appium"


async def _kill_process_on_port(port: int) -> None:
    """Kill any process listening on the given port."""
    pids: set[str] = set()

    if platform.system() == "Windows":
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


async def _create_test_session() -> str | None:
    """Create a short-lived Appium session to verify the server is healthy."""
    android_home = ensure_android_home()
    always_match: dict[str, object] = {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:deviceName": "emulator-5554",
        "appium:udid": "emulator-5554",
        "appium:noReset": True,
        "appium:autoGrantPermissions": True,
        "appium:newCommandTimeout": 120,
        "appium:allowInsecure": "*:adb_shell",
    }
    if android_home:
        always_match["appium:androidHome"] = android_home

    capabilities = {"capabilities": {"alwaysMatch": always_match, "firstMatch": [{}]}}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{_APPIUM_URL}/session", json=capabilities)
            if resp.status_code == 200:
                data = resp.json()
                sid = data.get("value", {}).get("sessionId") or data.get("sessionId")
                if sid:
                    return sid
            body = resp.text[:200]
            logger.warning(
                "Test session creation failed",
                extra={"extra_data": {"status": resp.status_code, "body": body}},
            )
    except Exception as exc:
        logger.warning("Test session creation error", extra={"extra_data": {"error": str(exc)}})
    return None


async def _close_test_session(sid: str) -> None:
    """Close a test session."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.delete(f"{_APPIUM_URL}/session/{sid}")
    except Exception:
        pass


async def ensure_appium_running() -> bool:
    """Legacy wrapper — delegates to AppiumManager singleton on port 4723."""
    global _appium_manager
    try:
        await _appium_manager.ensure_appium_running(
            udid="emulator-5554",
            port=4723,
        )
        return True
    except RuntimeError:
        return False


@dataclass
class AppiumInstance:
    port: int
    process: asyncio.subprocess.Process
    log_path: str
    url: str


class AppiumManager:
    """Manage multiple Appium server instances, one per device."""

    def __init__(self) -> None:
        self._instances: dict[str, AppiumInstance] = {}

    async def ensure_appium_running(self, udid: str, port: int, log_path: str = "") -> AppiumInstance:
        """Start Appium server for *udid* on *port* (or return existing one)."""
        if udid in self._instances:
            inst = self._instances[udid]
            if await self._is_healthy(inst.url):
                return inst
            await self._stop_instance(udid)

        if not log_path:
            log_path = os.path.join(tempfile.gettempdir(), f"appium_{udid.replace(':', '_')}.log")

        # Use the existing module-level _kill_process_on_port
        await _kill_process_on_port(port)
        await asyncio.sleep(1)

        android_home = ensure_android_home()
        extra_env = {}
        if android_home:
            extra_env["ANDROID_HOME"] = android_home
            extra_env["ANDROID_SDK_ROOT"] = android_home

        appium_path = _find_appium()
        env = {**os.environ, **extra_env}

        log_fh = open(log_path, "a", encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            appium_path,
            "-p", str(port),
            "--allow-insecure", "*:adb_shell",
            stdout=log_fh,
            stderr=log_fh,
            env=env,
        )

        url = f"http://localhost:{port}"
        inst = AppiumInstance(port=port, process=proc, log_path=log_path, url=url)
        self._instances[udid] = inst

        # Wait for readiness (max ~30s)
        for _ in range(30):
            await asyncio.sleep(1)
            if await self._is_healthy(url):
                return inst

        raise RuntimeError(f"Appium server on port {port} did not start within 30s for device {udid}")

    async def stop(self, udid: str) -> None:
        """Stop Appium server for a single device."""
        await self._stop_instance(udid)

    async def stop_all(self) -> None:
        """Stop all running Appium servers."""
        for udid in list(self._instances.keys()):
            await self._stop_instance(udid)

    async def _stop_instance(self, udid: str) -> None:
        inst = self._instances.pop(udid, None)
        if inst is None:
            return
        try:
            inst.process.terminate()
            await asyncio.wait_for(inst.process.wait(), timeout=5)
        except Exception:
            try:
                inst.process.kill()
            except Exception:
                pass

    @staticmethod
    async def _is_healthy(url: str) -> bool:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{url}/status")
                return resp.status_code == 200
        except Exception:
            return False


# Global AppiumManager singleton used by the legacy ensure_appium_running wrapper
_appium_manager = AppiumManager()
