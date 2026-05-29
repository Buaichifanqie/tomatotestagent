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
from typing import TYPE_CHECKING

import httpx

from testagent.common.logging import get_logger

if TYPE_CHECKING:
    import asyncio as _asyncio

logger = get_logger(__name__)

_APPIUM_URL = "http://localhost:4723"
_appium_process: _asyncio.subprocess.Process | None = None


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
        async with httpx.AsyncClient(timeout=30) as client:
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
    """Ensure Appium server is running and can create sessions.

    If the existing Appium server is healthy, returns immediately.
    Otherwise kills the old process, starts a new one with ANDROID_HOME
    in its environment, and waits for it to be ready.
    """
    global _appium_process

    # Step 1: Check if existing Appium is healthy
    for _ in range(5):
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{_APPIUM_URL}/status")
            if resp.status_code == 200:
                test_sid = await _create_test_session()
                if test_sid:
                    await _close_test_session(test_sid)
                    logger.info("Existing Appium is healthy, session verified")
                    return True
                logger.warning("Appium server is up but session creation failed, will restart...")
                break
        except (httpx.RequestError, httpx.TimeoutException):
            pass
        await asyncio.sleep(1)

    # Step 2: Kill old Appium process
    logger.info("Existing Appium not available, restarting...")

    # Discard stale reference to process from a previous event loop
    _appium_process = None

    await _kill_process_on_port(4723)

    await _kill_process_on_port(4723)
    await asyncio.sleep(2)

    # Step 3: Start new Appium with ANDROID_HOME
    android_home = ensure_android_home()
    extra_env = {}
    if android_home:
        extra_env["ANDROID_HOME"] = android_home
        extra_env["ANDROID_SDK_ROOT"] = android_home

    appium_path = _find_appium()
    env = {**os.environ, **extra_env}

    if platform.system() == "Windows" and android_home:
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

    # Step 4: Wait for readiness
    for _ in range(30):
        await asyncio.sleep(1)
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                resp = await client.get(f"{_APPIUM_URL}/status")
            if resp.status_code == 200:
                test_sid = await _create_test_session()
                if test_sid:
                    await _close_test_session(test_sid)
                    logger.info("Appium started with ANDROID_HOME, session verified OK")
                    return True
                logger.warning("Appium server is up but session creation failed, waiting...")
        except httpx.RequestError:
            continue

    logger.error("Failed to start Appium or create test session")
    return False
